"""Mock-based tests for the E0-T5 Postgres reader (R3).

These MUST run without `psycopg` installed. Two properties are proven:

1. Delayed import — importing `evals.telemetry.pg_source` never imports `psycopg` (the default
   `uv sync` omits the optional `pg` group, so the telemetry gate keeps running off SQLite).
2. Attribution equivalence — the Postgres adapter reuses the SAME `_materialize` /
   `_classify_attribution` as SQLite, so SET NULL NULL join keys are DEGRADED (evidence absence)
   on Postgres exactly as on SQLite, and a malformed (non-uuid) join key is UNVERIFIED + counts
   toward `unparseable_rows`.

The fake connection records every SQL string it is asked to execute and asserts that NONE of them
contain a write keyword (INSERT / UPDATE / DELETE / DROP / CREATE), proving the adapter is
strictly read-only.
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

from evals.telemetry.compute import source_state
from evals.telemetry.model import AttributionStatus
from evals.telemetry.pg_source import PostgresEventSource
from evals.telemetry.source import SqliteEventSource, select_source
from evals.telemetry.tests.fixtures import (
    event_uuid,
    item_uuid,
    job_uuid,
    make_db,
)


def _tmp_path() -> Path:
    """Sandbox-safe temp dir (mirrors fixtures.py's `tmp_path` workaround for the EEXIST shim)."""
    return Path(tempfile.mkdtemp(prefix="sl-e0t5-pg-"))

_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE")


def _event_row(item_id: str, event_id: str) -> dict:
    """A full pseudo-`dict_row` matching the keys of `_EVENT_COLUMNS` (string values)."""
    return {
        "event_id": event_id,
        "event": "prettify_approved",
        "received_at": "2026-11-04T12:00:00.000000Z",
        "client_ts": "2026-11-04T12:00:00.000000Z",
        "session_id": "sess-1a2b3c",
        "subject": f"usr_{'0' * 8}-3333-4333-8333-333333333333",
        "app_version": "1.4.0",
        "platform": "ios",
        "item_id": item_id,
        "decision": "approve",
        "prettify_latency_ms": 1000,
        "attrs_latency_ms": 1000,
        "fields_touched": 3,
        "export_latency_ms": 1000,
        "delivery_latency_ms": 9000,
        "payload_sha256": "0" * 64,
    }


class _FakeCursor:
    def __init__(self, conn: FakePGConn) -> None:
        self._conn = conn

    def execute(self, sql: str, *args: object, **kwargs: object) -> _FakeCursor:
        self._conn.sql_log.append(sql)
        self._conn._last_sql = sql
        return self

    def fetchall(self) -> list[dict]:
        return self._conn._rows_for(self._conn._last_sql)

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakePGConn:
    """A psycopg-shaped fake: supports both `connection.execute` and `cursor().execute`."""

    def __init__(
        self,
        tables: set[str],
        events: list[dict],
        items: list[dict],
        jobs: list[dict],
        ledger: list[dict],
    ) -> None:
        self._tables = tables
        self._events = events
        self._items = items
        self._jobs = jobs
        self._ledger = ledger
        self.sql_log: list[str] = []
        self._last_sql: str | None = None
        self.closed = False

    def _rows_for(self, sql: str) -> list[dict]:
        if "information_schema" in sql:
            return [{"table_name": t} for t in self._tables]
        if "public.events" in sql:
            return self._events
        if "public.items" in sql:
            return self._items
        if "public.jobs" in sql:
            return self._jobs
        if "usage_events" in sql:
            return self._ledger
        raise AssertionError(f"unexpected SQL dispatched to fake: {sql}")

    def execute(self, sql: str, *args: object, **kwargs: object) -> FakePGConn:
        self.sql_log.append(sql)
        self._last_sql = sql
        return self

    def fetchall(self) -> list[dict]:
        assert self._last_sql is not None
        return self._rows_for(self._last_sql)

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def _assert_no_writes(fake: FakePGConn) -> None:
    import re

    joined = " ".join(fake.sql_log).upper()
    for kw in _WRITE_KEYWORDS:
        # Word-boundary match so `DELETED_AT` (a SELECT column) is not mistaken for DELETE.
        assert not re.search(rf"\b{kw}\b", joined), (
            f"adapter issued a write ({kw}) — SQL log: {fake.sql_log}"
        )


def _default_tables() -> set[str]:
    return {"events", "items", "jobs", "usage_events"}


def _run_pg(
    ledger: list[dict],
    *,
    events: list[dict] | None = None,
    items: list[dict] | None = None,
    jobs: list[dict] | None = None,
    tables: set[str] | None = None,
) -> tuple:
    """Run the Postgres adapter against a fresh fake and return `(SourceData, fake)`."""
    if events is None:
        events = [_event_row(item_uuid(1), event_uuid(1))]
    if items is None:
        items = [{"id": item_uuid(1), "deleted_at": None}]
    if jobs is None:
        jobs = []
    if tables is None:
        tables = _default_tables()
    fake = FakePGConn(tables, events, items, jobs, ledger)
    with mock.patch("evals.telemetry.pg_source._connect", return_value=fake):
        data = PostgresEventSource("postgres://u:p@localhost/db").load()
    return data, fake


# ------------------------------------------------------------------------------------------
# 1. Delayed import: importing the module must NOT import psycopg.
# ------------------------------------------------------------------------------------------
def test_pg_module_imports_without_psycopg():
    # Capture the module set BEFORE importing, so a *different* test that legitimately imports
    # psycopg (the real-Postgres integration test) running earlier in the session cannot make
    # this assertion flaky. The contract under test is that importing `pg_source` ITSELF must
    # not pull in psycopg (the import is lazy, inside `_connect`) — not that psycopg is globally
    # absent for the whole run.
    before = set(sys.modules)
    mod = importlib.import_module("evals.telemetry.pg_source")
    # Loud failure if we ever regress to a top-level `import psycopg`.
    assert "psycopg" not in (set(sys.modules) - before)
    assert not hasattr(mod, "psycopg")


# ------------------------------------------------------------------------------------------
# 2. select_source wiring (env-driven).
# ------------------------------------------------------------------------------------------
def test_select_source_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("SNAPLIST_PG_DSN", raising=False)
    assert isinstance(select_source(), SqliteEventSource)


def test_select_source_uses_postgres_when_dsn_set(monkeypatch):
    monkeypatch.setenv("SNAPLIST_PG_DSN", "postgres://u:p@localhost/db")
    with mock.patch("evals.telemetry.pg_source._connect", return_value=mock.MagicMock()):
        src = select_source()
    assert isinstance(src, PostgresEventSource)


# ------------------------------------------------------------------------------------------
# 3. Read-only + attribution equivalence on equivalent logical rows (intact case).
# ------------------------------------------------------------------------------------------
def test_pg_attribution_matches_sqlite():
    item1 = item_uuid(1)
    item2 = item_uuid(2)
    job1 = job_uuid(1)
    ev1 = event_uuid(1)
    ev2 = event_uuid(2)

    # SQLite side: a real on-disk database (sandbox-safe temp dir).
    directory = _tmp_path()
    try:
        db_path = directory / "dev.db"
        db = make_db(db_path)
        db.connection.execute(
            "insert into items (id, deleted_at) values (?, ?)", (item1, None)
        )
        db.connection.execute(
            "insert into items (id, deleted_at) values (?, ?)", (item2, None)
        )
        db.connection.execute(
            "insert into jobs (id, item_id, state, reason_code) values (?, ?, ?, ?)",
            (job1, item1, "failed", "bad_photo"),
        )
        db.connection.execute(
            "insert into usage_events (event, units, job_id, item_id) "
            "values ('item_enqueued', -1, ?, ?)",
            (job1, item1),
        )
        db.connection.execute(
            "insert into usage_events (event, units, job_id, item_id) "
            "values ('job_refunded', 1, ?, ?)",
            (job1, None),
        )
        for eid, iid in ((ev1, item1), (ev2, item2)):
            db.event(name="prettify_approved", item_id=iid, event_id=eid, decision="approve")
        db.connection.commit()
        db.connection.close()
        sqlite_data = SqliteEventSource(str(db_path)).load()

        # Postgres side: the same logical rows via the fake connection.
        pg_events = [_event_row(item1, ev1), _event_row(item2, ev2)]
        pg_items = [
            {"id": item1, "deleted_at": None},
            {"id": item2, "deleted_at": None},
        ]
        pg_jobs = [{"item_id": item1, "state": "failed", "reason_code": "bad_photo"}]
        pg_ledger = [{"refund_job_id": job1, "enqueued_item_id": item1}]
        pg_data, fake = _run_pg(
            pg_ledger, events=pg_events, items=pg_items, jobs=pg_jobs
        )

        assert pg_data.backend == "postgres"
        assert pg_data.source_label.startswith("postgres:")
        assert "postgres://" in pg_data.source_label
        _assert_no_writes(fake)
        # SAME logic: identical attribution object.
        assert pg_data.attribution == sqlite_data.attribution
        assert pg_data.attribution.status is AttributionStatus.INTACT
        assert pg_data.attribution.refund_rows_total == 1
        assert pg_data.attribution.excluded_item_ids == {item1}
        # And the failure-mix residual is computed identically.
        assert pg_data.failure_mix_unattributable == sqlite_data.failure_mix_unattributable
        assert pg_data.failure_mix == sqlite_data.failure_mix
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# ------------------------------------------------------------------------------------------
# 4. NULL join keys => DEGRADED with missing_rate > 0 and source_state OK (rate <= 0.20).
# ------------------------------------------------------------------------------------------
def test_pg_null_join_keys_degraded_ok():
    intact = [
        {"refund_job_id": job_uuid(i), "enqueued_item_id": item_uuid(i)}
        for i in range(1, 6)
    ]
    ledger = intact + [{"refund_job_id": None, "enqueued_item_id": None}]
    data, fake = _run_pg(ledger)
    _assert_no_writes(fake)
    assert data.attribution.status is AttributionStatus.DEGRADED
    assert data.attribution.missing_rate > 0
    assert source_state(data).name == "OK"


# ------------------------------------------------------------------------------------------
# 5. All-NULL join keys => missing_rate = 1.0 => SOURCE_UNUSABLE_3B.
# ------------------------------------------------------------------------------------------
def test_pg_all_null_join_keys_source_unusable():
    ledger = [
        {"refund_job_id": None, "enqueued_item_id": None},
        {"refund_job_id": None, "enqueued_item_id": None},
    ]
    data, _fake = _run_pg(ledger)
    assert data.attribution.status is AttributionStatus.DEGRADED
    assert data.attribution.missing_rate > 0.20
    assert source_state(data).name == "SOURCE_UNUSABLE_3B"


# ------------------------------------------------------------------------------------------
# 6. Malformed (non-uuid) join key => UNVERIFIED and unparseable_rows > 0.
# ------------------------------------------------------------------------------------------
def test_pg_malformed_join_key_unverified():
    ledger = [
        {"refund_job_id": job_uuid(1), "enqueued_item_id": item_uuid(1)},
        {"refund_job_id": "not-a-uuid", "enqueued_item_id": "not-a-uuid"},
    ]
    data, fake = _run_pg(ledger)
    _assert_no_writes(fake)
    assert data.attribution.status is AttributionStatus.UNVERIFIED
    assert data.unparseable_rows > 0
    assert source_state(data).name == "SOURCE_UNUSABLE_3B"


# ------------------------------------------------------------------------------------------
# 7. source absent (3a): a connect/auth failure must yield present=False, never fabricated rows.
# ------------------------------------------------------------------------------------------
def test_pg_connect_failure_is_source_absent():
    with mock.patch(
        "evals.telemetry.pg_source._connect", side_effect=RuntimeError("no route to host")
    ):
        data = PostgresEventSource("postgres://u:p@localhost/db").load()
    assert data.present is False
    assert data.events_table is False
    assert data.events == ()
