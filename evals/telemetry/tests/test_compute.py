"""Pure-function tests for the functional core. T02: `compute.py` has tests before it is used
by the shell. `now` is always injected; no database and no clock read happens in this module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from evals.telemetry.compute import (
    activation,
    arithmetic_mean,
    build_attribution,
    build_timelines,
    completeness_of,
    excluded_rate,
    in_window,
    is_uuid_shaped,
    nearest_rank,
    parse_now,
    seller_key,
    source_state,
    verdict_for,
    window_bounds,
)
from evals.telemetry.model import (
    AttributionStatus,
    EventRow,
    MetricResult,
    SourceData,
    SourceState,
    Verdict,
)
from evals.telemetry.tests.fixtures import (
    BEFORE_WINDOW,
    IN_WINDOW,
    NOW,
    NOW_DT,
    WINDOW_START,
    item_uuid,
    subject,
)


def _event(**overrides: object) -> EventRow:
    base: dict[str, object] = dict(
        event_id="e1",
        event="prettify_approved",
        received_at=IN_WINDOW,
        client_ts=IN_WINDOW,
        session_id="s1",
        subject=subject(0),
        app_version="1.4.0",
        platform="ios",
        item_id=item_uuid(1),
    )
    base.update(overrides)
    return EventRow(**base)  # type: ignore[arg-type]


def test_window_is_half_open_on_utc_received_at() -> None:
    start, end = window_bounds(NOW_DT)
    assert start == WINDOW_START
    assert end == NOW
    assert in_window(IN_WINDOW, start, end)
    assert not in_window(NOW, start, end), "window end must be EXCLUSIVE"
    assert not in_window(BEFORE_WINDOW, start, end)


def test_window_end_is_exactly_seven_days_after_start() -> None:
    start, end = window_bounds(NOW_DT)
    assert parse_now(end) - parse_now(start) == timedelta(days=7)


def test_nearest_rank_matches_the_pinned_examples() -> None:
    assert nearest_rank([1, 2, 3, 4], 0.50) == 2
    assert nearest_rank(list(range(1, 31)), 0.90) == 27
    with pytest.raises(ValueError):
        nearest_rank([], 0.5)


def test_mean_is_plain_arithmetic_and_refuses_empty() -> None:
    assert arithmetic_mean([1, 2, 3, 4]) == 2.5
    with pytest.raises(ValueError):
        arithmetic_mean([])


def test_excluded_rate_uses_the_item_denominator_and_handles_zero() -> None:
    assert excluded_rate(n=24, excluded=6) == 0.20
    assert excluded_rate(n=20, excluded=10) == pytest.approx(1 / 3)
    assert excluded_rate(n=0, excluded=0) == 0.0


def test_verdict_for_uses_one_strict_judgement_in_one_direction() -> None:
    kwargs = dict(value=100.0, threshold=200.0, n=30, min_n=30, rate=0.0)
    assert verdict_for(**kwargs) is Verdict.PASS
    assert verdict_for(**{**kwargs, "value": 201.0}) is Verdict.FAIL
    # Strict `>`: exactly at the threshold is NOT a failure, and exactly 0.20 is NOT a 3b.
    assert verdict_for(**{**kwargs, "value": 200.0}) is Verdict.PASS
    assert verdict_for(**{**kwargs, "rate": 0.20}) is Verdict.PASS
    assert verdict_for(**{**kwargs, "rate": 0.21}) is Verdict.DATA_UNAVAILABLE
    assert verdict_for(**{**kwargs, "n": 29}) is Verdict.INCONCLUSIVE
    # The mirror `completeness` is never an input: there is no completeness parameter at all.
    assert "completeness" not in verdict_for.__code__.co_varnames


def test_completeness_is_only_ever_a_mirror_value() -> None:
    metrics = (
        MetricResult(
            name="tooltime_p50",
            n=30,
            sellers=3,
            value=100.0,
            threshold=35_000.0,
            unit="ms",
            min_n=30,
            excluded_rate=0.06,
            verdict=Verdict.PASS,
        ),
    )
    assert completeness_of(metrics) == pytest.approx(0.94)


def test_source_state_is_a_separate_layer_from_verdict_for() -> None:
    absent = SourceData(present=False, events_table=False)
    assert source_state(absent) is SourceState.SOURCE_ABSENT_3A
    assert source_state(SourceData(present=True, events_table=True)) is SourceState.OK
    corrupt = SourceData(present=True, events_table=True, unparseable_rows=1)
    assert source_state(corrupt) is SourceState.SOURCE_UNUSABLE_3B
    degraded = SourceData(
        present=True,
        events_table=True,
        attribution=build_attribution(
            refund_rows_total=10,
            refund_rows_attributable=9,
            unattributable=1,
            excluded_item_ids=frozenset(),
        ),
    )
    # 0 < rate <= 0.20 is a DEGRADED disclosure, NOT a 3b.
    assert degraded.attribution.missing_rate == 0.10
    assert source_state(degraded) is SourceState.OK
    unusable = SourceData(
        present=True,
        events_table=True,
        attribution=build_attribution(
            refund_rows_total=10,
            refund_rows_attributable=5,
            unattributable=5,
            excluded_item_ids=frozenset(),
        ),
    )
    assert source_state(unusable) is SourceState.SOURCE_UNUSABLE_3B


def test_attribution_zero_refund_rows_is_vacuously_intact_without_division() -> None:
    result = build_attribution(
        refund_rows_total=0,
        refund_rows_attributable=0,
        unattributable=0,
        excluded_item_ids=frozenset(),
    )
    assert result.status is AttributionStatus.INTACT
    assert result.missing_rate == 0.0
    assert result.label == "intact"


def test_attribution_degraded_carries_the_row_count() -> None:
    result = build_attribution(
        refund_rows_total=10,
        refund_rows_attributable=7,
        unattributable=3,
        excluded_item_ids=frozenset({"a"}),
    )
    assert result.status is AttributionStatus.DEGRADED
    assert result.label == "degraded(n=3)"
    assert result.missing_rate == 0.30


def test_uuid_shape_never_coesces() -> None:
    assert is_uuid_shaped(item_uuid(1))
    assert is_uuid_shaped(None) is False
    assert is_uuid_shaped("tee") is False
    # item_uuid() uses only digits, so its .upper() is a no-op; use a hex string WITH letters
    # to prove the shape check rejects upper-case rather than silently coercing it.
    assert is_uuid_shaped("abcdef01-0000-4000-8000-000000000000".upper()) is False, (
        "upper case is corruption, not spelling"
    )
    assert is_uuid_shaped("Z" * 36) is False


def test_seller_key_is_the_uuid_suffix_for_both_anon_and_usr() -> None:
    assert seller_key(f"usr_{item_uuid(2)}") == item_uuid(2)
    assert seller_key(f"anon_{item_uuid(2)}") == item_uuid(2)
    assert seller_key("opaque") == "opaque"


def test_tool_time_sums_retries_but_point_attribute_takes_latest() -> None:
    item = item_uuid(7)
    start, end = window_bounds(NOW_DT)
    events = (
        _event(event_id="a", item_id=item, decision="retry", prettify_latency_ms=1_000,
               attrs_latency_ms=500),
        _event(event_id="b", item_id=item, decision="retry", prettify_latency_ms=2_000,
               attrs_latency_ms=500),
        _event(event_id="c", item_id=item, decision="approve", prettify_latency_ms=3_000,
               attrs_latency_ms=500),
        _event(event_id="d", item_id=item, event="export_clicked", export_latency_ms=1_111),
        _event(event_id="e", item_id=item, event="export_delivered", delivery_latency_ms=9_999),
    )
    timeline = build_timelines(events, start, end)[item]
    assert timeline.retry_count == 2
    # SUM across retries: 1000 + 2000 + 3000, not the last 3000 (PLAN:136-146).
    assert timeline.prettify_sum_ms == 6_000
    # The point attribute (prettify_p50/p90 gate) takes the LATEST value = 3000.
    assert timeline.prettify_latency_ms == 3_000
    # PLAN:124 reconciliation: third term comes from export_clicked (1111), NOT the delivery
    # latency (9999), and delivery is not a prerequisite.
    assert timeline.export_latency_ms == 1_111
    assert timeline.tool_time_ms == 6_000 + 500 + 1_111


def test_tool_time_is_none_when_no_attempt_was_approved() -> None:
    item = item_uuid(8)
    start, end = window_bounds(NOW_DT)
    events = (
        _event(event_id="a", item_id=item, decision="retry", prettify_latency_ms=1_000,
               attrs_latency_ms=500),
        _event(event_id="b", item_id=item, event="export_clicked", export_latency_ms=1_000),
    )
    timeline = build_timelines(events, start, end)[item]
    assert timeline.approved is False
    assert timeline.tool_time_ms is None


def test_build_timelines_is_one_row_per_item_not_per_event() -> None:
    start, end = window_bounds(NOW_DT)
    events = tuple(
        _event(event_id=f"e{i}", decision="approve", prettify_latency_ms=1_000 + i,
               attrs_latency_ms=100)
        for i in range(5)
    )
    assert len(build_timelines(events, start, end)) == 1


def test_activation_counts_export_delivered_distinct_items_per_seller() -> None:
    start, end = window_bounds(NOW_DT)
    seller_a, seller_b = subject(1), subject(2)
    events = []
    for i in range(1, 4):
        events.append(
            _event(
                event_id=f"a{i}", event="export_delivered", item_id=item_uuid(i),
                subject=seller_a,
            )
        )
    # A second delivered event for the same item must NOT count twice (distinct item_id).
    events.append(
        _event(event_id="a-dup", event="export_delivered", item_id=item_uuid(1),
               subject=seller_a)
    )
    events.append(
        _event(event_id="b1", event="export_delivered", item_id=item_uuid(9),
               subject=seller_b)
    )
    # export_clicked must NOT count: activation is delivered, not attempted (CONTRACTS:358).
    events.append(
        _event(event_id="c1", event="export_clicked", item_id=item_uuid(10),
               subject=seller_b)
    )
    data = SourceData(present=True, events_table=True, events=tuple(events))
    n, ratio, text = activation(data, start, end)
    assert n == 2, "n counts sellers"
    assert ratio is None, "n < 20 sellers is UNMEASURED, never a ratio"
    assert "UNMEASURED" in text


def test_parse_now_is_utc_aware() -> None:
    parsed = parse_now(NOW)
    assert parsed.tzinfo is UTC
    assert parsed == datetime(2026, 11, 5, tzinfo=UTC)
