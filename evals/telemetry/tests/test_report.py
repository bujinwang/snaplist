# ruff: noqa: F811
# `tmp_path` is imported to register the shim-safe pytest fixture defined in fixtures.py;
# each test's parameter of the same name is the injected value, not a redefinition.
"""Pure-rendering tests for `report.py` (T04).

The report is stdout-only. Every gated row prints three fields (value | threshold | verdict)
plus a cross-unit `n`; `value` is the literal `-` whenever the sample is insufficient, and a
DEGRADED ledger is disclosed in FOUR independent places so a green exit can never hide it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path

from evals.telemetry import spec
from evals.telemetry.compute import verdict_for
from evals.telemetry.gate import build_report
from evals.telemetry.model import GateReport, Verdict
from evals.telemetry.report import render
from evals.telemetry.source import SqliteEventSource
from evals.telemetry.tests import fixtures as fx
from evals.telemetry.tests.fixtures import tmp_path  # noqa: F401 - pytest fixture

_GATE_LINE = re.compile(r"^[a-z0-9_]+\s")


def _report(tmp_path: Path, *, name: str = "dev.db") -> tuple[Path, GateReport]:
    """Build a real baseline report with a pinned window and SHA."""
    path = tmp_path / name
    db = fx.make_db(path)
    fx.populate_baseline(db)
    fx.close(db)
    return path, build_report(SqliteEventSource(path).load(), fx.NOW_DT, sha="test-sha")


def _gate_lines(text: str) -> list[str]:
    """The eight table rows, excluding the column header."""
    lines = [line for line in text.splitlines() if _GATE_LINE.match(line) and " <= " in line]
    return lines


def test_header_carries_population_window_sha_source_backend(tmp_path: Path) -> None:
    path, report = _report(tmp_path)
    text = render(report, colour=False)
    assert "SnapList telemetry gate — E0-T5" in text
    assert "population=30 items (from 3 sellers)" in text
    assert f"window=[{fx.WINDOW_START}, {fx.NOW})" in text
    assert "half-open UTC on received_at" in text
    assert "percentile=nearest-rank" in text
    assert "git_sha=test-sha" in text
    assert f"source=sqlite:{path}" in text
    assert "backend=local-dev" in text
    assert "refund_attribution=sqlite-only" in text


def test_every_gated_row_has_three_fields_and_cross_unit_n(tmp_path: Path) -> None:
    _, report = _report(tmp_path)
    lines = _gate_lines(render(report, colour=False))
    assert len(lines) == 8
    for line in lines:
        assert " <= " in line, "threshold column missing"
        value_column = line[24:38].strip()
        assert value_column == "-" or re.fullmatch(r"-?\d+(?:\.\d+)?(?: ms)?", value_column)
        tail = line[53:]
        assert any(v in tail for v in ("PASS", "FAIL", "UNMEASURED", "DATA_UNAVAILABLE"))
        assert re.search(r"n=\d+ items \(from \d+ sellers\)", tail), "cross-unit count missing"


def test_insufficient_sample_value_is_the_literal_dash(tmp_path: Path) -> None:
    path = tmp_path / "dev.db"
    db = fx.make_db(path)
    fx.populate_baseline(db, count=5)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 2
    lines = _gate_lines(result.stdout)
    assert len(lines) == 8
    for line in lines:
        assert line[24:38].strip() == "-", "no computed number may be published when n < 30"
        assert "UNMEASURED (n=5 < 30)" in line


def test_banner_only_when_every_gated_row_has_zero_n(tmp_path: Path) -> None:
    absent = tmp_path / "absent.db"
    db = fx.make_db(absent, with_events_table=False)
    fx.close(db)
    result = fx.run_cli(absent, "--since", "7d", "--gate")
    assert spec.NO_GATE_BANNER in result.stdout
    assert result.stdout.index(spec.NO_GATE_BANNER) < result.stdout.index("prettify_p50")

    small = tmp_path / "small.db"
    db = fx.make_db(small)
    fx.populate_baseline(db, count=5)
    fx.close(db)
    small_result = fx.run_cli(small, "--since", "7d", "--gate")
    assert spec.NO_GATE_BANNER not in small_result.stdout, "n=5 is measured, not n=0"


def test_ansi_only_when_requested_and_verdict_summary_stays_plain(tmp_path: Path) -> None:
    _, report = _report(tmp_path)
    plain = render(report, colour=False)
    coloured = render(report, colour=True)
    assert "\x1b[" not in plain
    assert "\x1b[32m" in coloured
    verdicts = next(line for line in coloured.splitlines() if line.startswith("VERDICTS:"))
    assert "\x1b[" not in verdicts, "the machine-readable summary is always plain text"


def test_unmeasured_differs_from_pass_in_text_and_colour(tmp_path: Path) -> None:
    _, report = _report(tmp_path)
    mixed = replace(
        report,
        metrics=(
            replace(report.metrics[0], verdict=Verdict.PASS),
            replace(
                report.metrics[1],
                verdict=Verdict.INCONCLUSIVE,
                value=None,
                n=5,
            ),
        ),
    )
    text = render(mixed, colour=True)
    assert "UNMEASURED (n=5 < 30)" in text
    assert "\x1b[32m" in text and "\x1b[33m" in text
    assert "PASS" in text and "UNMEASURED" in text


def test_report_only_rows_are_printed_but_never_in_metrics(tmp_path: Path) -> None:
    _, report = _report(tmp_path)
    assert len(report.metrics) == 8
    assert {metric.name for metric in report.metrics} == {
        gate.name for gate in spec.GATE_SPECS
    }
    assert all(metric.gated for metric in report.metrics)
    text = render(report, colour=False)
    for name in spec.REPORT_ONLY_ROWS:
        assert any(line.startswith(f"{name}=") for line in text.splitlines())


def test_completeness_is_printed_and_never_compared(tmp_path: Path) -> None:
    _, report = _report(tmp_path)
    text = render(report, colour=False)
    completeness_line = next(line for line in text.splitlines() if line.startswith("completeness="))
    assert "mirror of excluded_rate; printed, never judged" in completeness_line
    assert not hasattr(spec, "COMPLETENESS_MIN"), "no second threshold may be named"
    assert "completeness" not in verdict_for.__code__.co_varnames


def test_four_disclosures_when_attribution_is_degraded(tmp_path: Path) -> None:
    path = tmp_path / "dev.db"
    db = fx.make_db(path)
    fx.populate_baseline(db)
    for offset in range(1, 10):
        job_id = fx.job_uuid(800 + offset)
        db.ledger_enqueued(job_id, fx.item_uuid(800 + offset))
        db.ledger_refunded(job_id)
    db.ledger_refunded(None)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    text = result.stdout
    # 1. header
    assert "refund_attribution=degraded(n=1)" in text
    # 2. exclusion line
    assert "excluded_refunded=UNMEASURED (n=1 refund rows unattributable)" in text
    # 3. machine summary
    assert "REFUND_ATTRIBUTION=DEGRADED(n=1)" in text
    assert "VERDICTS:" in text
    # 4. docs/QA.md carries the named residual (asserted by test_makefile_wiring / QA review)
    assert "excluded_refunded=0" not in text, "never print a bare zero while degraded"


def test_intact_zero_refund_rows_prints_the_sample_count(tmp_path: Path) -> None:
    path, report = _report(tmp_path)
    text = render(report, colour=False)
    assert "excluded_refunded=0 (n=0 refund rows)" in text
    assert "REFUND_ATTRIBUTION=INTACT" in text
    assert "refund_attribution=degraded" not in text


def test_failure_mix_and_orphan_lines_are_always_present(tmp_path: Path) -> None:
    path = tmp_path / "dev.db"
    db = fx.make_db(path)
    fx.populate_baseline(db, count=31)
    db.hard_delete(fx.item_uuid(31))
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert "failure_mix_unattributable=1" in result.stdout
    assert "orphan_item_ids=1" in result.stdout
    assert re.search(r"failure_mix: bad_photo=\d+ no_garment=\d+", result.stdout)


def test_stdout_only_the_database_is_never_written(tmp_path: Path) -> None:
    path, _ = _report(tmp_path)
    before = hashlib.md5(path.read_bytes()).hexdigest()
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("SnapList telemetry gate — E0-T5")
    assert hashlib.md5(path.read_bytes()).hexdigest() == before, "report wrote to the database"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dev.db"], "report wrote a file"
