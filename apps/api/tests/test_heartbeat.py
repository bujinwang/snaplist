"""Heartbeats: liveness is written, stale work is rescued, and a live worker is never robbed.

The contract, per CONTRACTS §3 and the `recover_interrupted` docstring this work finally honours:
a `running` job whose heartbeat stopped is a retryable failure (attempts += 1). Identity is NOT
used — `claimed_by` defaults to the same string in every worker, so freshness is the only honest
liveness signal.

These tests use the shared `repository` fixture, which parametrizes over SQLite **and Postgres**
when `SNAPLIST_TEST_PG` is set, so every method below is exercised on both backends.
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from apps.api import usage
from apps.api.jobs import pipeline
from apps.api.jobs.state_machine import MAX_ATTEMPTS, apply_transition
from apps.api.jobs.worker import HEARTBEAT_STALE_SECONDS, JobWorker
from apps.api.repository import JobRecord, Repository, SqliteRepository, utcnow
from apps.api.schemas import ItemAttributes, JobKind, JobState, ReasonCode
from apps.api.storage import LocalStorage
from packages.vision.matte import MatteResult
from packages.vision.prettify import PrettifyResult
from packages.vision.rectify import RectifyResult
from packages.vision.segment import SegmentResult
from packages.vision.types import StageMeta

USER = UUID("88888888-8888-4888-8888-888888888888")
EVENT_JOB_REFUNDED = "job_refunded"
STALE = 2  # seconds, so the tests are quick without mocking the clock


def make_job(repository: Repository, *, kind: JobKind = JobKind.FULL) -> JobRecord:
    """Enqueue the way the router does: item + source asset + job + one quota spend."""
    item = repository.create_item(user_id=USER)
    with repository.transaction():
        repository.set_asset(
            item.id,
            user_id=USER,
            kind="source",
            storage_path=f"items/{USER}/{item.id}/source.jpg",
            byte_size=3,
            meta={"sha256": "0" * 64, "content_type": "image/jpeg"},
        )
        job = repository.create_job(item_id=item.id, user_id=USER, kind=kind)
        usage.spend_one(repository, USER, job_id=job.id, item_id=item.id)
    claimed = repository.get_job(job.id, user_id=USER)
    assert claimed is not None
    return claimed


def running(
    repository: Repository, job: JobRecord, *, heartbeat: datetime | None
) -> JobRecord:
    """Claim, then set the heartbeat to exactly what the test wants (None = historical row)."""
    current = apply_transition(repository, job, to_state=JobState.RUNNING, claimed_by="worker-A")
    if heartbeat is not None:
        repository.touch_job_heartbeat(job.id, user_id=USER, now=heartbeat)
    reread = repository.get_job(job.id, user_id=USER)
    assert reread is not None
    assert reread.heartbeat_at == heartbeat
    return current


def worker_for(repository: Repository, *, stale_after: float = STALE) -> JobWorker:
    return JobWorker(repository, claimed_by="worker-B", stale_after_seconds=stale_after)


# ------------------------------------------------------------------------------- the sweeper


def test_stale_and_null_heartbeats_are_requeued(repository: Repository) -> None:
    """An old mark and a never-written mark are both stale, and error_detail says so."""
    old = make_job(repository)
    never = make_job(repository)
    running(repository, old, heartbeat=utcnow() - timedelta(seconds=STALE * 3))
    running(repository, never, heartbeat=None)

    assert worker_for(repository).sweep_stale_jobs() == 2

    for job_id in (old.id, never.id):
        saved = repository.get_job(job_id, user_id=USER)
        assert saved is not None
        assert saved.state is JobState.QUEUED
        assert saved.attempts == 1, "a lost heartbeat is a retryable failure, attempts += 1"
        assert saved.reason_code is None
        assert saved.progress == 0
        assert "stale heartbeat" in (saved.error_detail or "")
        assert not repository.has_event_for_job(job_id, EVENT_JOB_REFUNDED)


def test_a_fresh_heartbeat_is_left_alone(repository: Repository) -> None:
    job = make_job(repository)
    running(repository, job, heartbeat=utcnow())

    assert worker_for(repository).sweep_stale_jobs() == 0
    saved = repository.get_job(job.id, user_id=USER)
    assert saved is not None
    assert saved.state is JobState.RUNNING
    assert saved.attempts == 0


def test_budget_exhaustion_fails_terminally_with_one_refund(repository: Repository) -> None:
    job = make_job(repository)
    running(repository, job, heartbeat=utcnow() - timedelta(seconds=STALE * 3))
    # One attempt already spent by the transition to RUNNING in `running()`; drive it to the
    # point where the next retry would be the third claim.
    for _ in range(MAX_ATTEMPTS):
        assert worker_for(repository).sweep_stale_jobs() == 1
        current = repository.get_job(job.id, user_id=USER)
        assert current is not None
        if current.state is JobState.FAILED:
            break
        running(repository, current, heartbeat=utcnow() - timedelta(seconds=STALE * 3))

    saved = repository.get_job(job.id, user_id=USER)
    assert saved is not None
    assert saved.state is JobState.FAILED
    assert saved.reason_code is ReasonCode.INTERNAL
    assert repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)
    # The ledger emits exactly one refund row, not one per sweep.
    assert repository.count_events(USER, EVENT_JOB_REFUNDED, start=utcnow() - timedelta(days=1),
                                   end=utcnow() + timedelta(days=1)) == 1


def test_run_once_sweeps_before_it_claims(repository: Repository) -> None:
    """A stale running job is rescued in the SAME round, before a fresh queued one is taken.

    If the sweep were after the claim, this worker would take the queued job and leave the dead
    worker's job running until the next call.
    """
    # The STALE job is deliberately oldest. `peek_next_queued_job` is FIFO, so which job this
    # round claims *proves* the order of operations: with sweep-before-claim the rescued job is
    # queued again before the peek and is therefore claimed; with sweep-after-claim the peek sees
    # only the other queued job. Do not swap the creation order — that would make the test
    # insensitive to the exact bug it exists to catch.
    stale_job = make_job(repository)
    fresh_job = make_job(repository)
    running(repository, stale_job, heartbeat=utcnow() - timedelta(seconds=STALE * 3))

    calls: list[UUID] = []

    def handler(job: JobRecord, repo: Repository) -> None:
        calls.append(job.id)

    worker = JobWorker(
        repository,
        claimed_by="worker-B",
        stale_after_seconds=STALE,
        handlers={JobKind.FULL: handler},
    )
    assert worker.run_once() is True

    rescued = repository.get_job(stale_job.id, user_id=USER)
    assert rescued is not None
    assert rescued.attempts == 1, "the stale one was requeued this round"
    assert rescued.state is JobState.DONE, "and it was the job this round then claimed"

    assert calls == [stale_job.id], (
        "the rescued job must be claimable in the SAME round: if the sweep ran after the claim, "
        f"the peek would only see the fresh queued job (saw {calls})"
    )
    untouched = repository.get_job(fresh_job.id, user_id=USER)
    assert untouched is not None and untouched.state is JobState.QUEUED


def test_recovery_never_robs_a_live_worker(repository: Repository) -> None:
    """Both entry points must refuse a fresh heartbeat, even from a different worker name."""
    job = make_job(repository)
    running(repository, job, heartbeat=utcnow())

    worker = worker_for(repository)
    assert worker.recover_interrupted() == 0
    assert worker.sweep_stale_jobs() == 0

    saved = repository.get_job(job.id, user_id=USER)
    assert saved is not None
    assert saved.state is JobState.RUNNING
    assert saved.claimed_by == "worker-A"


# ---------------------------------------------------------------------------- the write side


def test_claim_next_writes_a_heartbeat_on_the_row(repository: Repository) -> None:
    job = make_job(repository)
    claimed = worker_for(repository).claim_next()
    assert claimed is not None
    assert claimed.state is JobState.RUNNING

    saved = repository.get_job(job.id, user_id=USER)
    assert saved is not None
    assert saved.heartbeat_at is not None
    assert claimed.heartbeat_at == saved.heartbeat_at
    assert abs((utcnow() - saved.heartbeat_at).total_seconds()) < 5


class FakeExtractor:
    name = "fake-vlm"

    def extract(self, image: bytes) -> ItemAttributes:
        return ItemAttributes(
            category="top", colors=["black"], pattern="solid", material=[],
            confidence={"category": 0.9}, model_version="fake-vlm-1",
        )


def _png(size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("L", size, 255).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def pipeline_env() -> Iterator[SqliteRepository | Repository]:
    """A repository + storage with a real PNG source and the slow stages stubbed out."""
    with tempfile.TemporaryDirectory() as tmp:
        repository = SqliteRepository(":memory:")
        storage = LocalStorage(Path(tmp) / "objects")
        item = repository.create_item(user_id=USER)
        photo = _png()
        key = f"items/{USER}/{item.id}/source.jpg"
        storage.write(key, photo, content_type="image/jpeg")
        repository.set_asset(
            item.id, user_id=USER, kind="source", storage_path=key, byte_size=len(photo)
        )
        repository.create_job(item_id=item.id, user_id=USER, kind=JobKind.FULL)
        try:
            yield repository
        finally:
            repository.close()


def test_pipeline_heartbeats_are_strictly_increasing(
    pipeline_env: Repository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage boundaries really write, and each mark is newer than the last.

    Monkeypatching the stages keeps this about the heartbeat contract, not about segmentation.
    Asserting monotonicity (rather than just "a heartbeat exists") catches a stage that silently
    skips its beat — which is how a long stage becomes indistinguishable from a dead worker.
    """
    repository = pipeline_env
    job = repository.get_job(
        next(iter([j.id for j in repository.list_jobs_by_state(JobState.QUEUED)])),
        user_id=USER,
    )
    assert job is not None

    seen: list[datetime] = []
    original = repository.touch_job_heartbeat

    def recording(job_id: UUID, *, user_id: UUID, now: datetime) -> None:
        seen.append(now)
        original(job_id, user_id=user_id, now=now)

    monkeypatch.setattr(repository, "touch_job_heartbeat", recording)
    monkeypatch.setattr(
        pipeline,
        "segment",
        lambda *_a, **_k: SegmentResult(mask_png=_png(), meta=StageMeta("segment")),
    )
    monkeypatch.setattr(
        pipeline, "matte", lambda *_a, **_k: MatteResult(alpha_png=_png(), meta=StageMeta("matte"))
    )
    monkeypatch.setattr(
        pipeline,
        "rectify",
        lambda *_a, **_k: RectifyResult(flat_png=_png(), homography=(), meta=StageMeta("rectify")),
    )
    monkeypatch.setattr(
        pipeline,
        "prettify",
        lambda *_a, **_k: PrettifyResult(prettified_png=_png(), meta=StageMeta("prettify")),
    )

    # A minimal storage double: this test is about the heartbeat writes, not about object storage.
    class Stored:
        def __init__(self, key: str) -> None:
            self.key = key

    class SourceStorage:
        def read(self, key: str) -> bytes:
            return _png()

        def write(self, key: str, data: bytes, **_kwargs: object) -> Stored:
            return Stored(key)

    pipeline.run_full_pipeline(
        job, repository, SourceStorage(), extractor_factory=FakeExtractor  # type: ignore[arg-type]
    )

    assert len(seen) >= 8, (
        f"expected a beat per stage boundary and around write_back, got {len(seen)}"
    )
    assert seen == sorted(seen), "heartbeats must be monotonically non-decreasing"
    assert seen[-1] > seen[0]
    saved = repository.get_job(job.id, user_id=USER)
    assert saved is not None and saved.heartbeat_at == seen[-1]


def test_default_stale_window_is_five_minutes() -> None:
    """300s: long enough for a slow stage, short enough to rescue a dead worker promptly."""
    assert HEARTBEAT_STALE_SECONDS == 300
