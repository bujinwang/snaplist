"""Asset retention: only soft-deleted, already-exported items are ever cleaned up."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from apps.api.repository import EventRecord, SqliteRepository, utcnow
from apps.api.retention import sweep
from apps.api.storage import LocalStorage, StorageError, SupabaseStorage, asset_object_key


def _export_event(user_id: object, item_id: object, *, when: object | None = None) -> EventRecord:
    now = when or utcnow()
    return EventRecord(
        event_id=uuid4(),
        event="export_delivered",
        client_ts=now,
        received_at=now,
        session_id="sess-1",
        subject=f"usr_{user_id}",
        app_version="1.0",
        platform="ios",
        payload_sha256=uuid4().hex + uuid4().hex,
        item_id=item_id,
        marketplace="poshmark",
        row_count=1,
        bytes=9,
        delivery_latency_ms=12,
    )


def _seed(tmp_path, *, byte_size: int | None = 9, with_event: bool = True,
          soft_delete: bool = True) -> tuple[SqliteRepository, LocalStorage, object, object]:
    # `byte_size=None` means UNMEASURED, not "use the size storage happened to report".
    db = SqliteRepository(str(tmp_path / "snaplist.db"))
    storage = LocalStorage(tmp_path / "objects")
    user_id = uuid4()
    item = db.create_item(user_id=user_id, status="ready")
    key = asset_object_key(user_id, item.id, "prettified")
    storage.write(key, b"png-bytes", content_type="image/png")
    db.set_asset(
        item.id,
        user_id=user_id,
        kind="prettified",
        storage_path=key,
        byte_size=byte_size,
    )
    if soft_delete:
        db.soft_delete_item(item.id, user_id=user_id)
    if with_event:
        db.append_analytics_event(_export_event(user_id, item.id))
    return db, storage, user_id, item.id


def _future() -> object:
    return utcnow() + timedelta(days=31)


def test_dry_run_finds_candidates_and_changes_nothing(tmp_path) -> None:
    db, storage, _, _ = _seed(tmp_path)
    report = sweep(db, storage, now=_future(), dry_run=True)
    assert report.candidates == 1
    assert report.rows_deleted == 0
    assert report.objects_deleted == 0
    assert db.assets_for_retention(cutoff=_future() - timedelta(days=30))
    assert storage.exists(
        asset_object_key(
            uuid4(), uuid4(), "prettified"
        )
    ) is False  # sanity: a key we never wrote is absent
    assert "DRY-RUN" in report.render()


def test_execute_deletes_object_and_row_and_is_idempotent(tmp_path) -> None:
    db, storage, user_id, item_id = _seed(tmp_path)
    key = asset_object_key(user_id, item_id, "prettified")
    assert storage.exists(key)

    report = sweep(db, storage, now=_future(), dry_run=False)
    assert report.candidates == 1
    assert report.rows_deleted == 1
    assert report.objects_deleted == 1
    assert report.bytes_reclaimed == 9
    assert not storage.exists(key)
    assert db.get_item(item_id, user_id=user_id, include_deleted=True) is not None
    assert db.get_item(item_id, user_id=user_id, include_deleted=True).assets == ()

    again = sweep(db, storage, now=_future(), dry_run=False)
    assert again.candidates == 0
    assert again.bytes_reclaimed is None  # UNMEASURED, not 0


def test_live_item_is_never_deleted(tmp_path) -> None:
    db, storage, _, _ = _seed(tmp_path, soft_delete=False)
    assert sweep(db, storage, now=_future(), dry_run=False).candidates == 0


def test_no_delivered_export_is_never_deleted(tmp_path) -> None:
    db, storage, _, _ = _seed(tmp_path, with_event=False)
    assert sweep(db, storage, now=_future(), dry_run=False).candidates == 0


def test_recent_export_is_not_yet_eligible(tmp_path) -> None:
    db, storage, _, _ = _seed(tmp_path)
    assert sweep(db, storage, dry_run=False).candidates == 0


def test_unmeasured_bytes_stay_unmeasured(tmp_path) -> None:
    db, storage, _, _ = _seed(tmp_path, byte_size=None)
    report = sweep(db, storage, now=_future(), dry_run=False)
    assert report.rows_deleted == 1
    assert report.bytes_reclaimed is None
    assert report.unmeasured_byte_rows == 1
    assert '"bytes_reclaimed": null' in report.to_json()


def test_storage_failure_keeps_the_row_for_retry(tmp_path) -> None:
    db, storage, _, _ = _seed(tmp_path)

    class FailingStorage:
        def delete(self, key: str) -> bool:
            raise StorageError("bucket is down")

    report = sweep(db, FailingStorage(), now=_future(), dry_run=False)  # type: ignore[arg-type]
    assert report.failures == 1
    assert report.rows_deleted == 0
    assert sweep(db, storage, now=_future(), dry_run=False).rows_deleted == 1


def test_missing_object_still_cleans_the_row(tmp_path) -> None:
    db, storage, user_id, item_id = _seed(tmp_path)
    storage.delete(asset_object_key(user_id, item_id, "prettified"))
    report = sweep(db, storage, now=_future(), dry_run=False)
    assert report.objects_missing == 1
    assert report.rows_deleted == 1


def test_invalid_retention_window_is_refused(tmp_path) -> None:
    db, storage, _, _ = _seed(tmp_path)
    with pytest.raises(ValueError, match="retention_days"):
        sweep(db, storage, retention_days=-1)


def test_supabase_delete_maps_404_to_false_and_other_errors_to_storage_error() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(404 if "missing" in str(request.url) else 503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = SupabaseStorage(url="https://example.supabase.co", service_key="k", client=client)
    assert store.delete("items/u/i/missing.png") is False
    with pytest.raises(StorageError):
        store.delete("items/u/i/present.png")
    assert seen == ["DELETE", "DELETE"]
