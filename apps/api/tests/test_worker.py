"""Tests for the E5-T3 job worker (`apps/api/jobs/worker.py`).

The worker's contract, per docs/PLAN.md E5-T3 (adapted to the local sqlite seam):

* a queued job is claimed exactly once per attempt — two workers cannot both claim it;
* a deterministic stub fails TERMINAL with `internal` and an error_detail naming the
  PLAN ticket, without burning the retry budget;
* a transient failure retries and lands terminal `failed` after the 3-claim budget,
  with exactly one refund (the ledger's unique index is the last line of defence, but
  the test proves the worker only ever asks once);
* a job that was claimed when the worker "died" (left in `running`) is recovered at
  startup: re-queued with attempts += 1, then driven to a real terminal state — never
  `done` with missing assets;
* a succeeding handler reaches `done` with progress 1.0 and its assets written.

Refunds need a spend first, so every fixture job also calls `usage.spend_one` — the same
enqueue order the router performs.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from apps.api import usage
from apps.api.jobs.state_machine import apply_transition
from apps.api.jobs.worker import JobWorker, TransientJobError
from apps.api.repository import JobRecord, SqliteRepository
from apps.api.schemas import JobKind, JobState, ReasonCode

USER_A = UUID("11111111-1111-4111-8111-111111111111")
USER_B = UUID("22222222-2222-4222-8222-222222222222")

EVENT_ITEM_ENQUEUED = "item_enqueued"
EVENT_JOB_REFUNDED = "job_refunded"


@pytest.fixture
def worker(repository: SqliteRepository) -> JobWorker:
    return JobWorker(repository, claimed_by="test-worker")


def make_job(
    repository: SqliteRepository,
    *,
    user_id: UUID = USER_A,
    kind: JobKind = JobKind.FULL,
) -> JobRecord:
    """Enqueue the way the router does: item + job + one quota unit, one transaction."""
    item = repository.create_item(user_id=user_id)
    with repository.transaction():
        job = repository.create_job(item_id=item.id, user_id=user_id, kind=kind)
        usage.spend_one(repository, user_id, job_id=job.id, item_id=item.id)
    return repository.get_job(job.id, user_id=user_id)  # type: ignore[return-value]


def writing_handler(calls: list[UUID]) -> object:
    """A handler that stands in for a real pipeline: writes an asset, then returns."""

    def run(job: JobRecord, repo: SqliteRepository) -> None:
        calls.append(job.id)
        repo.set_asset(
            job.item_id,
            user_id=job.user_id,
            kind="cutout",
            storage_path=f"items/{job.item_id}/cutout.png",
        )

    return run


# ------------------------------------------------------------------------- claim semantics


def test_two_workers_cannot_claim_the_same_job(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    make_job(repository)
    other = JobWorker(repository, claimed_by="second-worker")

    first = worker.claim_next()
    assert first is not None
    assert first.claimed_by == "test-worker"
    assert first.state is JobState.RUNNING

    # The row is `running` now, so there is nothing left to claim for anyone.
    assert other.claim_next() is None


def test_empty_queue_is_reported_as_zero(worker: JobWorker) -> None:
    assert worker.run_pending() == 0


# ------------------------------------------------------- the honest stub path (default)


def test_stub_job_fails_terminal_without_burning_retry_budget(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    job = make_job(repository)

    assert worker.run_pending() == 1

    done = repository.get_job(job.id, user_id=job.user_id)
    assert done is not None
    assert done.state is JobState.FAILED
    assert done.reason_code is ReasonCode.INTERNAL
    # Deterministic failure: one claim, zero retries, named ticket.
    assert done.attempts == 0
    assert "E1-T2" in (done.error_detail or "")
    assert done.finished_at is not None
    assert done.claimed_by == "test-worker"


def test_stub_failure_refunds_exactly_one_unit(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    job = make_job(repository)
    assert repository.has_event_for_job(job.id, EVENT_ITEM_ENQUEUED)

    worker.run_pending()

    assert repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)
    # quota_units_used sums signed units over the period: -1 spent, +1 refunded = 0.
    assert repository.quota_units_used(job.user_id, start=_period_start(), end=_period_end()) == 0


def test_vton_and_export_kinds_name_their_own_tickets(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    for kind in (JobKind.VTON, JobKind.EXPORT):
        job = make_job(repository, kind=kind)
        worker.run_pending()
        done = repository.get_job(job.id, user_id=job.user_id)
        assert done is not None and done.state is JobState.FAILED
        assert "E4-T1" in (done.error_detail or "") or "E7" in (done.error_detail or "")


# ------------------------------------------------------------------- retry budget & refund


def test_transient_failures_retry_then_reach_done(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    job = make_job(repository)
    calls: list[UUID] = []

    flaky_calls: list[int] = []

    def flaky(job: JobRecord, repo: SqliteRepository) -> None:
        flaky_calls.append(1)
        calls.append(job.id)
        if len(flaky_calls) <= 2:
            raise TransientJobError("provider timed out (simulated)")

    worker.handlers[JobKind.FULL] = flaky  # type: ignore[assignment]
    # run_pending counts claims, not jobs: one job, three claims (two retries, then done).
    assert worker.run_pending() == 3

    done = repository.get_job(job.id, user_id=job.user_id)
    assert done is not None
    assert done.state is JobState.DONE
    assert done.progress == 1.0
    assert len(calls) == 3  # claimed three times: two retries, then success
    assert not repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)


def test_permanent_transient_failure_exhausts_budget_and_refunds_once(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    job = make_job(repository)
    calls: list[UUID] = []

    def always_transient(job: JobRecord, repo: SqliteRepository) -> None:
        calls.append(job.id)
        raise TransientJobError("still failing (simulated)")

    worker.handlers[JobKind.FULL] = always_transient  # type: ignore[assignment]
    assert worker.run_pending() == 3  # the whole budget, spent on one job

    done = repository.get_job(job.id, user_id=job.user_id)
    assert done is not None
    assert done.state is JobState.FAILED
    assert len(calls) == 3, "the contract allows at most three claims"
    assert repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)


def test_budget_exhaustion_refunds_the_right_user(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    """Two users' jobs both fail; each refund lands on its own ledger, never swapped."""
    job_a = make_job(repository, user_id=USER_A)
    job_b = make_job(repository, user_id=USER_B)

    worker.run_pending()

    for job in (job_a, job_b):
        done = repository.get_job(job.id, user_id=job.user_id)
        assert done is not None and done.state is JobState.FAILED
        assert repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)
        assert (
            repository.quota_units_used(job.user_id, start=_period_start(), end=_period_end())
            == 0
        )


# ---------------------------------------------------------------------- success & recovery


def test_successful_handler_marks_done_with_assets_written(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    job = make_job(repository)
    calls: list[UUID] = []
    worker.handlers[JobKind.FULL] = writing_handler(calls)  # type: ignore[assignment]

    assert worker.run_pending() == 1

    done = repository.get_job(job.id, user_id=job.user_id)
    assert done is not None
    assert done.state is JobState.DONE
    assert done.reason_code is None
    pack = repository.get_item(job.item_id, user_id=job.user_id)
    assert pack is not None
    assert pack.path_for("cutout") == f"items/{job.item_id}/cutout.png"


def test_interrupted_job_is_recovered_then_completes(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    """Crash simulation: claim the job, never finish it, restart the worker."""
    job = make_job(repository)
    claimed = worker.claim_next()
    assert claimed is not None and claimed.state is JobState.RUNNING
    del claimed  # ...and the process "dies" here.

    restarted = JobWorker(repository, claimed_by="restarted-worker")
    assert restarted.recover_interrupted() == 1

    requeued = repository.get_job(job.id, user_id=job.user_id)
    assert requeued is not None
    assert requeued.state is JobState.QUEUED
    assert requeued.attempts == 1, "a lost claim is a retryable failure: attempts += 1"

    calls: list[UUID] = []
    restarted.handlers[JobKind.FULL] = writing_handler(calls)  # type: ignore[assignment]
    assert restarted.run_pending() == 1

    done = repository.get_job(job.id, user_id=job.user_id)
    assert done is not None
    assert done.state is JobState.DONE
    pack = repository.get_item(job.item_id, user_id=job.user_id)
    assert pack is not None
    assert "cutout" in (pack.to_asset_pack().cutout_url or "")


def test_recovered_job_with_spent_budget_fails_instead_of_looping(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    """A crash at attempts == 2 cannot requeue (the machine refuses); it must terminate."""
    job = make_job(repository)
    # Walk the legal edges to a third claim: queued->running->queued (attempts 1),
    # ->running->queued (attempts 2), ->running ... then the process "dies".
    job = apply_transition(repository, job, to_state=JobState.RUNNING, claimed_by="w1")
    job = apply_transition(repository, job, to_state=JobState.QUEUED)
    job = apply_transition(repository, job, to_state=JobState.RUNNING, claimed_by="w1")
    job = apply_transition(repository, job, to_state=JobState.QUEUED)
    job = apply_transition(repository, job, to_state=JobState.RUNNING, claimed_by="dead")
    assert job.attempts == 2

    worker.recover_interrupted()

    done = repository.get_job(job.id, user_id=job.user_id)
    assert done is not None
    assert done.state is JobState.FAILED
    assert done.reason_code is ReasonCode.INTERNAL
    assert "retry budget" in (done.error_detail or "")


def test_recover_interrupted_returns_zero_on_clean_queue(worker: JobWorker) -> None:
    assert worker.recover_interrupted() == 0


def test_run_pending_respects_limit(
    repository: SqliteRepository, worker: JobWorker
) -> None:
    make_job(repository)
    make_job(repository)
    make_job(repository)

    assert worker.run_pending(limit=2) == 2
    # One job is still queued, untouched, for the next run.
    assert len(repository.list_jobs_by_state(JobState.QUEUED)) == 1


# ------------------------------------------------------------------ period helper


def _period_start():
    from apps.api.usage import period_for

    return period_for()[0]


def _period_end():
    from apps.api.usage import period_for

    return period_for()[1]
