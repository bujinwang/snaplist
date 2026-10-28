"""Job state machine: the legal edges, every illegal edge, the retry budget, and the refund.

`docs/CONTRACTS.md` §3 is the spec; `apps/api/jobs/state_machine.py` is the implementation. These
tests drive the module directly (an HTTP route cannot move a job — deliberately) and then assert
through the repository, so the checks are about persisted state, not about in-memory objects.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from apps.api import usage
from apps.api.jobs import IllegalJobTransition, apply_transition, fail, retry_or_fail
from apps.api.jobs.state_machine import MAX_ATTEMPTS
from apps.api.repository import Repository, SqliteRepository, utcnow
from apps.api.schemas import JobKind, JobState, ReasonCode

USER = UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def seeded(repository: SqliteRepository) -> tuple[Repository, UUID, UUID]:
    item = repository.create_item(user_id=USER)
    job = repository.create_job(item_id=item.id, user_id=USER, kind=JobKind.FULL)
    usage.spend_one(repository, USER, job_id=job.id, item_id=item.id)
    return repository, item.id, job.id


def _job(repository: Repository, job_id: UUID):
    record = repository.get_job(job_id, user_id=USER)
    assert record is not None
    return record


def test_queued_to_running_to_done(seeded) -> None:
    repository, _, job_id = seeded
    running = apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING)
    assert running.started_at is not None
    assert running.claimed_by is None  # no claim token was passed

    done = apply_transition(repository, running, to_state=JobState.DONE)
    assert done.state is JobState.DONE
    assert done.progress == 1.0, "CONTRACTS §3: done sets progress 1.0"
    assert done.finished_at is not None
    assert done.reason_code is None, "invariant 1: done => reason_code is null"

    assert _job(repository, job_id).state is JobState.DONE


def test_done_is_terminal(seeded) -> None:
    repository, _, job_id = seeded
    done = apply_transition(
        repository,
        apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING),
        to_state=JobState.DONE,
    )
    for illegal in (JobState.RUNNING, JobState.QUEUED, JobState.FAILED):
        with pytest.raises(IllegalJobTransition):
            apply_transition(repository, done, to_state=illegal, reason_code=ReasonCode.INTERNAL)


def test_queued_cannot_jump_straight_to_done(seeded) -> None:
    repository, _, job_id = seeded
    with pytest.raises(IllegalJobTransition) as excinfo:
        apply_transition(repository, _job(repository, job_id), to_state=JobState.DONE)
    assert excinfo.value.from_state is JobState.QUEUED
    assert excinfo.value.to_state is JobState.DONE
    assert "cannot move queued -> done" in str(excinfo.value)


def test_a_job_cannot_be_failed_from_queued(seeded) -> None:
    repository, _, job_id = seeded
    with pytest.raises(IllegalJobTransition):
        fail(repository, _job(repository, job_id), reason_code=ReasonCode.BAD_PHOTO)


def test_failing_without_a_reason_code_is_a_programming_error(seeded) -> None:
    repository, _, job_id = seeded
    running = apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING)
    with pytest.raises(ValueError, match="reason_code"):
        apply_transition(repository, running, to_state=JobState.FAILED)


def test_failure_sets_the_reason_code_and_refunds_exactly_one_unit(seeded) -> None:
    repository, _, job_id = seeded
    start, end = usage.period_for()
    assert repository.quota_units_used(USER, start=start, end=end) == 1

    running = apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING)
    failed = fail(repository, running, reason_code=ReasonCode.LOW_CONFIDENCE)
    assert failed.state is JobState.FAILED
    assert failed.reason_code is ReasonCode.LOW_CONFIDENCE
    assert failed.finished_at is not None
    assert repository.quota_units_used(USER, start=start, end=end) == 0, "one unit refunded"


def test_refund_is_idempotent_per_job(seeded) -> None:
    repository, _, job_id = seeded
    start, end = usage.period_for()
    running = apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING)
    fail(repository, running, reason_code=ReasonCode.INTERNAL)
    # A retry of the same failure path must not mint a second unit.
    assert usage.refund_one(repository, USER, job_id=job_id) is False
    assert repository.quota_units_used(USER, start=start, end=end) == 0


def test_a_refund_without_a_spend_mints_nothing(repository: SqliteRepository) -> None:
    item = repository.create_item(user_id=USER)
    job = repository.create_job(item_id=item.id, user_id=USER, kind=JobKind.FULL)
    start, end = usage.period_for()
    assert usage.refund_one(repository, USER, job_id=job.id) is False
    assert repository.quota_units_used(USER, start=start, end=end) == 0
    # A negative "used" would be free quota; the ledger must never produce one.
    assert repository.quota_units_used(USER, start=start, end=end) >= 0


def test_retry_budget_is_three_claims_then_terminal(seeded) -> None:
    repository, _, job_id = seeded
    job = _job(repository, job_id)

    # claim 1 → retry
    job = apply_transition(repository, job, to_state=JobState.RUNNING)
    job = retry_or_fail(repository, job, reason_code=ReasonCode.INTERNAL, retryable=True)
    assert job.state is JobState.QUEUED and job.attempts == 1

    # claim 2 → retry
    job = apply_transition(repository, job, to_state=JobState.RUNNING)
    job = retry_or_fail(repository, job, reason_code=ReasonCode.INTERNAL, retryable=True)
    assert job.state is JobState.QUEUED and job.attempts == 2

    # claim 3 → the budget is gone, so it must terminate with a reason code
    job = apply_transition(repository, job, to_state=JobState.RUNNING)
    job = retry_or_fail(repository, job, reason_code=ReasonCode.INTERNAL, retryable=True)
    assert job.state is JobState.FAILED
    assert job.reason_code is ReasonCode.INTERNAL
    assert job.attempts == MAX_ATTEMPTS - 1, "attempts counts retries actually spent"

    with pytest.raises(IllegalJobTransition):
        apply_transition(repository, job, to_state=JobState.QUEUED)


def test_a_non_retryable_error_fails_immediately(seeded) -> None:
    repository, _, job_id = seeded
    running = apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING)
    job = retry_or_fail(repository, running, reason_code=ReasonCode.VTON_REJECTED, retryable=False)
    assert job.state is JobState.FAILED
    assert job.reason_code is ReasonCode.VTON_REJECTED


def test_progress_outside_zero_to_one_is_refused(seeded) -> None:
    repository, _, job_id = seeded
    running = apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING)
    with pytest.raises(ValueError, match="progress"):
        apply_transition(repository, running, to_state=JobState.DONE, progress=1.4)


def test_reason_code_enum_has_no_sixth_value() -> None:
    assert sorted(code.value for code in ReasonCode) == sorted(
        ["bad_photo", "no_garment", "low_confidence", "vton_rejected", "internal"]
    )


def test_timestamps_are_ordered(seeded) -> None:
    repository, _, job_id = seeded
    running = apply_transition(repository, _job(repository, job_id), to_state=JobState.RUNNING)
    done = apply_transition(repository, running, to_state=JobState.DONE)
    assert done.created_at <= (done.started_at or utcnow()) <= (done.finished_at or utcnow())
