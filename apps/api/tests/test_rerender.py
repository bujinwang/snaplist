"""Rerender: a new PRETTIFY job, one quota unit, and no VLM.

The route used to be a permanent 501 with a docstring claiming neither the worker nor prettify
existed. Both do now. What this file pins is the *shape* of the fix:

* a re-render creates a NEW job row on a terminal item and spends exactly one quota unit
  (CONTRACTS §3 answers both questions — no new decision is being made here);
* it reads the category from attributes a previous FULL job stored, so no `VLM_API_KEY` is needed;
* it rewrites ONLY `prettified`, leaving the FULL job's `cutout` untouched.

The `repository` fixture parametrizes over SQLite and Postgres when `SNAPLIST_TEST_PG` is set.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from apps.api.jobs import pipeline
from apps.api.jobs.pipeline import build_prettify_handler
from apps.api.jobs.worker import JobWorker
from apps.api.main import create_app
from apps.api.repository import ItemRecord, Repository, utcnow
from apps.api.schemas import ItemAttributes, JobKind, JobState, ReasonCode
from apps.api.settings import Settings
from apps.api.storage import LocalStorage
from packages.vision.prettify import PrettifyResult
from packages.vision.segment import SegmentResult
from packages.vision.types import PrettifyMode, StageMeta

USER = UUID("99999999-9999-4999-8999-999999999999")
OTHER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
HEADERS = {"x-snaplist-user-id": str(USER)}
EVENT_JOB_REFUNDED = "job_refunded"
PHOTO = (
    Path(__file__).resolve().parents[3]
    / "evals" / "golden-provisional" / "photos" / "top-02.jpg"
)


@pytest.fixture
def env(repository: Repository) -> Iterator[tuple[Repository, LocalStorage, TestClient]]:
    """Repository + storage + the real app, sharing all three.

    Requesting the parametrized `repository` fixture directly propagates its two params to every
    test here, so the route and the handler are exercised on SQLite *and* Postgres.
    """
    with tempfile.TemporaryDirectory() as tmp:
        storage = LocalStorage(
            Path(tmp) / "objects", secret=b"unit-test-signing-secret-32bytes"
        )
        app = create_app(
            repository=repository,
            settings=Settings(env="local", supabase_jwt_secret=""),
            storage=storage,
        )
        with TestClient(app) as client:
            yield repository, storage, client
        repository.close()


def item_with_source(
    repository: Repository,
    storage: LocalStorage,
    *,
    user_id: UUID = USER,
    attributes: bool = True,
    photo: bytes | None = None,
) -> ItemRecord:
    """The state a rerender expects: source bytes, an attributes record, and a cutout from FULL."""
    item = repository.create_item(user_id=user_id)
    data = PHOTO.read_bytes() if photo is None else photo
    key = f"items/{user_id}/{item.id}/source.jpg"
    storage.write(key, data, content_type="image/jpeg")
    with repository.transaction():
        repository.set_asset(
            item.id, user_id=user_id, kind="source", storage_path=key,
            byte_size=len(data), meta={"sha256": hashlib.sha256(data).hexdigest()},
        )
        cutout = b"the full job's cutout"
        cutout_key = f"items/{user_id}/{item.id}/cutout.png"
        storage.write(cutout_key, cutout, content_type="image/png")
        repository.set_asset(
            item.id, user_id=user_id, kind="cutout", storage_path=cutout_key,
            byte_size=len(cutout), meta={"sha256": hashlib.sha256(cutout).hexdigest()},
        )
    if attributes:
        repository.update_item_attributes(
            item.id,
            user_id=user_id,
            attributes=ItemAttributes(
                category="top", colors=["black"], pattern="solid", material=[],
                confidence={"category": 0.9}, model_version="full-vlm-1",
            ),
        )
    record = repository.get_item(item.id, user_id=user_id)
    assert record is not None
    return record


def enqueue_rerender(client: TestClient, item_id: UUID) -> UUID:
    """Create the PRETTIFY job the way a client does — through the route under test."""
    response = client.post(f"/v1/items/{item_id}/rerender", headers=HEADERS)
    assert response.status_code == 202, response.text
    return UUID(response.json()["job_id"])


def spent(repository: Repository, user_id: UUID = USER) -> int:
    now = utcnow()
    return repository.quota_units_used(
        user_id, start=now - timedelta(days=1), end=now + timedelta(days=1)
    )


def jobs_of(repository: Repository, item_id: UUID, user_id: UUID = USER) -> list:
    return [j for j in repository.list_jobs_by_state(JobState.QUEUED)
            if j.item_id == item_id and j.kind is JobKind.PRETTIFY]


# --------------------------------------------------------------------------------------- route


def test_rerender_enqueues_a_new_job_and_spends_one_quota_unit(
    env: tuple[Repository, LocalStorage, TestClient],
) -> None:
    repository, storage, client = env
    item = item_with_source(repository, storage)
    before = spent(repository)

    response = client.post(f"/v1/items/{item.id}/rerender", headers=HEADERS)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["item_id"] == str(item.id)
    assert body["state"] == "queued"

    queued = jobs_of(repository, item.id)
    assert len(queued) == 1
    assert queued[0].attempts == 0
    assert str(queued[0].id) == body["job_id"]
    assert spent(repository) == before + 1, "exactly one unit, atomically with the job"


def test_idempotency_key_replays_without_spending_twice(
    env: tuple[Repository, LocalStorage, TestClient],
) -> None:
    repository, storage, client = env
    item = item_with_source(repository, storage)
    headers = {**HEADERS, "Idempotency-Key": "rerender-same-key-123"}

    first = client.post(f"/v1/items/{item.id}/rerender", headers=headers)
    second = client.post(f"/v1/items/{item.id}/rerender", headers=headers)
    assert first.status_code == second.status_code == 202, (first.text, second.text)
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(jobs_of(repository, item.id)) == 1
    assert spent(repository) == 1, "a replay must not spend a second unit"


def test_foreign_and_deleted_items_are_404_without_a_job_or_a_charge(
    env: tuple[Repository, LocalStorage, TestClient],
) -> None:
    repository, storage, client = env
    foreign = item_with_source(repository, storage, user_id=OTHER)
    own = item_with_source(repository, storage)
    assert repository.soft_delete_item(own.id, user_id=USER)

    for item_id in (foreign.id, own.id):
        response = client.post(f"/v1/items/{item_id}/rerender", headers=HEADERS)
        assert response.status_code == 404, (item_id, response.text)
    assert jobs_of(repository, foreign.id, user_id=OTHER) == []
    assert jobs_of(repository, own.id) == []
    assert spent(repository) == 0
    assert spent(repository, OTHER) == 0


# ------------------------------------------------------------------------------------- handler


def worker_with_fake_segment(
    repository: Repository, storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
) -> JobWorker:
    """CI has no u2net weights; replace only segmentation, keep matte/rectify/prettify real."""
    def fake_segment(photo: bytes, *_args: object, **_kwargs: object) -> SegmentResult:
        with Image.open(io.BytesIO(photo)) as opened:
            size = opened.size
        buffer = io.BytesIO()
        Image.new("L", size, 255).save(buffer, format="PNG")
        return SegmentResult(mask_png=buffer.getvalue(), meta=StageMeta("segment"))

    monkeypatch.setattr(pipeline, "segment", fake_segment)
    return JobWorker(
        repository,
        claimed_by="test-worker",
        handlers={JobKind.PRETTIFY: build_prettify_handler(storage=storage)},
    )


def test_handler_rewrites_prettified_and_leaves_cutout_alone(
    env: tuple[Repository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, storage, client = env
    item = item_with_source(repository, storage)
    before = repository.get_item(item.id, user_id=USER)
    assert before is not None
    cutout_before = before.path_for("cutout")
    cutout_meta = next(a for a in before.assets if a.kind == "cutout").meta

    enqueue_rerender(client, item.id)
    worker = worker_with_fake_segment(repository, storage, monkeypatch)
    assert worker.run_pending(limit=1) >= 1

    after = repository.get_item(item.id, user_id=USER)
    assert after is not None
    produced = [a for a in after.assets if a.kind == "prettified"]
    assert len(produced) == 1, "write_back upserts: one row even on a fresh rerender"
    data = storage.read(produced[0].storage_path)
    assert data
    assert produced[0].meta is not None
    assert produced[0].meta["sha256"] == hashlib.sha256(data).hexdigest()
    assert produced[0].meta["stage"] == "prettify"

    cutout_after = after.path_for("cutout")
    assert cutout_after == cutout_before
    assert next(a for a in after.assets if a.kind == "cutout").meta == cutout_meta
    assert storage.read(cutout_after) == b"the full job's cutout"

    job = next(j for j in repository.list_jobs_by_state(JobState.DONE) if j.item_id == item.id)
    assert job.kind is JobKind.PRETTIFY
    assert job.reason_code is None
    assert job.provider is not None
    assert "from-item-attributes" in job.provider
    assert "fake" not in job.provider and "fake-vlm-1" not in job.provider
    assert job.model_version == "full-vlm-1"
    assert job.cost_micros is None


def test_item_without_attributes_fails_terminally_and_refunds_once(
    env: tuple[Repository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, storage, client = env
    item = item_with_source(repository, storage, attributes=False)
    enqueue_rerender(client, item.id)

    worker_with_fake_segment(repository, storage, monkeypatch).run_pending(limit=1)

    job = next(j for j in repository.list_jobs_by_state(JobState.FAILED) if j.item_id == item.id)
    assert job.kind is JobKind.PRETTIFY
    assert job.reason_code is ReasonCode.INTERNAL
    assert "full job first" in (job.error_detail or "")
    assert repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)
    assert spent(repository) == 0, "the single spend was refunded exactly once"


def test_prettify_is_classical_and_sdxl_is_never_called(
    env: tuple[Repository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rerender is the classical path; the generative one was adjudicated UNMEETABLE (E2-T2)."""
    repository, storage, client = env
    item = item_with_source(repository, storage)
    enqueue_rerender(client, item.id)

    seen: list[PrettifyMode] = []
    real_prettify = pipeline.prettify

    def recording(flat: bytes, alpha: bytes, **kwargs: object) -> PrettifyResult:
        mode = kwargs.get("mode")
        assert isinstance(mode, PrettifyMode), f"mode must be explicit, got {mode!r}"
        seen.append(mode)
        return real_prettify(flat, alpha, mode=mode)  # type: ignore[arg-type]

    def forbidden(*_args: object, **_kwargs: object) -> PrettifyResult:
        raise AssertionError("prettify_sdxl must never be called from a rerender")

    worker = worker_with_fake_segment(repository, storage, monkeypatch)
    monkeypatch.setattr(pipeline, "prettify", recording)
    monkeypatch.setattr(pipeline, "prettify_sdxl", forbidden, raising=False)
    worker.run_pending(limit=1)

    assert seen == [PrettifyMode.CLASSICAL]
