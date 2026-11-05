# ruff: noqa: F811
# `tmp_path` is imported to register the shim-safe pytest fixture defined in fixtures.py;
# each test's parameter of the same name is the injected value, not a redefinition.
"""Data mutations M0-M9 (M9 split into a/b), each driven through the REAL CLI.

Every scenario builds a real SQLite file, runs `python -m evals.telemetry` as a subprocess and
asserts the process exit code plus the disclosures in stdout. The transcripts pasted into
`docs/QA.md` come from these exact fixtures. Nothing here asserts SQL or Makefile text.

M3 (3a) and M4 (3b) each have their OWN `--allow-inconclusive` test: they must give 0 and 3
respectively. M7 pins the ledger self-join + distinct(item_id). M9b is the ONLY assertion with
teeth against "green while degraded".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from evals.telemetry.source import SqliteEventSource
from evals.telemetry.tests import fixtures as fx
from evals.telemetry.tests.fixtures import tmp_path  # noqa: F401 - pytest fixture


def _db(tmp_path: Path, label: str, *, with_events_table: bool = True) -> tuple[fx.Db, Path]:
    path = tmp_path / f"{label}.db"
    return fx.make_db(path, with_events_table=with_events_table), path


def _gate(path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return fx.run_cli(path, "--since", "7d", "--gate", *extra)


# -----------------------------------------------------------------------------------------
# M0 - the honest baseline: thirty complete items across three sellers, all eight rows PASS.
# -----------------------------------------------------------------------------------------
def test_m0_baseline_is_green_and_reports_zero_refund_rows(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m0")
    fx.populate_baseline(db)
    fx.close(db)
    result = _gate(path)
    assert result.returncode == 0
    assert "PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0" in result.stdout
    assert "excluded_refunded=0 (n=0 refund rows)" in result.stdout
    assert "REFUND_ATTRIBUTION=INTACT" in result.stdout


# -----------------------------------------------------------------------------------------
# M1 - a threshold breached on sufficient sample is FAIL (1), never downgraded.
# -----------------------------------------------------------------------------------------
def test_m1_threshold_breach_is_fail_and_never_downgraded(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m1")
    fx.populate_baseline(db, prettify_latency_ms=25_000)
    fx.close(db)
    result = _gate(path)
    assert result.returncode == 1
    assert "FAIL=1" in result.stdout
    assert "prettify_p50" in result.stdout and "25000 ms" in result.stdout
    allowed = _gate(path, "--allow-inconclusive")
    assert allowed.returncode == 1
    assert "FAIL=1" in allowed.stdout


# -----------------------------------------------------------------------------------------
# M2 - insufficient sample is INCONCLUSIVE (2); the flag downgrades ONLY this to 0.
# -----------------------------------------------------------------------------------------
def test_m2_insufficient_sample_is_inconclusive_and_downgrades(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m2")
    fx.populate_baseline(db, count=5)
    fx.close(db)
    result = _gate(path)
    assert result.returncode == 2
    assert "INCONCLUSIVE=8" in result.stdout
    assert "UNMEASURED (n=5 < 30)" in result.stdout
    allowed = _gate(path, "--allow-inconclusive")
    assert allowed.returncode == 0
    assert "INCONCLUSIVE=8" in allowed.stdout


# -----------------------------------------------------------------------------------------
# M3 - source absent (3a). Separate test: --allow-inconclusive MUST give 0.
# -----------------------------------------------------------------------------------------
def test_m3_3a_is_three_and_downgrades_to_zero(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m3", with_events_table=False)
    fx.close(db)
    assert _gate(path).returncode == 3
    allowed = _gate(path, "--allow-inconclusive")
    assert allowed.returncode == 0
    assert "source_state=SOURCE_ABSENT_3A" in allowed.stdout
    assert "GATE NOT EVALUATED — n=0. This is NOT a pass." in allowed.stdout


# -----------------------------------------------------------------------------------------
# M4 - source unusable (3b). Separate test: --allow-inconclusive MUST still give 3.
# -----------------------------------------------------------------------------------------
def test_m4_3b_is_three_and_never_downgrades(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m4")
    fx.populate_baseline(db)
    db.event(
        name="prettify_approved",
        item_id="tee",
        decision="approve",
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
    )
    fx.close(db)
    assert _gate(path).returncode == 3
    allowed = _gate(path, "--allow-inconclusive")
    assert allowed.returncode == 3
    assert "source_state=SOURCE_UNUSABLE_3B" in allowed.stdout


# -----------------------------------------------------------------------------------------
# M5 - per-metric excluded_rate > 0.20 is DATA_UNAVAILABLE (3), never a greener number.
# -----------------------------------------------------------------------------------------
def test_m5_excluded_rate_over_twenty_percent_is_data_unavailable(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m5")
    for index in range(1, 31):
        db.full_item(index, with_export_clicked=(index <= 20))
    fx.close(db)
    result = _gate(path)
    assert result.returncode == 3
    assert "DATA_UNAVAILABLE=3" in result.stdout
    assert "DATA_UNAVAILABLE (excluded_rate=0.33 > 0.20)" in result.stdout
    assert _gate(path, "--allow-inconclusive").returncode == 3


# -----------------------------------------------------------------------------------------
# M6 - soft-deleted items KEEP their events; the historical rate must not shrink.
# -----------------------------------------------------------------------------------------
def test_m6_soft_delete_keeps_the_history_rate(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m6")
    fx.populate_baseline(db)
    db.connection.execute(
        "update items set deleted_at = ? where id in (select id from items limit 10)",
        (fx.IN_WINDOW,),
    )
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert len(data.deleted_items) == 10
    assert len(data.events) == 30 * 4
    result = _gate(path)
    assert result.returncode == 0
    assert "population=30 items (from 3 sellers)" in result.stdout
    assert "PASS=8" in result.stdout


# -----------------------------------------------------------------------------------------
# M7 - ledger self-join + distinct(item_id): 3 refund jobs on 1 item -> excluded_refunded=1.
# -----------------------------------------------------------------------------------------
def test_m7_ledger_self_join_counts_distinct_items(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m7")
    fx.populate_baseline(db, count=31)
    item_id = fx.item_uuid(31)
    for offset in range(1, 4):
        job_id = fx.job_uuid(500 + offset)
        db.ledger_enqueued(job_id, item_id)
        db.ledger_refunded(job_id)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.refund_rows_total == 3
    assert data.attribution.excluded_item_ids == frozenset({item_id})
    result = _gate(path)
    assert result.returncode == 0
    assert "excluded_refunded=1 (n=3 refund rows, distinct item_id)" in result.stdout


# -----------------------------------------------------------------------------------------
# M8 - hard delete cascades jobs but leaves events + ledger; orphan is NOT excluded.
# -----------------------------------------------------------------------------------------
def test_m8_hard_deleted_orphan_is_reported_not_excluded(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m8")
    fx.populate_baseline(db, count=31)
    item_id = fx.item_uuid(31)
    for offset in range(1, 4):
        job_id = fx.job_uuid(600 + offset)
        db.job(600 + offset, item_id, state="failed", reason_code="bad_photo")
        db.ledger_enqueued(job_id, item_id)
        db.ledger_refunded(job_id)
    db.hard_delete(item_id)  # real FK cascade removes the jobs rows
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert item_id in data.orphan_item_ids
    assert item_id not in data.failed_items
    assert data.attribution.excluded_item_ids == frozenset({item_id})
    result = _gate(path)
    assert result.returncode == 0
    assert "orphan_item_ids=1" in result.stdout
    assert "failure_mix_unattributable=1" in result.stdout
    assert "excluded_refunded=1 (n=3 refund rows, distinct item_id)" in result.stdout


# -----------------------------------------------------------------------------------------
# M9a - ALL join keys NULL -> the source_state guard fires; --gate=3 even with the flag.
# -----------------------------------------------------------------------------------------
def test_m9a_all_join_keys_null_never_goes_green(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m9a")
    fx.populate_baseline(db)
    for _ in range(3):
        db.ledger_enqueued(None, None)
        db.ledger_refunded(None)
    fx.close(db)
    strict = _gate(path)
    assert strict.returncode == 3
    assert "source_state=SOURCE_UNUSABLE_3B" in strict.stdout
    assert "refund_attribution=degraded(n=3)" in strict.stdout
    assert "excluded_refunded=UNMEASURED (n=3 refund rows unattributable)" in strict.stdout
    assert "excluded_refunded=0" not in strict.stdout
    allowed = _gate(path, "--allow-inconclusive")
    assert allowed.returncode == 3, "3b is never downgradable"
    assert "source_state=SOURCE_UNUSABLE_3B" in allowed.stdout


# -----------------------------------------------------------------------------------------
# M9b - 0 < rate <= 0.20 is DEGRADED, source_state stays OK, the exit code comes from the
# eight gated rows (here a legitimate 0) BUT the report MUST disclose the degradation.
# This is the only assertion with teeth against "green while degraded".
# -----------------------------------------------------------------------------------------
def test_m9b_green_while_degraded_discloses_everywhere(tmp_path: Path) -> None:
    db, path = _db(tmp_path, "m9b")
    fx.populate_baseline(db)
    for offset in range(1, 10):
        job_id = fx.job_uuid(700 + offset)
        db.ledger_enqueued(job_id, fx.item_uuid(700 + offset))
        db.ledger_refunded(job_id)
    db.ledger_refunded(None)
    fx.close(db)
    data = SqliteEventSource(path).load()
    assert data.attribution.status.value == "DEGRADED"
    assert data.attribution.missing_rate == 0.10

    result = _gate(path)
    assert result.returncode == 0, "exit code is the eight rows' verdict, not a forced 2"
    assert "source_state=" not in result.stdout, "source_state() is still OK"
    assert "refund_attribution=degraded(n=1)" in result.stdout
    assert "UNMEASURED" in result.stdout
    assert "VERDICTS:" in result.stdout
    assert "REFUND_ATTRIBUTION=DEGRADED(n=1)" in result.stdout
    assert "excluded_refunded=0" not in result.stdout, "never a bare zero while degraded"
    assert "excluded_refunded=UNMEASURED (n=1 refund rows unattributable)" in result.stdout
