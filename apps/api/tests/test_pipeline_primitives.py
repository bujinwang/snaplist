"""Tests for the pipeline primitives (PR A): named failures, job provenance, asset metadata, and
the shared asset derivations.

Each piece is here because the pipeline will lean on it, so each is tested as a contract rather
than as an implementation detail:

* `JobFailure` — a stage that knows *why* it failed must be able to say so, and the worker must
  not flatten that into `internal`. The three-branch policy is asserted by making a failure
  retryable and non-retryable and checking which one burns the budget.
* `set_job_provenance` — the columns are what a cost report and a "which model produced this"
  question read. Validation is tested on the boundaries the PG CHECK enforces, and `None` must
  survive as NULL: a free job and an unmeasured job are different claims.
* `AssetWrite.meta` — a caller may describe its own work but may not restate the digest.
* `asset_pack` — the CLI and the pipeline share one derivation, so the tests pin the properties
  both depend on (passthrough, resize, alpha fidelity).
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from apps.api import usage
from apps.api.jobs.errors import JobFailure
from apps.api.jobs.state_machine import MAX_ATTEMPTS
from apps.api.jobs.worker import JobWorker
from apps.api.jobs.writeback import (
    META_CONTENT_TYPE,
    META_SHA256,
    META_WRITER,
    WRITER_NAME,
    AssetWrite,
    write_back,
)
from apps.api.repository import JobRecord, Repository
from apps.api.schemas import JobKind, JobState, ReasonCode
from apps.api.storage import LocalStorage
from packages.vision import asset_pack

USER_A = UUID("33333333-3333-4333-8333-333333333333")
EVENT_JOB_REFUNDED = "job_refunded"


@pytest.fixture
def storage_root() -> Iterator[Path]:
    """An objects directory under a plain temp dir.

    Deliberately NOT `tmp_path`: requesting that fixture makes pytest create its session basetemp,
    and in this sandbox `mkdir pytest-of-<user>` raises `EEXIST` (the directory survives between
    runs), which surfaces as an error at *setup* — a test failure about the sandbox, not the code.
    A `TemporaryDirectory` has no such coupling and is removed by the context manager.
    """
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp) / "objects"


def make_job(repository: Repository, *, user_id: UUID = USER_A) -> JobRecord:
    """Enqueue the way the router does: source asset + item + job + one quota unit."""
    item = repository.create_item(user_id=user_id)
    with repository.transaction():
        repository.set_asset(
            item.id,
            user_id=user_id,
            kind="source",
            storage_path=f"items/{user_id}/{item.id}/source.jpg",
            byte_size=3,
            meta={"sha256": "0" * 64, "content_type": "image/jpeg"},
        )
        job = repository.create_job(item_id=item.id, user_id=user_id, kind=JobKind.FULL)
        usage.spend_one(repository, user_id, job_id=job.id, item_id=item.id)
    claimed = repository.get_job(job.id, user_id=user_id)
    assert claimed is not None
    return claimed


def failing_handler(**kwargs: object) -> object:
    """A handler that always raises `JobFailure` with the given kwargs."""

    def run(job: JobRecord, repo: Repository) -> None:
        raise JobFailure(**kwargs)  # type: ignore[arg-type]

    return run


# ------------------------------------------------------------------ A1 + A2: named failures


def test_job_failure_is_a_runtime_error_so_the_branch_order_matters() -> None:
    """The new branch must come first: `JobFailure` is a `RuntimeError`.

    If somebody moves the generic `except Exception` above it, nothing raises and nothing looks
    broken — the job just reports `internal` with a retryable budget. This asserts the shape that
    makes the ordering load-bearing.
    """
    assert issubclass(JobFailure, RuntimeError)


def test_non_retryable_failure_is_terminal_with_its_reason_and_one_refund(
    repository: Repository,
) -> None:
    job = make_job(repository)
    worker = JobWorker(repository, claimed_by="test-worker")
    worker.handlers[JobKind.FULL] = failing_handler(  # type: ignore[assignment]
        reason_code=ReasonCode.NO_GARMENT,
        retryable=False,
        stage="segment",
        detail="the matte covers 3% of the frame",
    )

    # One claim: a deterministic cause must not spend the retry budget.
    assert worker.run_pending() == 1

    failed = repository.get_job(job.id, user_id=job.user_id)
    assert failed is not None
    assert failed.state is JobState.FAILED
    assert failed.reason_code is ReasonCode.NO_GARMENT
    assert failed.error_detail is not None
    assert "segment" in failed.error_detail
    assert "3% of the frame" in failed.error_detail
    assert repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)


def test_retryable_failure_spends_the_budget_then_terminates_with_the_same_reason(
    repository: Repository,
) -> None:
    job = make_job(repository)
    worker = JobWorker(repository, claimed_by="test-worker")
    worker.handlers[JobKind.FULL] = failing_handler(  # type: ignore[assignment]
        reason_code=ReasonCode.INTERNAL,
        retryable=True,
        stage="vton",
        detail="provider returned 503",
    )

    # Retryable: re-queued each time until the budget is spent, then terminal.
    assert worker.run_pending() == MAX_ATTEMPTS

    failed = repository.get_job(job.id, user_id=job.user_id)
    assert failed is not None
    assert failed.state is JobState.FAILED
    assert failed.reason_code is ReasonCode.INTERNAL
    assert repository.has_event_for_job(job.id, EVENT_JOB_REFUNDED)


def test_a_plain_exception_still_reports_internal(repository: Repository) -> None:
    """The generic branch keeps its meaning: an unnamed failure is `internal` and retryable."""
    job = make_job(repository)
    worker = JobWorker(repository, claimed_by="test-worker")

    def boom(job: JobRecord, repo: Repository) -> None:
        raise ValueError("something nobody classified")

    worker.handlers[JobKind.FULL] = boom  # type: ignore[assignment]
    assert worker.run_pending() == MAX_ATTEMPTS

    failed = repository.get_job(job.id, user_id=job.user_id)
    assert failed is not None
    assert failed.state is JobState.FAILED
    assert failed.reason_code is ReasonCode.INTERNAL
    assert failed.error_detail is not None
    assert "ValueError" in failed.error_detail


def test_job_failure_str_names_stage_reason_and_detail() -> None:
    exc = JobFailure(
        reason_code=ReasonCode.BAD_PHOTO,
        retryable=False,
        stage="segment",
        detail="two garments in frame",
    )
    text = str(exc)
    for fragment in ("segment", "bad_photo", "two garments in frame"):
        assert fragment in text, (fragment, text)


# ---------------------------------------------------------------------- A3: job provenance


def test_provenance_round_trips_and_is_readable(repository: Repository) -> None:
    job = make_job(repository)
    updated = repository.set_job_provenance(
        job.id,
        user_id=job.user_id,
        provider="  u2net  ",
        model_version="  u2net-portrait-v1  ",
        cost_micros=4321,
    )
    assert updated.provider == "u2net", "the stored value is trimmed"
    assert updated.model_version == "u2net-portrait-v1"
    assert updated.cost_micros == 4321

    # A fresh read returns the same three values — this is what a cost report consumes.
    reread = repository.get_job(job.id, user_id=job.user_id)
    assert reread is not None
    assert (reread.provider, reread.model_version, reread.cost_micros) == (
        "u2net",
        "u2net-portrait-v1",
        4321,
    )


def test_cost_micros_none_is_preserved_as_null_not_zero(repository: Repository) -> None:
    """UNMEASURED and free are different claims, and the column must be able to say both."""
    job = make_job(repository)
    updated = repository.set_job_provenance(
        job.id, user_id=job.user_id, provider="u2net", model_version="n/a", cost_micros=None
    )
    assert updated.cost_micros is None
    assert updated.cost_micros != 0


@pytest.mark.parametrize(
    ("provider", "model_version", "cost_micros", "fragment"),
    [
        ("", "m", None, "provider must be 2-64"),
        ("x", "m", None, "provider must be 2-64"),
        ("y" * 65, "m", None, "provider must be 2-64"),
        ("   ", "m", None, "provider must be 2-64"),
        ("ok", "", None, "model_version must be non-empty"),
        ("ok", "   ", None, "model_version must be non-empty"),
        ("ok", "m", -1, "cost_micros must be >= 0"),
        ("ok", "m", True, "cost_micros must be an int"),
        ("ok", "m", 1.5, "cost_micros must be an int"),
        # `stub` is legal at create_job and illegal here — see test_stub_is_refused_by_provenance.
        ("stub", "m", None, "must not be 'stub'"),
        ("  stub  ", "m", None, "must not be 'stub'"),
    ],
)
def test_provenance_validation_refuses_rather_than_truncating(
    repository: Repository,
    provider: str,
    model_version: str,
    cost_micros: object,
    fragment: str,
) -> None:
    """Each bound is the one the PG CHECK enforces, refused with a sentence instead of a
    constraint error — and refused *before* the write, so a rejected call changes nothing."""
    job = make_job(repository)
    with pytest.raises(ValueError) as caught:
        repository.set_job_provenance(
            job.id,
            user_id=job.user_id,
            provider=provider,
            model_version=model_version,
            cost_micros=cost_micros,  # type: ignore[arg-type]
        )
    assert fragment in str(caught.value), str(caught.value)

    unchanged = repository.get_job(job.id, user_id=job.user_id)
    assert unchanged is not None
    assert unchanged.model_version is None, "a rejected write must not half-apply"


def test_provenance_rejects_a_job_that_does_not_belong_to_the_user(
    repository: Repository,
) -> None:
    job = make_job(repository)
    other_user = UUID("44444444-4444-4444-8444-444444444444")
    with pytest.raises(LookupError):
        repository.set_job_provenance(
            job.id,
            user_id=other_user,
            provider="u2net",
            model_version="v1",
            cost_micros=0,
        )


def test_stub_is_refused_by_provenance_but_still_written_at_create(
    repository: Repository,
) -> None:
    """The two halves of the `stub` rule, asserted together so neither can drift alone.

    `create_job` writing `stub` is the honest "no pipeline has run yet". `set_job_provenance`
    writing it would say the same thing about a job whose handler just finished — which is a
    contradiction, and would erase the very distinction the column exists for. If somebody moves
    this gate onto `create_job`, the first assertion fails; if they remove it from provenance, the
    second does.
    """
    job = make_job(repository)
    assert job.provider == "stub", "create_job must still label a fresh job 'stub'"

    with pytest.raises(ValueError) as caught:
        repository.set_job_provenance(
            job.id, user_id=job.user_id, provider="stub", model_version="v1", cost_micros=0
        )
    message = str(caught.value)
    assert "must not be 'stub'" in message, message
    assert "no pipeline has run yet" in message, "the reason must be in the error, not just a code"

    # The job row is untouched: a refused write does not half-apply.
    after = repository.get_job(job.id, user_id=job.user_id)
    assert after is not None
    assert after.provider == "stub"
    assert after.model_version is None


# ------------------------------------------------------------------------ A4: asset metadata


def test_caller_meta_is_kept_but_cannot_forge_the_digest(
    repository: Repository, storage_root: Path
) -> None:
    """The store owns `sha256`/`content_type`/`writer`; the producer may add its own facts."""
    storage = LocalStorage(storage_root)
    item = repository.create_item(user_id=USER_A)
    payload = b"pretend png bytes"

    write_back(
        repository,
        storage,
        item_id=item.id,
        user_id=USER_A,
        assets=[
            AssetWrite(
                kind="prettified",
                data=payload,
                content_type="image/png",
                meta={
                    "backend": "local_shadow_attenuation_v1",
                    "seconds": 0.42,
                    "sha256": "FORGED",
                    "content_type": "text/plain",
                    "writer": "somebody-else",
                },
            )
        ],
    )

    record = repository.get_item(item.id, user_id=USER_A)
    assert record is not None
    asset = next(a for a in record.assets if a.kind == "prettified")
    assert asset.meta is not None
    assert asset.meta["backend"] == "local_shadow_attenuation_v1", "the caller's own facts survive"
    assert asset.meta["seconds"] == 0.42
    assert asset.meta[META_SHA256] != "FORGED", "a caller may not state the digest"
    assert len(asset.meta[META_SHA256]) == 64
    assert asset.meta[META_CONTENT_TYPE] == "image/png"
    assert asset.meta[META_WRITER] == WRITER_NAME


def test_omitting_meta_behaves_exactly_as_before(
    repository: Repository, storage_root: Path
) -> None:
    storage = LocalStorage(storage_root)
    item = repository.create_item(user_id=USER_A)

    write_back(
        repository,
        storage,
        item_id=item.id,
        user_id=USER_A,
        assets=[AssetWrite(kind="cutout", data=b"bytes", content_type="image/png")],
    )

    record = repository.get_item(item.id, user_id=USER_A)
    assert record is not None
    asset = next(a for a in record.assets if a.kind == "cutout")
    assert asset.meta is not None
    assert set(asset.meta) == {META_SHA256, META_CONTENT_TYPE, META_WRITER}


# ------------------------------------------------------------------------ A4: asset_pack


def _png(size: tuple[int, int], mode: str, color: object) -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_alpha_for_canvas_passes_an_equal_size_alpha_through_byte_for_byte() -> None:
    flat = _png((40, 50), "RGB", (10, 20, 30))
    alpha = _png((40, 50), "L", 128)
    assert asset_pack.alpha_for_canvas(alpha, flat) is alpha


def test_alpha_for_canvas_resizes_to_the_flat_canvas() -> None:
    flat = _png((60, 40), "RGB", (200, 100, 50))
    alpha = _png((30, 20), "L", 200)
    out = asset_pack.alpha_for_canvas(alpha, flat)
    with Image.open(io.BytesIO(out)) as opened:
        assert opened.size == (60, 40)
        assert opened.mode == "L"
        assert opened.getpixel((30, 20)) == 200, "a flat fill stays flat through a bilinear resize"


def test_cutout_png_composites_the_photo_onto_the_alpha_exactly() -> None:
    photo = _png((24, 16), "RGB", (200, 40, 40))
    alpha = _png((24, 16), "L", 90)
    out = asset_pack.cutout_png(photo, alpha)

    with Image.open(io.BytesIO(out)) as opened:
        assert opened.mode == "RGBA"
        assert opened.size == (24, 16)
        assert opened.getchannel("A").getpixel((0, 0)) == 90
        assert opened.getchannel("A").getpixel((23, 15)) == 90
        red, green, blue, _ = opened.getpixel((5, 5))
    assert (red, green, blue) == (200, 40, 40), "the photograph is not altered, only masked"


def test_the_cli_uses_the_shared_derivations_rather_than_its_own_copies() -> None:
    """One derivation, two consumers. If somebody re-adds a private copy to the CLI, the two
    will drift on interpolation or on the passthrough rule and nothing would notice."""
    from packages.vision import cli

    assert cli.cutout_png is asset_pack.cutout_png
    assert cli.alpha_for_canvas is asset_pack.alpha_for_canvas
    assert not hasattr(cli, "_alpha_on_flat_canvas"), "the private copy is gone, not duplicated"


def test_asset_pack_imports_nothing_from_the_job_layer() -> None:
    """It is a pure bytes-in/bytes-out module; pulling the API into it would invert the layering."""
    source = (asset_pack.__file__ or "")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "apps.api" not in text
    assert "httpx" not in text
