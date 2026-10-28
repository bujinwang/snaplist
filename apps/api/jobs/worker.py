"""E5-T3 — the job runner: claims queued jobs, runs them, and terminates them honestly.

What exists before this module
------------------------------
The state machine (`apps/api/jobs/state_machine.py`) owns every legal transition and the
refund side effect; the ledger (`apps/api/usage.py`) spends at enqueue and refunds at
terminal failure. What was missing is the *process* that moves a job — so every job this
build enqueues sat in `queued` forever.

What this worker does
---------------------
    claim (queued -> running, inside one write transaction)
      -> run the kind's handler
           ok        -> running -> done        (progress 1.0, finished_at set)
           transient -> retry_or_fail          (back into queued, attempts += 1, or
                                                  terminal `failed` once the budget of 3
                                                  claims is spent — then the refund fires)
           stage does not exist yet
                     -> terminal `failed(INTERNAL)` immediately, error_detail naming the
                        PLAN ticket. Burning the retry budget on a deterministic stub would
                        be theatre.

Single-claim guarantee, stated without pretending: the claim is *peek inside a write
transaction, then the state machine's transition*. SQLite serializes writers with
`begin immediate`, which is honest for this build (one process, one connection). The
production claim is `for update skip locked` — the query is documented in
`infra/migrations/0001_init.sql` and is what the Modal worker will run.

Heartbeat: `heartbeat_at` and the running-queue sweeper are Postgres/Modal mechanics
(the columns and index already exist in 0001_init.sql). The honest local equivalent is
`recover_interrupted()`, run at process start: a job found in `running` was claimed by a
process that died, so it is re-queued with `attempts += 1` (CONTRACTS §3 treats a lost
heartbeat as a retryable failure) — or failed terminally if the retry budget is spent.
A job interrupted this way can never surface as `done` with missing assets, because `done`
is only ever written by the same transition that observed the handler return.

Handlers are the seam: `JobWorker(handlers={...})` is where the real E1/E2 stages plug in.
Every built-in handler raises `PipelineUnavailable` until its ticket lands — the worker
does not invent work, and every terminal row keeps `provider='stub'`, which is the truth
about this build.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from apps.api.jobs.state_machine import (
    IllegalJobTransition,
    apply_transition,
    fail,
    retry_or_fail,
)
from apps.api.repository import JobRecord, Repository, build_repository
from apps.api.schemas import JobKind, JobState, ReasonCode
from packages.vision import StageNotImplemented

Handler = Callable[[JobRecord, Repository], None]

# Which stage each job kind waits on, so the error_detail names the ticket that unblocks
# the job instead of a bare "not implemented". Kept in this file because the worker is the
# only caller that needs the kind -> ticket mapping.
UNIMPLEMENTED_STAGE: dict[JobKind, tuple[str, str]] = {
    JobKind.FULL: ("segment (first stage of the full pipeline)", "E1-T2"),
    JobKind.PRETTIFY: ("rectify + prettify", "E2-T1/E2-T2"),
    JobKind.ATTRIBUTES: ("attrs (VLM)", "E3-T1"),
    JobKind.VTON: ("vton adapter", "E4-T1"),
    # The synchronous export path (POST /v1/exports) runs today; executing kind=export
    # asynchronously needs a marketplace (and result storage) on the job row first.
    JobKind.EXPORT: ("export job execution", "E7/E5-T2"),
}


class PipelineUnavailable(RuntimeError):
    """A stage this job needs does not exist yet.

    Deliberately NOT a retryable condition: the code will not write itself between
    attempt 1 and attempt 2, and three identical failures plus a refund would only
    launder a stub through the retry budget.
    """


class TransientJobError(RuntimeError):
    """A failure a retry could plausibly fix (provider timeout, lock contention, ...).

    Raising this from a handler is how a test (or a real stage) asks for the retry path.
    """


def default_handler(job: JobRecord, repository: Repository) -> None:
    """Every built-in handler until the owning ticket lands. Refuses to invent work."""
    del repository  # unused: there is genuinely nothing this handler can produce
    stage, ticket = UNIMPLEMENTED_STAGE[job.kind]
    raise PipelineUnavailable(
        f"kind={job.kind.value} cannot run: {stage} is not implemented "
        f"(docs/PLAN.md ticket {ticket}); photo bytes are not persisted yet either "
        "(signed upload is still 501, E5-T4)"
    )


DEFAULT_HANDLERS: dict[JobKind, Handler] = {kind: default_handler for kind in JobKind}

_ERROR_DETAIL_LIMIT = 500


def _detail(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}" if not isinstance(exc, PipelineUnavailable) else str(exc)
    return text[:_ERROR_DETAIL_LIMIT]


@dataclass(slots=True)
class WorkerReport:
    """What one worker process did. Numbers, not adjectives."""

    processed: int
    recovered: int


class JobWorker:
    """Claims queued jobs and drives them through the state machine to a terminal state."""

    def __init__(
        self,
        repository: Repository,
        *,
        handlers: Mapping[JobKind, Handler] | None = None,
        claimed_by: str = "snaplist-worker",
    ) -> None:
        self.repository = repository
        self.handlers: dict[JobKind, Handler] = dict(DEFAULT_HANDLERS)
        if handlers:
            self.handlers.update(handlers)
        self.claimed_by = claimed_by

    # -- claim --------------------------------------------------------------------------
    def claim_next(self) -> JobRecord | None:
        """Claim the oldest queued job, or return None. Atomic with respect to workers.

        The peek and the transition share one `begin immediate` transaction, so a second
        worker (in this process) cannot claim the same row: by the time it peeks, the row
        is already `running`.
        """
        with self.repository.transaction():
            job = self.repository.peek_next_queued_job()
            if job is None:
                return None
            return apply_transition(
                self.repository,
                job,
                to_state=JobState.RUNNING,
                claimed_by=self.claimed_by,
            )

    # -- run ----------------------------------------------------------------------------
    def run_once(self) -> bool:
        """Claim and execute one job. Returns False when the queue was empty.

        The exception mapping is the whole policy of this worker:
          * PipelineUnavailable / StageNotImplemented -> terminal now (deterministic);
          * anything else -> retryable, the attempts budget decides (CONTRACTS §4 lists
            `internal` as "maybe" retryable — unknown is treated as transient until
            proven otherwise, and the budget bounds the damage).
        """
        job = self.claim_next()
        if job is None:
            return False
        handler = self.handlers[job.kind]
        try:
            handler(job, self.repository)
        except (PipelineUnavailable, StageNotImplemented) as exc:
            retry_or_fail(
                self.repository,
                job,
                reason_code=ReasonCode.INTERNAL,
                retryable=False,
                error_detail=_detail(exc),
            )
        except Exception as exc:  # noqa: BLE001 - the boundary where jobs become data
            retry_or_fail(
                self.repository,
                job,
                reason_code=ReasonCode.INTERNAL,
                retryable=True,
                error_detail=_detail(exc),
            )
        else:
            apply_transition(self.repository, job, to_state=JobState.DONE)
        return True

    def run_pending(self, *, limit: int | None = None) -> int:
        """Drain the queue. `limit` caps the number of jobs this call may claim."""
        processed = 0
        while limit is None or processed < limit:
            if not self.run_once():
                break
            processed += 1
        return processed

    # -- crash recovery -------------------------------------------------------------------
    def recover_interrupted(self) -> int:
        """Re-queue jobs found in `running` at process start. Returns how many.

        A job in `running` at startup was claimed by a worker that died before it could
        finish or fail. CONTRACTS §3: lost heartbeat == retryable failure, attempts += 1.
        If the machine refuses the requeue (budget spent), the job is failed terminally —
        stuck-forever is not a state this worker is allowed to leave behind.
        """
        recovered = 0
        with self.repository.transaction():
            for job in self.repository.list_jobs_by_state(JobState.RUNNING):
                try:
                    apply_transition(
                        self.repository,
                        job,
                        to_state=JobState.QUEUED,
                        error_detail="worker restart: the job was claimed when the process died",
                    )
                except IllegalJobTransition:
                    fail(
                        self.repository,
                        job,
                        reason_code=ReasonCode.INTERNAL,
                        error_detail=(
                            "worker died after the retry budget was spent; "
                            "terminal failure instead of running forever"
                        ),
                    )
                recovered += 1
        return recovered


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m apps.api.jobs.worker [--database PATH] [--limit N]`.

    The startup order is always recover-then-drain: anything the previous process left
    in `running` is re-queued before new claims happen, so a restart can never strand work.
    """
    parser = argparse.ArgumentParser(
        prog="python -m apps.api.jobs.worker", description="SnapList local job runner"
    )
    parser.add_argument(
        "--database",
        default=None,
        help="sqlite file (default: SNAPLIST_DB env or :memory: — see build_repository)",
    )
    parser.add_argument("--limit", type=int, default=None, help="max jobs to claim this run")
    parser.add_argument("--claimed-by", default="snaplist-worker")
    args = parser.parse_args(argv)

    repository = build_repository(args.database)
    try:
        worker = JobWorker(repository, claimed_by=args.claimed_by)
        recovered = worker.recover_interrupted()
        processed = worker.run_pending(limit=args.limit)
        print(f"recovered {recovered} interrupted job(s); processed {processed} job(s)")
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
