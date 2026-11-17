"""CLI smoke tests for `python -m apps.api.jobs.worker` — the production entry point.

Every other worker test constructs `JobWorker(...)` and injects handlers. That is the right unit
test, but it cannot catch a broken `main()`: a wrong storage build, a missing handler injection, or
a removed "refuse to start" check would leave this whole suite green while every real job failed.
These tests run the actual module in a subprocess instead.

Two explicit fakes, stated so nobody mistakes this for a measurement:

1. `SNAPLIST_ATTRS_PROVIDER=stub` — the attribute extractor is a stub. The job's
   `provider='u2net|classical|stub'` and `model_version='stub:not-a-model'` are deliberately written
   into the row so a reader can see at a glance that no VLM ran. This is NOT accuracy evidence.
2. A generated `sitecustomize.py` replaces ONLY `packages.vision.segment.segment` in the child
   process, because CI has no u2net weights (176 MB, `make fetch-models`). Everything the ticket is
   actually about — settings, `build_storage`, the unconfigured refusal, and the FULL/PRETTIFY
   handler injection — is the real `main()` code path. The stub even labels its `StageMeta` backend
   `u2net`, so the provider string has the same shape as a weighted run.

No handler is monkeypatched here: doing that would bypass `main()` and defeat the purpose.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

from apps.api.repository import JobRecord, SqliteRepository
from apps.api.schemas import ItemAttributes, JobKind, JobState
from apps.api.storage import LocalStorage, source_object_key

REPO_ROOT = Path(__file__).resolve().parents[3]
PHOTO = REPO_ROOT / "evals" / "golden-provisional" / "photos" / "top-02.jpg"
USER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

# Injected into the child process only. See the module docstring: this replaces one *stage*, not a
# handler, because the CI image cannot carry the model weights.
_SITECUSTOMIZE = '''\
import io

from PIL import Image

import packages.vision.segment as _segment
from packages.vision.types import SegmentBackend, StageMeta


def _segment_stub(image: bytes, backend=None):
    with Image.open(io.BytesIO(image)) as opened:
        size = opened.size
    buffer = io.BytesIO()
    Image.new("L", size, 255).save(buffer, format="PNG")
    return _segment.SegmentResult(
        mask_png=buffer.getvalue(),
        meta=StageMeta(stage="segment", backend=SegmentBackend.U2NET, seconds=0.0),
    )


_segment.segment = _segment_stub
'''


def seed_item(
    repository: SqliteRepository,
    storage: LocalStorage,
    *,
    with_attributes: bool,
    with_cutout: bool = False,
    kind: JobKind = JobKind.FULL,
) -> tuple[UUID, UUID]:
    """An item with source bytes, optionally attributes and a cutout, and one queued job."""
    item = repository.create_item(user_id=USER)
    data = PHOTO.read_bytes()
    key = source_object_key(USER, item.id, "image/jpeg")
    storage.write(key, data, content_type="image/jpeg")
    with repository.transaction():
        repository.set_asset(
            item.id,
            user_id=USER,
            kind="source",
            position=0,
            storage_path=key,
            byte_size=len(data),
            meta={"sha256": hashlib.sha256(data).hexdigest(), "content_type": "image/jpeg"},
        )
        if with_cutout:
            cutout = b"a previous full job's cutout"
            cutout_key = f"items/{USER}/{item.id}/cutout.png"
            storage.write(cutout_key, cutout, content_type="image/png")
            repository.set_asset(
                item.id,
                user_id=USER,
                kind="cutout",
                position=0,
                storage_path=cutout_key,
                byte_size=len(cutout),
                meta={"sha256": hashlib.sha256(cutout).hexdigest()},
            )
    if with_attributes:
        repository.update_item_attributes(
            item.id,
            user_id=USER,
            attributes=ItemAttributes(
                category="top",
                colors=["black"],
                pattern="solid",
                material=[],
                confidence={"category": 0.9},
                model_version="stub:not-a-model",
            ),
        )
    job = repository.create_job(item_id=item.id, user_id=USER, kind=kind)
    return item.id, job.id


@pytest.fixture
def seeded() -> tuple[Path, Path, SqliteRepository, LocalStorage]:
    """A temp SQLite file and object directory the child process can open."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sitecustomize_dir = root / "sitecustomize"
        sitecustomize_dir.mkdir()
        (sitecustomize_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
        db = root / "db.sqlite"
        storage = LocalStorage(root / "storage")
        repository = SqliteRepository(db)
        try:
            yield db, sitecustomize_dir, repository, storage
        finally:
            repository.close()


def cli_env(
    sitecustomize_dir: Path,
    storage_root: Path,
    *,
    backend: str = "local",
) -> dict[str, str]:
    """The child's environment. Credentials are cleared unless a test adds them back."""
    env = dict(os.environ)
    for key in (
        "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_JWT_SECRET",
        "SNAPLIST_SUPABASE_URL", "SNAPLIST_SUPABASE_SERVICE_KEY",
    ):
        env.pop(key, None)
    env.update(
        {
            "SNAPLIST_ATTRS_PROVIDER": "stub",  # explicitly a fake run, never accuracy evidence
            "SNAPLIST_STORAGE_BACKEND": backend,
            "SNAPLIST_STORAGE_DIR": str(storage_root),
            "PYTHONPATH": os.pathsep.join(
                [str(sitecustomize_dir), str(REPO_ROOT), env.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep),
        }
    )
    return env


def run_cli(
    db: Path,
    sitecustomize_dir: Path,
    storage: LocalStorage,
    *,
    limit: int,
    backend: str = "local",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "apps.api.jobs.worker",
            "--database",
            str(db),
            "--limit",
            str(limit),
        ],
        cwd=REPO_ROOT,
        env=cli_env(sitecustomize_dir, Path(storage.root), backend=backend),
        capture_output=True,
        text=True,
        timeout=300,
    )


def job_of(repository: SqliteRepository, job_id: UUID) -> JobRecord:
    job = repository.get_job(job_id, user_id=USER)
    assert job is not None
    return job


# -------------------------------------------------------------------------- 1. FULL via the CLI


def test_cli_completes_a_full_job_and_writes_three_assets(
    seeded: tuple[Path, Path, SqliteRepository, LocalStorage],
) -> None:
    db, sitecustomize_dir, repository, storage = seeded
    _item_id, job_id = seed_item(repository, storage, with_attributes=False, kind=JobKind.FULL)
    repository.close()  # the child opens the same file; SQLite serialises writers, not readers here

    result = run_cli(db, sitecustomize_dir, storage, limit=1)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "processed 1 job(s)" in result.stdout, result.stdout

    job = job_of(SqliteRepository(db), job_id)
    assert job.state is JobState.DONE
    assert job.reason_code is None
    # These two values are the marker of the deliberately fake VLM — do not relax them.
    assert job.provider == "u2net|classical|stub"
    assert job.model_version == "stub:not-a-model"
    assert job.cost_micros is None
    assert job.heartbeat_at is not None

    reopened = SqliteRepository(db)
    item = reopened.get_item(_item_id, user_id=USER)
    assert item is not None
    produced = sorted((a.kind, a.position) for a in item.assets)
    assert produced == [("cutout", 0), ("prettified", 0), ("source", 0)]
    for asset in item.assets:
        if asset.kind == "source":
            continue
        assert asset.meta is not None
        data = storage.read(asset.storage_path)
        assert data
        assert asset.meta["sha256"] == hashlib.sha256(data).hexdigest()
    reopened.close()


# -------------------------------------------------------------- 2. PRETTIFY injection via CLI


def test_cli_also_completes_a_prettify_job_in_the_same_run(
    seeded: tuple[Path, Path, SqliteRepository, LocalStorage],
) -> None:
    """Both handlers must be injected by `main()`: one process, one round, two kinds done."""
    db, sitecustomize_dir, repository, storage = seeded
    _full_item, full_job = seed_item(
        repository, storage, with_attributes=False, kind=JobKind.FULL
    )
    rerender_item, prettify_job = seed_item(
        repository, storage, with_attributes=True, with_cutout=True, kind=JobKind.PRETTIFY
    )
    cutout_before = repository.get_item(rerender_item, user_id=USER).path_for("cutout")  # type: ignore[union-attr]
    repository.close()

    result = run_cli(db, sitecustomize_dir, storage, limit=2)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "processed 2 job(s)" in result.stdout, result.stdout

    reopened = SqliteRepository(db)
    try:
        full = job_of(reopened, full_job)
        rerender = job_of(reopened, prettify_job)
        assert full.state is JobState.DONE, full.error_detail
        assert rerender.state is JobState.DONE, rerender.error_detail
        assert rerender.provider is not None
        assert "from-item-attributes" in rerender.provider

        item = reopened.get_item(rerender_item, user_id=USER)
        assert item is not None
        assert item.path_for("cutout") == cutout_before
        assert storage.read(cutout_before) == b"a previous full job's cutout"
        prettified = [a for a in item.assets if a.kind == "prettified"]
        assert len(prettified) == 1
        assert prettified[0].meta is not None
        assert prettified[0].meta["sha256"] == hashlib.sha256(
            storage.read(prettified[0].storage_path)
        ).hexdigest()
    finally:
        reopened.close()


# ------------------------------------------------------------------------ 3. unconfigured start


def test_cli_refuses_to_start_without_storage_and_claims_nothing(
    seeded: tuple[Path, Path, SqliteRepository, LocalStorage],
) -> None:
    db, sitecustomize_dir, repository, storage = seeded
    _item_id, job_id = seed_item(repository, storage, with_attributes=False, kind=JobKind.FULL)
    repository.close()

    result = run_cli(db, sitecustomize_dir, storage, limit=1, backend="supabase")
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "refusing to start" in result.stderr, result.stderr
    assert "processed" not in result.stdout

    job = job_of(SqliteRepository(db), job_id)
    assert job.state is JobState.QUEUED, "a refused worker must not claim anything"
    assert job.attempts == 0
