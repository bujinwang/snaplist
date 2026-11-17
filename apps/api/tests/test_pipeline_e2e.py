"""End-to-end tests for the wired pipeline (`apps/api/jobs/pipeline.py`, PR B).

The claim under test is narrow and checkable: **a photo that arrives through the public API comes
out the other side of `python -m apps.api.jobs.worker` as two stored assets, a persisted attribute
record, and a job row that says which code produced them.** Before this PR the same flow ended in
`failed(internal)` with a refund, because every kind pointed at `default_handler`.

Only the VLM is faked (`extractor_factory`); segmentation, matte, rectify and prettify run for
real on a real fixture photo in the happy-path test. The failure-mapping tests replace the stages
with stubs that raise, so each row of the mapping table costs milliseconds instead of five seconds.

Time is not slept and nothing is downloaded: the fixture photo is on disk and the storage backend
is `LocalStorage` under a `TemporaryDirectory` (deliberately not pytest's `tmp_path`, whose session
basetemp creation raises EEXIST in this sandbox).
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from apps.api.jobs import pipeline
from apps.api.jobs.pipeline import build_full_handler, run_full_pipeline
from apps.api.jobs.state_machine import MAX_ATTEMPTS
from apps.api.jobs.worker import JobWorker
from apps.api.main import create_app
from apps.api.repository import JobRecord, SqliteRepository
from apps.api.schemas import ItemAttributes, JobKind, JobState, ReasonCode
from apps.api.settings import Settings
from apps.api.storage import LocalStorage, MissingObject, StorageError
from packages.vision.attrs import AttributeExtractionFailed, AttributesUnavailable
from packages.vision.matte import MatteResult
from packages.vision.prettify import PrettifyResult
from packages.vision.rectify import RectifyResult
from packages.vision.segment import SegmentBackendUnavailable, SegmentResult
from packages.vision.types import PrettifyMode, StageMeta

REPO_ROOT = Path(__file__).resolve().parents[3]
PHOTO = REPO_ROOT / "evals" / "golden-provisional" / "photos" / "top-02.jpg"
USER = UUID("66666666-6666-4666-8666-666666666666")
HEADERS = {"x-snaplist-user-id": str(USER)}
EVENT_JOB_REFUNDED = "job_refunded"


class FakeExtractor:
    """The only faked stage: a VLM that always answers the same way."""

    name = "fake-vlm"

    def __init__(self, *, category: str = "top") -> None:
        self.category = category
        self.calls = 0

    def extract(self, image: bytes) -> ItemAttributes:
        self.calls += 1
        assert image, "the extractor must be handed the photo bytes"
        return ItemAttributes(
            category=self.category,  # type: ignore[arg-type]
            colors=["black"],
            pattern="solid",
            material=[],
            confidence={"category": 0.9},
            model_version="fake-vlm-1",
        )


@pytest.fixture
def wired() -> Iterator[tuple[SqliteRepository, LocalStorage, TestClient]]:
    """A repository, a storage backend and the real app, sharing all three."""
    with tempfile.TemporaryDirectory() as tmp:
        repository = SqliteRepository(":memory:")
        storage = LocalStorage(
            Path(tmp) / "objects",
            secret=b"unit-test-signing-secret-32bytes",
            max_upload_bytes=10_000_000,
        )
        app = create_app(
            repository=repository,
            settings=Settings(env="local", supabase_jwt_secret=""),
            storage=storage,
        )
        with TestClient(app) as client:
            yield repository, storage, client
        repository.close()


def enqueue_and_upload(
    client: TestClient, *, photo: bytes | None = None
) -> tuple[UUID, UUID]:
    """Create an item through the API and PUT the bytes to its signed URL.

    This is the production path: the job only becomes claimable once the source object exists
    (`peek_next_queued_job` requires it), so a fixture that wrote the asset directly would describe
    a state the API cannot produce.
    """
    created = client.post("/v1/items", headers=HEADERS, json={"filename": "tee.jpg"})
    assert created.status_code == 202, created.text
    body = created.json()
    parsed = urlparse(body["upload_url"])
    path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    uploaded = client.put(
        path,
        content=PHOTO.read_bytes() if photo is None else photo,
        headers={"content-type": "image/jpeg"},
    )
    assert uploaded.status_code == 201, uploaded.text
    return UUID(body["item_id"]), UUID(body["job_id"])


def worker_for(
    repository: SqliteRepository,
    storage: LocalStorage,
    *,
    extractor: FakeExtractor | None = None,
) -> JobWorker:
    return JobWorker(
        repository,
        claimed_by="test-worker",
        handlers={
            JobKind.FULL: build_full_handler(
                storage=storage, extractor_factory=lambda: extractor or FakeExtractor()
            )
        },
    )


# ---------------------------------------------------------------- 1. the real end-to-end run


def test_a_photo_becomes_two_assets_and_an_attribute_record(
    wired: tuple[SqliteRepository, LocalStorage, TestClient],
) -> None:
    repository, storage, client = wired
    item_id, job_id = enqueue_and_upload(client)
    extractor = FakeExtractor()

    assert worker_for(repository, storage, extractor=extractor).run_pending() == 1

    job = repository.get_job(job_id, user_id=USER)
    assert job is not None
    assert job.state is JobState.DONE
    assert job.reason_code is None
    assert job.error_detail is None
    assert extractor.calls == 1

    # Exactly two assets, both at position 0: cutout and prettified. No mask row — its
    # information is the cutout's alpha channel and AssetPack does not expose it.
    item = repository.get_item(item_id, user_id=USER)
    assert item is not None
    produced = sorted((a.kind, a.position) for a in item.assets if a.kind != "source")
    assert produced == [("cutout", 0), ("prettified", 0)]

    for asset in (a for a in item.assets if a.kind != "source"):
        data = storage.read(asset.storage_path)
        assert data, f"{asset.kind} must be stored, not just recorded"
        assert asset.meta is not None
        # The stored digest is the digest of the bytes actually written.
        assert asset.meta["sha256"] == __import__("hashlib").sha256(data).hexdigest()
        assert asset.meta["stage"] in {"cutout", "prettify"}
        assert asset.meta["content_type" if "content_type" in asset.meta else "stage"]

    # Attributes reached the item, and the job says which code produced the pack.
    assert item.attributes is not None
    assert item.attributes.model_version == "fake-vlm-1"
    assert job.provider is not None
    assert "stub" not in job.provider, "a job that ran the pipeline may not claim it was a stub"
    assert job.provider.split("|")[1] == PrettifyMode.CLASSICAL.value
    assert job.provider.split("|")[2] == "fake-vlm"
    assert job.cost_micros is None, "UNMEASURED cost must stay None, never 0"

    # And the API can serve the pack: the two URLs are non-null and fetchable.
    pack = client.get(f"/v1/items/{item_id}", headers=HEADERS)
    assert pack.status_code == 200, pack.text
    body = pack.json()
    assert body["cutout_url"] and body["prettified_url"]
    assert body["attributes"] is not None
    for url_key in ("cutout_url", "prettified_url"):
        parsed = urlparse(body[url_key])
        path = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
        fetched = client.get(path)
        assert fetched.status_code == 200, (url_key, fetched.text)
        assert fetched.content, f"{url_key} must serve bytes"


def test_replaying_the_handler_is_idempotent(
    wired: tuple[SqliteRepository, LocalStorage, TestClient],
) -> None:
    """Re-running the same job must not duplicate rows or change the stored bytes."""
    repository, storage, client = wired
    item_id, job_id = enqueue_and_upload(client)
    worker = worker_for(repository, storage)
    assert worker.run_pending() == 1

    before = repository.get_item(item_id, user_id=USER)
    assert before is not None
    before_assets = {
        (a.kind, a.position): (a.storage_path, a.meta["sha256"] if a.meta else None)
        for a in before.assets
        if a.kind != "source"
    }

    job = repository.get_job(job_id, user_id=USER)
    assert job is not None
    run_full_pipeline(job, repository, storage, extractor_factory=FakeExtractor)

    after = repository.get_item(item_id, user_id=USER)
    assert after is not None
    after_assets = {
        (a.kind, a.position): (a.storage_path, a.meta["sha256"] if a.meta else None)
        for a in after.assets
        if a.kind != "source"
    }
    assert after_assets == before_assets, "same keys, same digests, no extra rows"


# --------------------------------------------------------- 2. the failure mapping, row by row


def _tiny_png(mode: str = "L", size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, 255 if mode == "L" else (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _stage_meta(stage: str) -> StageMeta:
    return StageMeta(stage=stage, backend="test-double", seconds=0.0)


def _claim_one(repository: SqliteRepository, storage: LocalStorage, client: TestClient) -> UUID:
    _item_id, job_id = enqueue_and_upload(client)
    return job_id


def _run_with(
    repository: SqliteRepository,
    storage: LocalStorage,
    monkeypatch: pytest.MonkeyPatch,
    **patches: object,
) -> None:
    for name, replacement in patches.items():
        monkeypatch.setattr(pipeline, name, replacement)
    assert worker_for(repository, storage).run_pending() >= 1


def _assert_terminal(
    repository: SqliteRepository,
    job_id: UUID,
    *,
    reason_code: ReasonCode,
) -> JobRecord:
    job = repository.get_job(job_id, user_id=USER)
    assert job is not None
    assert job.state is JobState.FAILED, (job.state, job.error_detail)
    assert job.reason_code is reason_code, job.error_detail
    assert repository.has_event_for_job(job_id, EVENT_JOB_REFUNDED), "exactly one refund"
    return job


def test_bad_photo_bytes_map_to_bad_photo(
    wired: tuple[SqliteRepository, LocalStorage, TestClient],
) -> None:
    """Undecodable bytes: the seller must send another photo, so no retry."""
    repository, storage, client = wired
    _item_id, job_id = enqueue_and_upload(client, photo=b"not an image at all")
    assert worker_for(repository, storage).run_pending() == 1
    job = _assert_terminal(repository, job_id, reason_code=ReasonCode.BAD_PHOTO)
    assert "segment" in (job.error_detail or "")


def test_no_garment_maps_to_no_garment(
    wired: tuple[SqliteRepository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero-pixel mask: deterministic, so one attempt and a refund."""
    repository, storage, client = wired
    job_id = _claim_one(repository, storage, client)

    def fake_rectify(*_args: object, **_kwargs: object) -> RectifyResult:
        raise ValueError("rectify(): alpha contains no garment pixels (no_garment)")

    _run_with(
        repository,
        storage,
        monkeypatch,
        segment=lambda *_a, **_k: SegmentResult(mask_png=_tiny_png(), meta=_stage_meta("segment")),
        matte=lambda *_a, **_k: MatteResult(alpha_png=_tiny_png(), meta=_stage_meta("matte")),
        rectify=fake_rectify,
    )
    _assert_terminal(repository, job_id, reason_code=ReasonCode.NO_GARMENT)


def test_missing_weights_map_to_internal_and_do_not_retry(
    wired: tuple[SqliteRepository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend this machine cannot run: retrying cannot find missing weights."""
    repository, storage, client = wired
    job_id = _claim_one(repository, storage, client)

    def unavailable(*_args: object, **_kwargs: object) -> SegmentResult:
        raise SegmentBackendUnavailable(
            "u2net weights missing at /models/u2net.pth; run make fetch-u2net"
        )

    _run_with(repository, storage, monkeypatch, segment=unavailable)
    job = _assert_terminal(repository, job_id, reason_code=ReasonCode.INTERNAL)
    assert "u2net weights missing" in (job.error_detail or ""), "the real reason must survive"


def test_unconfigured_vlm_maps_to_low_confidence(
    wired: tuple[SqliteRepository, LocalStorage, TestClient],
) -> None:
    repository, storage, client = wired
    job_id = _claim_one(repository, storage, client)

    def no_vlm() -> FakeExtractor:
        raise AttributesUnavailable("no VLM configured: set VLM_API_KEY")

    JobWorker(
        repository,
        claimed_by="test-worker",
        handlers={JobKind.FULL: build_full_handler(storage=storage, extractor_factory=no_vlm)},
    ).run_pending()
    _assert_terminal(repository, job_id, reason_code=ReasonCode.LOW_CONFIDENCE)


def test_extraction_failure_maps_to_low_confidence(
    wired: tuple[SqliteRepository, LocalStorage, TestClient],
) -> None:
    repository, storage, client = wired
    job_id = _claim_one(repository, storage, client)

    class Broken(FakeExtractor):
        def extract(self, image: bytes) -> ItemAttributes:
            raise AttributeExtractionFailed(
                "schema violation after one repair", attempts=2
            )

    JobWorker(
        repository,
        claimed_by="test-worker",
        handlers={
            JobKind.FULL: build_full_handler(storage=storage, extractor_factory=Broken)
        },
    ).run_pending()
    _assert_terminal(repository, job_id, reason_code=ReasonCode.LOW_CONFIDENCE)


def test_missing_source_object_maps_to_internal(
    wired: tuple[SqliteRepository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row says the bytes are there and the bucket disagrees: not a transient problem."""
    repository, storage, client = wired
    job_id = _claim_one(repository, storage, client)

    def missing(_key: str) -> bytes:
        raise MissingObject("the object is not in the bucket")

    monkeypatch.setattr(storage, "read", missing)
    assert worker_for(repository, storage).run_pending() == 1
    _assert_terminal(repository, job_id, reason_code=ReasonCode.INTERNAL)


def test_a_transient_storage_error_is_retried(
    wired: tuple[SqliteRepository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one class worth retrying: the budget decides, and it is spent before termination."""
    repository, storage, client = wired
    job_id = _claim_one(repository, storage, client)

    def flaky(_key: str) -> bytes:
        raise StorageError("connection reset by peer")

    monkeypatch.setattr(storage, "read", flaky)
    assert worker_for(repository, storage).run_pending() == MAX_ATTEMPTS
    job = repository.get_job(job_id, user_id=USER)
    assert job is not None
    assert job.state is JobState.FAILED
    assert job.reason_code is ReasonCode.INTERNAL
    assert job.attempts >= 1, "a transient failure must spend the retry budget"


def test_a_shape_assertion_maps_to_internal_without_retrying(
    wired: tuple[SqliteRepository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any other ValueError is our bug: loud, terminal, not worth three attempts."""
    repository, storage, client = wired
    job_id = _claim_one(repository, storage, client)

    def wrong_shape(*_args: object, **_kwargs: object) -> MatteResult:
        raise ValueError("matte(): unexpected alpha shape")

    _run_with(
        repository,
        storage,
        monkeypatch,
        segment=lambda *_a, **_k: SegmentResult(mask_png=_tiny_png(), meta=_stage_meta("segment")),
        matte=wrong_shape,
    )
    _assert_terminal(repository, job_id, reason_code=ReasonCode.INTERNAL)


# ------------------------------------------------------------- 3. the CLASSICAL / no-SDXL gate


def test_prettify_is_called_with_classical_and_never_with_sdxl(
    wired: tuple[SqliteRepository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prettify`'s default is PASS_THROUGH.

    Omitting `mode` would ship the un-cleaned flat-lay while the report claimed cleanup ran, and
    calling `prettify_sdxl` would turn every job into a retryable failure (E2-T2 adjudicated it
    UNMEETABLE). Both are asserted here rather than trusted to the call site.
    """
    repository, storage, client = wired
    _item_id, job_id = enqueue_and_upload(client)

    seen: list[PrettifyMode] = []
    real_prettify = pipeline.prettify

    def recording_prettify(flat: bytes, alpha: bytes, **kwargs: object) -> PrettifyResult:
        mode = kwargs.get("mode")
        assert isinstance(mode, PrettifyMode), f"mode must be passed explicitly, got {mode!r}"
        seen.append(mode)
        return real_prettify(flat, alpha, mode=mode)  # type: ignore[arg-type]

    def forbidden(*_args: object, **_kwargs: object) -> PrettifyResult:
        raise AssertionError("prettify_sdxl must never be called from the pipeline")

    monkeypatch.setattr(pipeline, "prettify", recording_prettify)
    monkeypatch.setattr(pipeline, "prettify_sdxl", forbidden, raising=False)

    assert worker_for(repository, storage).run_pending() == 1
    assert seen == [PrettifyMode.CLASSICAL]
    job = repository.get_job(job_id, user_id=USER)
    assert job is not None and job.state is JobState.DONE


def test_attributes_are_extracted_before_segmentation_so_rectify_gets_the_category(
    wired: tuple[SqliteRepository, LocalStorage, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order is load-bearing: `rectify` needs a real category and nothing else supplies one."""
    repository, storage, client = wired
    _item_id, _job_id = enqueue_and_upload(client)

    order: list[str] = []
    real_segment = pipeline.segment

    class OrderedExtractor(FakeExtractor):
        def extract(self, image: bytes) -> ItemAttributes:
            order.append("attributes")
            return super().extract(image)

    def ordered_segment(*args: object, **kwargs: object) -> SegmentResult:
        order.append("segment")
        return real_segment(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "segment", ordered_segment)
    JobWorker(
        repository,
        claimed_by="test-worker",
        handlers={
            JobKind.FULL: build_full_handler(
                storage=storage, extractor_factory=OrderedExtractor
            )
        },
    ).run_pending()

    assert order[:2] == ["attributes", "segment"], order
