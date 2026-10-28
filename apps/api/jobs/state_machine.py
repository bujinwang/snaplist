"""The `Job` state machine from `docs/CONTRACTS.md` §3, in one readable table.

    queued ──claim──▶ running ──ok──▶ done
                         │
                         ├─retryable (attempts < 3)──▶ queued
                         └─terminal──▶ failed(reason_code)

Everything not in `LEGAL_TRANSITIONS` is refused with `IllegalJobTransition`. That includes
re-opening a terminal job: CONTRACTS.md says a re-render creates a **new** job row, so
`done -> running` is not a retry, it is a bug, and it fails loudly.

Who calls this
--------------
* the pytest suite (proves the table, the retry budget and the refund)
* the Modal worker in production (E5-T3), which is the only process allowed to move a job

The API routers never call it. `GET /v1/jobs/{id}` reads only — a seller who could move a job
could set `state=failed` and farm refunds.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from apps.api import usage as usage_module
from apps.api.repository import JobRecord, Repository, utcnow
from apps.api.schemas import JobState, ReasonCode

# The whole machine. A terminal state has no outgoing edge, which is what "terminal" means.
LEGAL_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING}),
    JobState.RUNNING: frozenset({JobState.DONE, JobState.FAILED, JobState.QUEUED}),
    JobState.DONE: frozenset(),
    JobState.FAILED: frozenset(),
}

MAX_ATTEMPTS = 3


class IllegalJobTransition(Exception):
    """A transition the contract forbids. 409 at the API boundary, an exception internally."""

    def __init__(
        self, job_id: UUID, from_state: JobState, to_state: JobState, detail: str = ""
    ) -> None:
        allowed = sorted(state.value for state in LEGAL_TRANSITIONS[from_state])
        message = (
            f"job {job_id} cannot move {from_state.value} -> {to_state.value}; "
            f"legal from {from_state.value}: {allowed or 'nothing (terminal)'}"
        )
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.job_id = job_id
        self.from_state = from_state
        self.to_state = to_state
        self.detail = detail


def _check_progress(progress: float | None) -> None:
    if progress is None:
        return
    if not 0.0 <= progress <= 1.0:
        raise ValueError(f"progress must be within 0..1, got {progress!r}")


def apply_transition(
    repository: Repository,
    job: JobRecord,
    *,
    to_state: JobState,
    reason_code: ReasonCode | None = None,
    progress: float | None = None,
    error_detail: str | None = None,
    claimed_by: str | None = None,
    now: datetime | None = None,
) -> JobRecord:
    """Move one job one step, or refuse. Side effects match CONTRACTS.md §3's table."""
    if to_state not in LEGAL_TRANSITIONS[job.state]:
        raise IllegalJobTransition(job.id, job.state, to_state)
    _check_progress(progress)
    moment = now or utcnow()

    attempts = job.attempts
    started_at = job.started_at
    finished_at = job.finished_at
    resolved_reason = job.reason_code
    claimed = job.claimed_by

    if to_state is JobState.RUNNING:
        # clause: `queued -> running` is the claim. started_at records the first claim;
        # later claims do not overwrite it, so the latency report keeps the real start.
        started_at = started_at or moment
        claimed = claimed_by or claimed
        resolved_reason = None

    elif to_state is JobState.DONE:
        # invariant 1: done => reason_code is null
        resolved_reason = None
        finished_at = moment
        progress = 1.0

    elif to_state is JobState.FAILED:
        # invariant 2: failed => reason_code is not null
        if reason_code is None:
            raise ValueError("a job may only be failed with a reason_code")
        resolved_reason = reason_code
        finished_at = moment
        if progress is None:
            progress = job.progress

    elif to_state is JobState.QUEUED:  # the retry edge, running -> queued
        attempts = job.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            # invariant 3: attempts == 3 => terminal. Refuse instead of writing a row the
            # database will reject anyway.
            raise IllegalJobTransition(
                job.id,
                job.state,
                to_state,
                detail=f"retry budget exhausted after {job.attempts} attempts; fail the job",
            )
        claimed = None
        progress = 0.0
        resolved_reason = None

    updated = JobRecord(
        id=job.id,
        item_id=job.item_id,
        user_id=job.user_id,
        kind=job.kind,
        state=to_state,
        attempts=attempts,
        progress=job.progress if progress is None else progress,
        created_at=job.created_at,
        updated_at=moment,
        reason_code=resolved_reason,
        error_detail=error_detail if error_detail is not None else job.error_detail,
        claimed_by=claimed,
        started_at=started_at,
        finished_at=finished_at,
    )

    with repository.transaction():
        repository.update_job(updated)
        if to_state is JobState.FAILED:
            # invariant 4: a failed job refunds exactly one unit, idempotent by job_id
            usage_module.refund_one(repository, job.user_id, job_id=job.id, now=moment)
    return updated


def fail(
    repository: Repository,
    job: JobRecord,
    *,
    reason_code: ReasonCode,
    error_detail: str | None = None,
    now: datetime | None = None,
) -> JobRecord:
    """Terminal failure. The only path that refunds quota."""
    return apply_transition(
        repository,
        job,
        to_state=JobState.FAILED,
        reason_code=reason_code,
        error_detail=error_detail,
        now=now,
    )


def retry_or_fail(
    repository: Repository,
    job: JobRecord,
    *,
    reason_code: ReasonCode,
    retryable: bool,
    error_detail: str | None = None,
    now: datetime | None = None,
) -> JobRecord:
    """CONTRACTS.md §3: retryable *and* attempts < 3 re-queues; anything else terminates.

    A job is claimed at most `MAX_ATTEMPTS` times. `attempts` counts retries already spent,
    so the third claim must terminate: `attempts + 1 >= 3`.
    """
    if retryable and job.attempts + 1 < MAX_ATTEMPTS:
        return apply_transition(
            repository,
            job,
            to_state=JobState.QUEUED,
            error_detail=error_detail,
            now=now,
        )
    return fail(repository, job, reason_code=reason_code, error_detail=error_detail, now=now)
