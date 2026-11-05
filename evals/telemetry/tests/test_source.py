# ruff: noqa: F811
# `tmp_path` is imported to register the shim-safe pytest fixture defined in fixtures.py;
# each test's parameter of the same name is the injected value, not a redefinition.
"""Real-SQLite adapter tests for E0-T5 (T03).

Every assertion here is measured against a database file on disk, never against SQL text. The
reader is `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`; these tests prove it never writes
DDL, never constructs `SqliteRepository`, preserves `item_id` as plain TEXT, and splits NULL
(evidence absence -> DEGRADED) from malformed non-NULL ids (corruption -> 3b).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from evals.telemetry.compute import build_timelines, source_state, window_bounds
from evals.telemetry.gate import build_report
from evals.telemetry.model import AttributionStatus, SourceState
from evals.telemetry.source import SqliteEventSource
from evals.telemetry.tests import fixtures as fx
from evals.telemetry.tests.fixtures import tmp_path  # noqa: F401 - pytest fixture


def _new_db(tmp_path: Path, name: str = "dev.db") -> tuple[fx.Db, Path]:
    """Create one fresh, real SQLite file and return the writer plus its path."""
    path = tmp_path / name
    return fx.make_db(path), path


def test_read_only_source_never_creates_a_table(tmp_path: Path) -> None:
    path = tmp_path / "empty.db"
    db = fx.make_db(path, with_events_table=False)
    fx.close(db)
    before = hashlib.md5(path.read_bytes()).hexdigest()

    data = SqliteEventSource(path).load()

    # A present FILE with no `events` table is 3a, not "an empty but present source".
    assert data.present is True
    assert data.events_table is False
    assert data.events == ()
    assert source_state(data) is SourceState.SOURCE_ABSENT_3A

    connection = sqlite3.connect(path)
    try:
        tables = connection.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    assert tables == [], "the reader must not run executescript() or any migration DDL"
    assert hashlib.md5(path.read_bytes()).hexdigest() == before, "reader wrote to the database"


def test_missing_database_file_is_source_absent_3a(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.db"
    data = SqliteEventSource(path).load()
    assert data.present is False
    assert data.events_table is False
    assert source_state(data) is SourceState.SOURCE_ABSENT_3A
    assert data.source_label == f"sqlite:{path}"


def test_unset_database_is_source_absent_3a() -> None:
    data = SqliteEventSource("").load()
    assert data.present is False
    assert data.events_table is False
    assert source_state(data) is SourceState.SOURCE_ABSENT_3A
    assert data.source_label == "sqlite:<unset>"


def test_backend_and_source_label_are_pinned(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db, count=1)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.source_label == f"sqlite:{path}"
    assert data.backend == "local-dev"
    report = build_report(data, fx.NOW_DT, sha="test-sha")
    assert report.source_label == f"sqlite:{path}"
    assert report.backend == "local-dev"
    assert report.git_sha == "test-sha"


def test_event_item_id_is_plain_text_not_a_uuid_object(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db, count=1)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.events
    item_ids = {event.item_id for event in data.events}
    assert item_ids == {fx.item_uuid(1)}
    assert all(isinstance(value, str) and len(value) == 36 for value in item_ids if value)


def test_null_item_id_is_legal_evidence_absence_not_3b(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    # `subscription_started` legitimately carries no item id (ADR-008 reverse CHECK).
    db.event(name="subscription_started", item_id=None)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.unparseable_rows == 0
    assert source_state(data) is SourceState.OK


def test_malformed_non_null_item_id_is_unparseable_3b(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    db.event(
        name="prettify_approved",
        item_id="tee",
        decision="approve",
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
    )
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.unparseable_rows == 1
    assert len(data.malformed_event_ids) == 1
    assert source_state(data) is SourceState.SOURCE_UNUSABLE_3B
    assert fx.run_cli(path, "--since", "7d", "--gate").returncode == 3


def test_uppercase_item_id_is_corruption_never_coerced(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    upper = "abcdef01-0000-4000-8000-000000000000".upper()
    db.event(
        name="prettify_approved",
        item_id=upper,
        decision="approve",
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
    )
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert upper in {event.item_id for event in data.events}
    assert data.unparseable_rows == 1, "no lower()/UUID() coercion: two ids must never merge"
    assert source_state(data) is SourceState.SOURCE_UNUSABLE_3B


def test_field_names_json_text_is_never_read_or_parsed(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    db.connection.execute(
        "update events set field_names = ?", ("this is not json and is not a gate input",)
    )
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.unparseable_rows == 0
    assert source_state(data) is SourceState.OK
    assert fx.run_cli(path, "--since", "7d", "--gate").returncode == 0


def test_ledger_self_join_recovers_item_from_the_enqueued_row(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    item_id = fx.item_uuid(1)
    job_id = fx.job_uuid(900)
    db.ledger_enqueued(job_id, item_id)
    db.ledger_refunded(job_id)  # production leaves job_refunded.item_id NULL
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.refund_rows_total == 1
    assert data.attribution.refund_rows_attributable == 1
    assert data.attribution.unattributable == 0
    assert data.attribution.excluded_item_ids == frozenset({item_id})
    assert data.attribution.status is AttributionStatus.INTACT


def test_job_refunded_item_id_column_is_never_read(tmp_path: Path) -> None:
    """A naive reader of `job_refunded.item_id` would exclude the WRONG item."""
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    correct = fx.item_uuid(1)
    decoy = fx.item_uuid(2)
    job_id = fx.job_uuid(901)
    db.ledger_enqueued(job_id, correct)
    db.ledger_refunded(job_id, item_id=decoy)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.excluded_item_ids == frozenset({correct})
    assert decoy not in data.attribution.excluded_item_ids


def test_refund_counting_is_distinct_item_id_not_refund_rows(tmp_path: Path) -> None:
    """M7: one item with three refund jobs counts as ONE excluded item."""
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db, count=31)
    item_id = fx.item_uuid(31)
    for offset in range(1, 4):
        job_id = fx.job_uuid(500 + offset)
        db.ledger_enqueued(job_id, item_id)
        db.ledger_refunded(job_id)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.refund_rows_total == 3
    assert data.attribution.refund_rows_attributable == 3
    assert data.attribution.excluded_item_ids == frozenset({item_id})
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert "excluded_refunded=1 (n=3 refund rows, distinct item_id)" in result.stdout


def test_zero_refund_rows_is_vacuously_intact_without_division(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.refund_rows_total == 0
    assert data.attribution.unattributable == 0
    assert data.attribution.missing_rate == 0.0
    assert data.attribution.status is AttributionStatus.INTACT
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "excluded_refunded=0 (n=0 refund rows)" in result.stdout


def test_missing_usage_events_table_is_intact_not_3b(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    db.connection.execute("drop table usage_events")
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.status is AttributionStatus.INTACT
    assert data.attribution.refund_rows_total == 0
    assert source_state(data) is SourceState.OK


def test_null_join_key_is_degraded_not_3b(tmp_path: Path) -> None:
    """Q2: NULL = evidence absence (PG SET NULL is a REAL producer), counted in the rate."""
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    for offset in range(1, 10):
        job_id = fx.job_uuid(700 + offset)
        db.ledger_enqueued(job_id, fx.item_uuid(700 + offset))
        db.ledger_refunded(job_id)
    db.ledger_refunded(None)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.refund_rows_total == 10
    assert data.attribution.refund_rows_attributable == 9
    assert data.attribution.unattributable == 1
    assert data.attribution.missing_rate == 0.10
    assert data.attribution.status is AttributionStatus.DEGRADED
    assert data.unparseable_rows == 0
    assert source_state(data) is SourceState.OK, "0 < rate <= 0.20 is a disclosure, not 3b"


def test_all_join_keys_null_trips_the_source_state_guard(tmp_path: Path) -> None:
    """M9a: the guard must survive even though the verdict layer sees excluded_rate == 0."""
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    for _ in range(3):
        db.ledger_enqueued(None, None)
        db.ledger_refunded(None)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.status is AttributionStatus.DEGRADED
    assert data.attribution.missing_rate == 1.0
    assert data.unparseable_rows == 0, "NULL is never 'unparseable'"
    assert source_state(data) is SourceState.SOURCE_UNUSABLE_3B


def test_malformed_non_null_join_key_is_unverified_3b(tmp_path: Path) -> None:
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db)
    db.ledger_enqueued("not-a-uuid", fx.item_uuid(1))
    db.ledger_refunded("not-a-uuid")
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.unparseable_rows > 0
    assert data.attribution.status is AttributionStatus.UNVERIFIED
    assert source_state(data) is SourceState.SOURCE_UNUSABLE_3B
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 3
    assert "excluded_refunded=UNMEASURED" in result.stdout


def test_failure_mix_counts_distinct_items_not_job_rows(tmp_path: Path) -> None:
    """Addendum 1, C: three failed jobs on ONE item is one exclusion, not three."""
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db, count=31)
    item_id = fx.item_uuid(31)
    for offset in range(1, 4):
        db.job(900 + offset, item_id, state="failed", reason_code="bad_photo")
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.failure_mix["bad_photo"] == 1
    assert data.failed_items == frozenset({item_id})
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert "failure_mix: bad_photo=1" in result.stdout


def test_sqlite_fk_asymmetry_is_real_in_the_fixture(tmp_path: Path) -> None:
    """`jobs` cascades; `usage_events` and `events.item_id` have no FK at all."""
    db, path = _new_db(tmp_path)
    assert db.connection.execute("pragma foreign_keys").fetchone()[0] == 1
    jobs_fk = list(db.connection.execute("pragma foreign_key_list(jobs)"))
    ledger_fk = list(db.connection.execute("pragma foreign_key_list(usage_events)"))
    events_fk = list(db.connection.execute("pragma foreign_key_list(events)"))
    assert len(jobs_fk) == 1 and jobs_fk[0][6] == "CASCADE"
    assert ledger_fk == []
    assert events_fk == []
    fx.close(db)


def test_hard_delete_leaves_events_and_ledger_surviving(tmp_path: Path) -> None:
    """Addendum 1, B measured on SQLite: items 1->0, jobs 1->0, events and ledger survive."""
    db, path = _new_db(tmp_path)
    fx.populate_baseline(db, count=1)
    item_id = fx.item_uuid(1)
    job_id = fx.job_uuid(1)
    db.ledger_enqueued(job_id, item_id)
    db.ledger_refunded(job_id)
    fx.close(db)

    writer = sqlite3.connect(path)
    try:
        before = tuple(
            writer.execute(f"select count(*) from {table}").fetchone()[0]
            for table in ("items", "jobs", "events", "usage_events")
        )
    finally:
        writer.close()
    assert before == (1, 1, 4, 2)

    writer = sqlite3.connect(path)
    try:
        writer.execute("pragma foreign_keys = on")
        writer.execute("delete from items")
        writer.commit()
        after = tuple(
            writer.execute(f"select count(*) from {table}").fetchone()[0]
            for table in ("items", "jobs", "events", "usage_events")
        )
    finally:
        writer.close()
    assert after == (0, 0, 4, 2), "jobs cascade; events and the ledger keep their ids"

    data = SqliteEventSource(path).load()
    assert len(data.events) == 4
    assert data.orphan_item_ids == frozenset({item_id})
    assert data.attribution.excluded_item_ids == frozenset({item_id})
    start, end = window_bounds(fx.NOW_DT)
    assert item_id in build_timelines(data.events, start, end)
