"""Real-Postgres integration test for PostgresEventSource (E0-T5 R3).

This is the test that turns R3's "UNVERIFIED (real PG)" into "MEASURED (local PG14,
superuser)". It is deliberately **skipped** unless a real Postgres is reachable, so the
default `pytest` (which runs without the optional `pg` group and without a database) never
goes red for a missing database.

Scope of what this proves (and what it deliberately does NOT):
  * proves  — the migration chain `shim -> 0001 -> 0002 -> 0003` applies cleanly to a real
             Postgres 14, and `PostgresEventSource` reading that database produces the SAME
             attribution / failure_mix / orphan classification as the SQLite path.
  * does NOT — exercise RLS row-visibility. The test connects as a superuser, which bypasses
             RLS, so the "real RLS role" path stays UNVERIFIED by design (see ADR-009 §3 and
             QA.md §5.2). Do not cite this test as evidence for Supabase / PG15 / RLS.

Requires: psycopg (the optional `pg` group), the `psql` CLI, and a Postgres listening on the
configurable socket/host (`SNAPLIST_PG_HOST`, default `/tmp`). It creates and drops a
throwaway database; it never touches an existing one.
"""

import datetime
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from evals.telemetry.compute import source_state
from evals.telemetry.pg_source import PostgresEventSource

REPO = Path(__file__).resolve().parents[3]
MIG = REPO / "infra" / "migrations"
PGHOST = os.environ.get("SNAPLIST_PG_HOST", "/tmp")
BASE_DB = os.environ.get("SNAPLIST_PG_BASE_DB", "postgres")

MIGRATION_ORDER = [
    "verify/local_supabase_shim.sql",
    "0001_init.sql",
    "0002_events.sql",
    "0003_events_item_id.sql",
]


def _pg_available() -> bool:
    """Cheap, side-effect-free gate: psycopg importable, psql + pg_isready on PATH, PG up.

    Deliberately does NOT import psycopg here: `importlib.util.find_spec` resolves the module
    without executing it, and `pg_isready` is a separate binary. This keeps `sys.modules` free of
    psycopg so it cannot break `test_pg_source.py::test_pg_module_imports_without_psycopg` (which
    asserts importing `pg_source` does not itself pull in psycopg).
    """
    if importlib.util.find_spec("psycopg") is None:
        return False
    if shutil.which("psql") is None or shutil.which("pg_isready") is None:
        return False
    result = subprocess.run(
        ["pg_isready", "-h", PGHOST, "-d", BASE_DB],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="no local Postgres + psql + psycopg (optional `pg` group); integration test skipped",
)


def _upsert_migration(conn, name: str) -> None:
    """Apply one migration file via `psql -f` (handles begin/commit blocks and $$ functions)."""
    path = MIG / name
    result = subprocess.run(
        ["psql", "-h", PGHOST, "-d", conn.info.dbname, "-v", "ON_ERROR_STOP=1", "-f", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"psql failed on {name}:\n{result.stdout}\n{result.stderr}"
    )


def test_postgres_event_source_reads_real_pg():
    """PostgresEventSource over a real PG14 yields the same verdicts as the SQLite path."""
    import psycopg

    dbname = f"snaplist_e0t5_itest_{os.getpid()}"
    reader_dsn = f"postgresql:///{dbname}?host={PGHOST}"

    # 1) throwaway database (superuser -> RLS bypassed; RLS stays UNVERIFIED)
    admin = psycopg.connect(dbname=BASE_DB, host=PGHOST, autocommit=True)
    try:
        admin.execute(f'drop database if exists "{dbname}" with (force)')
        admin.execute(f'create database "{dbname}"')
    finally:
        admin.close()

    try:
        # 2) apply migrations
        loader = psycopg.connect(reader_dsn, autocommit=True)
        try:
            for name in MIGRATION_ORDER:
                _upsert_migration(loader, name)
        finally:
            loader.close()

        # 3) constraint-valid dataset: ITEM1 has items/jobs/events + a full refund ledger pair;
        #    ITEM2 is an orphan upload_started (event but no items/jobs row).
        ITEM1 = "aaaaaaaa-0000-4000-8000-000000000001"
        ITEM2 = "aaaaaaaa-0000-4000-8000-000000000002"
        JOB1 = "bbbbbbbb-0000-4000-8000-000000000001"
        USER = "cccccccc-0000-4000-8000-000000000001"
        SUBJECT = "usr_cccccccc-0000-4000-8000-000000000001"
        SHA = "a" * 64
        NOW = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.UTC)

        conn = psycopg.connect(reader_dsn, autocommit=True)
        cur = conn.cursor()
        cur.execute(
            "insert into public.items (id, user_id, status) values (%s,%s,'draft')",
            (ITEM1, USER),
        )
        cur.execute(
            "insert into public.jobs (id, item_id, user_id, state, reason_code) "
            "values (%s,%s,%s,'failed','bad_photo')",
            (JOB1, ITEM1, USER),
        )
        cur.execute(
            "insert into public.usage_events (user_id, event, units, job_id, item_id) "
            "values (%s,'item_enqueued',-1,%s,%s)",
            (USER, JOB1, ITEM1),
        )
        cur.execute(
            "insert into public.usage_events (user_id, event, units, job_id) "
            "values (%s,'job_refunded',1,%s)",
            (USER, JOB1),
        )
        cur.execute(
            "insert into public.events "
            "(event_id, event, client_ts, received_at, session_id, subject, app_version, "
            " platform, item_id, decision, job_latency_ms, prettify_latency_ms, "
            " attrs_latency_ms, payload_sha256) "
            "values (%s,'prettify_approved',%s,%s,'sess-0001',%s,'1.0.0','ios',%s,"
            " 'approve',1000,1000,1000,%s)",
            (JOB1, NOW, NOW, SUBJECT, ITEM1, SHA),
        )
        cur.execute(
            "insert into public.events "
            "(event_id, event, client_ts, received_at, session_id, subject, app_version, "
            " platform, item_id, source, capture_mode, payload_sha256) "
            "values (%s,'upload_started',%s,%s,'sess-0002',%s,'1.0.0','ios',%s,"
            " 'camera','single',%s)",
            ("dddddddd-0000-4000-8000-000000000001", NOW, NOW, SUBJECT, ITEM2, SHA),
        )
        conn.close()

        # 4) run the read-only reader and assert parity with the SQLite path
        data = PostgresEventSource(reader_dsn).load()

        assert data.backend == "postgres"
        assert data.source_label.startswith("postgres:")
        assert data.present and data.events_table
        assert len(data.events) == 2
        assert data.attribution.status.value == "INTACT"
        assert data.attribution.refund_rows_total == 1
        assert data.attribution.refund_rows_attributable == 1
        assert data.attribution.unattributable == 0
        assert data.attribution.excluded_item_ids == frozenset({ITEM1})
        assert data.failure_mix.get("bad_photo") == 1
        assert data.failure_mix_unattributable == 1
        assert data.orphan_item_ids == frozenset({ITEM2})
        assert source_state(data).value == "OK"
    finally:
        # 5) always drop the throwaway database
        admin = psycopg.connect(dbname=BASE_DB, host=PGHOST, autocommit=True)
        try:
            admin.execute(f'drop database if exists "{dbname}" with (force)')
        finally:
            admin.close()
