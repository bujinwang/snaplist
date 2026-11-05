# ruff: noqa: F811
# `tmp_path` is imported to register the shim-safe pytest fixture defined in fixtures.py;
# each test's parameter of the same name is the injected value, not a redefinition.
"""The four exit codes (T04). `gate.py` is the ONLY module that knows them.

Precedence `3 > 1 > 2 > 0` is a PRIORITY, not a numeric ordering: `max()` over the integer
codes would let an inconclusive (2) outrank a real regression (1). Real CLI subprocess exit
codes prove the behaviour; synthetic `GateReport`s prove the aggregation arithmetic (a mixed
FAIL + INCONCLUSIVE population cannot be manufactured from complete fixture rows without also
tripping the exclusion guard, so both kinds of evidence are needed).
"""

from __future__ import annotations

from pathlib import Path

from evals.telemetry.gate import aggregate_exit, exit_code
from evals.telemetry.model import (
    Attribution,
    GateReport,
    MetricResult,
    SourceState,
    Verdict,
)
from evals.telemetry.spec import (
    EXIT_DATA_ERROR,
    EXIT_FAIL,
    EXIT_INCONCLUSIVE,
    EXIT_PASS,
)
from evals.telemetry.tests import fixtures as fx
from evals.telemetry.tests.fixtures import tmp_path  # noqa: F401 - pytest fixture


def _metric(index: int, verdict: Verdict) -> MetricResult:
    """One synthetic gated row with a sufficient sample."""
    return MetricResult(
        name=f"gate_{index}",
        n=30,
        sellers=3,
        value=100.0,
        threshold=200.0,
        unit="ms",
        min_n=30,
        excluded_rate=0.0,
        verdict=verdict,
        gated=True,
    )


def _report(
    verdicts: tuple[Verdict, ...],
    *,
    state: SourceState = SourceState.OK,
    report_only: tuple[str, ...] = (),
) -> GateReport:
    """A fully-specified report whose only exit contributions are `verdicts` and `state`."""
    return GateReport(
        metrics=tuple(_metric(index, verdict) for index, verdict in enumerate(verdicts)),
        report_only=report_only,
        source_state=state,
        refund_attribution=Attribution(),
        completeness=None,
        failure_mix={},
        failure_mix_unattributable=0,
        orphan_item_ids=(),
        excluded_refunded=0,
        window_start=fx.WINDOW_START,
        window_end=fx.NOW,
        population_items=30,
        population_sellers=3,
        git_sha="test-sha",
        source_label="sqlite:test",
        backend="local-dev",
        percentile_method="nearest-rank",
        banner=None,
    )


def _all(verdict: Verdict, count: int = 8) -> tuple[Verdict, ...]:
    return (verdict,) * count


def _baseline(tmp_path: Path, *, name: str = "dev.db", **kwargs: object) -> Path:
    path = tmp_path / name
    db = fx.make_db(path)
    fx.populate_baseline(db, **kwargs)  # type: ignore[arg-type]
    fx.close(db)
    return path


# -----------------------------------------------------------------------------------------
# Real CLI subprocess exit codes.
# -----------------------------------------------------------------------------------------
def test_report_mode_always_returns_zero_even_on_a_failure(tmp_path: Path) -> None:
    path = _baseline(tmp_path, name="fail.db", prettify_latency_ms=25_000)
    assert fx.run_cli(path, "--since", "7d").returncode == EXIT_PASS
    assert fx.run_cli(path, "--since", "7d", "--gate").returncode == EXIT_FAIL


def test_all_eight_rows_pass_returns_zero(tmp_path: Path) -> None:
    path = _baseline(tmp_path)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == EXIT_PASS
    assert "PASS=8 FAIL=0" in result.stdout


def test_fail_is_one_and_is_never_downgraded(tmp_path: Path) -> None:
    path = _baseline(tmp_path, name="fail.db", prettify_latency_ms=25_000)
    assert fx.run_cli(path, "--since", "7d", "--gate").returncode == EXIT_FAIL
    result = fx.run_cli(path, "--since", "7d", "--gate", "--allow-inconclusive")
    assert result.returncode == EXIT_FAIL
    assert "FAIL=1" in result.stdout


def test_inconclusive_is_two_and_downgrades_to_zero(tmp_path: Path) -> None:
    path = tmp_path / "small.db"
    db = fx.make_db(path)
    fx.populate_baseline(db, count=5)
    fx.close(db)
    strict = fx.run_cli(path, "--since", "7d", "--gate")
    assert strict.returncode == EXIT_INCONCLUSIVE
    assert "INCONCLUSIVE=8" in strict.stdout
    allowed = fx.run_cli(path, "--since", "7d", "--gate", "--allow-inconclusive")
    assert allowed.returncode == EXIT_PASS
    assert "INCONCLUSIVE=8" in allowed.stdout, "a green that says WHY it is green"


def test_m3_source_absent_3a_downgrades_to_zero_with_allow_inconclusive(
    tmp_path: Path,
) -> None:
    """M3, the separate 3a test: 3 -> 0 under --allow-inconclusive."""
    path = tmp_path / "absent.db"
    db = fx.make_db(path, with_events_table=False)
    fx.close(db)
    strict = fx.run_cli(path, "--since", "7d", "--gate")
    assert strict.returncode == EXIT_DATA_ERROR
    assert "source_state=SOURCE_ABSENT_3A" in strict.stdout
    allowed = fx.run_cli(path, "--since", "7d", "--gate", "--allow-inconclusive")
    assert allowed.returncode == EXIT_PASS
    assert "source_state=SOURCE_ABSENT_3A" in allowed.stdout
    assert "GATE NOT EVALUATED" in allowed.stdout


def test_m4_source_unusable_3b_stays_three_with_allow_inconclusive(tmp_path: Path) -> None:
    """M4, the separate 3b test: 3 -> 3 under --allow-inconclusive."""
    path = tmp_path / "corrupt.db"
    db = fx.make_db(path)
    fx.populate_baseline(db)
    db.event(
        name="prettify_approved",
        item_id="tee",
        decision="approve",
        prettify_latency_ms=1_000,
        attrs_latency_ms=1_000,
    )
    fx.close(db)
    for extra in ((), ("--allow-inconclusive",)):
        result = fx.run_cli(path, "--since", "7d", "--gate", *extra)
        assert result.returncode == EXIT_DATA_ERROR
        assert "source_state=SOURCE_UNUSABLE_3B" in result.stdout


def test_non_7d_since_is_unmeasured_and_exits_three(tmp_path: Path) -> None:
    path = _baseline(tmp_path)
    result = fx.run_cli(path, "--since", "30d", "--gate")
    assert result.returncode == EXIT_DATA_ERROR
    assert "UNMEASURED" in result.stderr
    assert "--since '30d'" in result.stderr


# -----------------------------------------------------------------------------------------
# Aggregation arithmetic, computed over ALL contributions before choosing (no short-circuit).
# -----------------------------------------------------------------------------------------
def test_fail_outranks_inconclusive_by_rank_not_numeric_value() -> None:
    report = _report((Verdict.INCONCLUSIVE, Verdict.INCONCLUSIVE, Verdict.FAIL))
    assert aggregate_exit(report, allow_inconclusive=False) == EXIT_FAIL
    assert aggregate_exit(report, allow_inconclusive=True) == EXIT_FAIL


def test_data_error_outranks_fail_and_inconclusive() -> None:
    report = _report(
        (Verdict.INCONCLUSIVE, Verdict.FAIL, Verdict.DATA_UNAVAILABLE, Verdict.PASS)
    )
    assert aggregate_exit(report, allow_inconclusive=False) == EXIT_DATA_ERROR
    assert aggregate_exit(report, allow_inconclusive=True) == EXIT_DATA_ERROR


def test_no_short_circuit_a_late_fail_is_still_seen() -> None:
    report = _report(_all(Verdict.INCONCLUSIVE, 7) + (Verdict.FAIL,))
    assert aggregate_exit(report, allow_inconclusive=False) == EXIT_FAIL
    assert aggregate_exit(report, allow_inconclusive=True) == EXIT_FAIL


def test_source_state_contributes_independently_of_every_verdict() -> None:
    absent = _report(_all(Verdict.PASS), state=SourceState.SOURCE_ABSENT_3A)
    assert aggregate_exit(absent, allow_inconclusive=False) == EXIT_DATA_ERROR
    assert aggregate_exit(absent, allow_inconclusive=True) == EXIT_PASS
    unusable = _report(_all(Verdict.PASS), state=SourceState.SOURCE_UNUSABLE_3B)
    assert aggregate_exit(unusable, allow_inconclusive=False) == EXIT_DATA_ERROR
    assert aggregate_exit(unusable, allow_inconclusive=True) == EXIT_DATA_ERROR


def test_downgrading_3a_never_hides_a_real_fail() -> None:
    """Downgrade happens per contribution BEFORE ranking, not on the single winner."""
    report = _report(
        (Verdict.PASS, Verdict.FAIL), state=SourceState.SOURCE_ABSENT_3A
    )
    assert aggregate_exit(report, allow_inconclusive=False) == EXIT_DATA_ERROR
    assert aggregate_exit(report, allow_inconclusive=True) == EXIT_FAIL


def test_report_only_rows_never_contribute_to_the_exit_code() -> None:
    report = _report(
        _all(Verdict.PASS),
        report_only=("fields_p50=FAIL", "activation_7d=DATA_UNAVAILABLE"),
    )
    assert aggregate_exit(report, allow_inconclusive=False) == EXIT_PASS


def test_exit_code_report_mode_is_always_zero() -> None:
    report = _report(_all(Verdict.FAIL), state=SourceState.SOURCE_UNUSABLE_3B)
    assert exit_code(report, gate=False, allow_inconclusive=False) == EXIT_PASS
    assert exit_code(report, gate=True, allow_inconclusive=True) == EXIT_DATA_ERROR


def test_empty_contribution_list_is_a_pass() -> None:
    assert aggregate_exit(_report(()), allow_inconclusive=False) == EXIT_PASS
