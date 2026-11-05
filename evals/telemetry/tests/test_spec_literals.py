"""Pin every frozen literal as a LITERAL, never derived from the implementation.

PLAN (e):332-333 — changing any number in the (b) gate table requires an ADR. A test that
asserts `threshold == GATE_SPECS[0].threshold` would be an identity and prove nothing; these
assertions write the numbers out explicitly.
"""

from __future__ import annotations

from evals.telemetry import spec
from evals.telemetry.compute import nearest_rank
from evals.telemetry.report import method_label


def test_the_single_exclusion_threshold_is_twenty_percent() -> None:
    assert spec.EXCLUDED_RATE_MAX == 0.20
    # There must be exactly ONE judgement threshold, not a second 0.20 for source_state and
    # not a mirror `completeness >= 0.80` comparison (PLAN:148-150 forbids both).
    assert not hasattr(spec, "COMPLETENESS_MIN"), "completeness is printed, never judged"
    assert "EXCLUDED_RATE_MAX" in spec.__dict__
    judgement_literals = [
        name for name in vars(spec) if name.endswith("_MAX") or name.endswith("_MIN")
    ]
    assert judgement_literals == ["EXCLUDED_RATE_MAX"]


def test_all_eight_gate_thresholds_and_sample_sizes_are_frozen() -> None:
    assert [(g.name, g.threshold, g.min_n, g.unit) for g in spec.GATE_SPECS] == [
        ("prettify_p50", 20_000.0, 30, "ms"),
        ("prettify_p90", 35_000.0, 30, "ms"),
        ("tooltime_p50", 35_000.0, 30, "ms"),
        ("tooltime_p90", 60_000.0, 30, "ms"),
        ("attrs_p90", 8_000.0, 30, "ms"),
        ("export_p90", 5_000.0, 30, "ms"),
        ("fields_mean", 5.0, 30, ""),
        ("fields_p90", 12.0, 30, ""),
    ]
    assert spec.MIN_SAMPLE_ITEMS == 30
    assert spec.ACTIVATION_MIN_SELLERS == 20
    assert spec.ACTIVATION_MIN_ITEMS == 3


def test_report_only_rows_are_exactly_four() -> None:
    assert spec.REPORT_ONLY_ROWS == (
        "fields_p50",
        "activation_7d",
        "export_burst_60m",
        "completeness",
    )


def test_failure_mix_codes_are_exactly_the_four_contract_codes() -> None:
    assert spec.FAILURE_MIX_CODES == (
        "bad_photo",
        "no_garment",
        "low_confidence",
        "vton_rejected",
    )


def test_exit_codes_and_their_priority_rank_map() -> None:
    assert spec.EXIT_PASS == 0
    assert spec.EXIT_FAIL == 1
    assert spec.EXIT_INCONCLUSIVE == 2
    assert spec.EXIT_DATA_ERROR == 3
    # 3 > 1 > 2 > 0 is a PRIORITY, not numeric: max() over the codes would invert 1 and 2.
    assert spec.EXIT_RANK == {3: 30, 1: 20, 2: 10, 0: 0}
    assert spec.EXIT_RANK[1] > spec.EXIT_RANK[2]


def test_only_inconclusive_and_3a_are_downgradable() -> None:
    assert spec.DOWNGRADABLE_STATES == frozenset({"INCONCLUSIVE", "SOURCE_ABSENT_3A"})
    assert "FAIL" not in spec.DOWNGRADABLE_STATES
    assert "SOURCE_UNUSABLE_3B" not in spec.DOWNGRADABLE_STATES
    assert "DATA_UNAVAILABLE" not in spec.DOWNGRADABLE_STATES


def test_window_percentile_and_banner_literals() -> None:
    assert spec.WINDOW_SINCE == "7d"
    assert spec.WINDOW_DAYS == 7
    assert spec.NEAREST_RANK_METHOD == "nearest-rank"
    assert method_label() == "nearest-rank"
    assert spec.VALUE_PLACEHOLDER == "-"
    assert spec.UNMEASURED == "UNMEASURED"
    assert spec.NO_GATE_BANNER == "GATE NOT EVALUATED — n=0. This is NOT a pass."
    assert spec.RECEIVED_AT_FORMAT == "%Y-%m-%dT%H:%M:%S.%fZ"


def test_nearest_rank_is_the_hand_written_definition_not_a_library_quantile() -> None:
    # §12.2(e): p50 of [1,2,3,4] = index ceil(2.0)-1 = 1 -> 2.
    assert nearest_rank([1, 2, 3, 4], 0.50) == 2.0
    # p90 of 30 values = index ceil(27)-1 = 26 -> 27th smallest.
    assert nearest_rank(list(range(1, 31)), 0.90) == 27.0
    assert nearest_rank([5], 0.50) == 5.0
