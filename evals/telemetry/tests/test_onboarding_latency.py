"""A1/A2 latency report: real numbers when measured, UNMEASURED when not."""

from __future__ import annotations

import json
from pathlib import Path

from evals import onboarding_latency as ol
from evals.telemetry.tests import fixtures as fx


def test_no_source_is_unmeasured_not_zero() -> None:
    rows = ol.measure(db="/tmp/snaplist-onboarding-absent.db")
    assert all(row.value is None for row in rows)
    assert all(row.status == "UNMEASURED" for row in rows)
    text = ol.render(rows, now=fx.NOW, window="7d")
    assert "A1 p50 | UNMEASURED" in text
    assert "A2 p90 | UNMEASURED" in text
    assert "| 0 ms |" not in text


def test_a1_uses_approved_prettify_latency(tmp_path: Path) -> None:
    db = fx.make_db(tmp_path / "onboarding-a1.db")
    path = db.path
    fx.populate_baseline(db)
    fx.close(db)

    rows = ol.measure(db=str(path))
    by_name = {row.name: row for row in rows}
    assert by_name["A1 p50"].value == 1000.0
    assert by_name["A1 p50"].status == "PASS"
    assert by_name["A1 p90"].value == 1000.0
    assert by_name["A1 p90"].status == "PASS"
    assert by_name["A2 p90"].value is None  # no gate producer in this build


def test_a1_over_budget_is_a_fail(tmp_path: Path) -> None:
    db = fx.make_db(tmp_path / "onboarding-a1-fail.db")
    path = db.path
    fx.populate_baseline(db, prettify_latency_ms=21_000)
    fx.close(db)

    rows = {row.name: row for row in ol.measure(db=str(path))}
    assert rows["A1 p50"].status == "FAIL"
    assert rows["A1 p90"].status == "PASS"  # 21s is still under the 35s p90 ceiling


def test_a2_needs_explicit_device_samples(tmp_path: Path) -> None:
    assert ol.measure(db=None)[-1].value is None
    fast = ol.measure(db=None, gate_durations=[700.0] * 30)[-1]
    slow = ol.measure(db=None, gate_durations=[900.0] * 30)[-1]
    assert (fast.value, fast.status) == (700.0, "PASS")
    assert (slow.value, slow.status) == (900.0, "FAIL")


def test_a2_strict_boundary_is_800ms(tmp_path: Path) -> None:
    row = ol.measure(db=None, gate_durations=[800.0] * 30)[-1]
    assert row.status == "FAIL"  # ONBOARDING.md says < 800ms, not <=


def test_negative_gate_durations_are_refused() -> None:
    try:
        ol.measure(db=None, gate_durations=[-1.0])
    except ValueError as exc:
        assert "gate durations" in str(exc)
    else:
        raise AssertionError("negative durations must not become a measurement")


def test_cli_dry_run_writes_nothing_and_execute_writes_report(tmp_path: Path) -> None:
    db = fx.make_db(tmp_path / "onboarding-cli.db")
    path = db.path
    fx.populate_baseline(db)
    fx.close(db)
    durations = tmp_path / "gate.json"
    durations.write_text(json.dumps([700.0] * 30))
    report = tmp_path / "reports" / "onboarding-latency.md"

    assert ol.main([
        "--db", str(path), "--dry-run", "--gate-latencies", str(durations),
        "--report", str(report),
    ]) == 0
    assert not report.exists()

    assert ol.main([
        "--db", str(path), "--gate-latencies", str(durations), "--report", str(report),
    ]) == 0
    text = report.read_text()
    assert "| A1 p50 | 1000 ms | <= 20,000 ms | PASS | 30 |" in text
    assert "| A2 p90 | 700 ms | < 800 ms | PASS | 30 |" in text
