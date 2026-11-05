"""Pure functional core for the E0-T5 gate. Zero I/O; `now` is always injected.

Two independent layers live here, and they must stay independent (PLAN:148-155 / :250):

* `verdict_for()` decides exactly ONE thing per metric: `excluded_rate > 0.20` (strict `>`),
  with `completeness = 1 - excluded_rate` printed but never compared. The gate's own
  `value > threshold` comparison turns a measured row into PASS/FAIL; that is not a second
  judgement on the exclusion threshold.
* `source_state()` classifies the *data source* (absent / unusable / OK). The
  `attribution_missing_rate` cut uses the SAME `EXCLUDED_RATE_MAX` literal but a DIFFERENT
  denominator (refund ledger rows), so the two rates are separate variables.

All percentiles are hand-written nearest-rank: `index = ceil(p * n) - 1` on ascending values.
No `statistics.quantiles`, no numpy, no third-party dependency.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from evals.telemetry.model import (
    Attribution,
    AttributionStatus,
    EventRow,
    ItemTimeline,
    MetricResult,
    SourceData,
    SourceState,
    Verdict,
)
from evals.telemetry.spec import (
    ACTIVATION_MIN_ITEMS,
    ACTIVATION_MIN_SELLERS,
    EXCLUDED_RATE_MAX,
    FAILURE_MIX_CODES,
    GATE_SPECS,
    MIN_SAMPLE_ITEMS,
    RECEIVED_AT_FORMAT,
    WINDOW_DAYS,
)

# The DB shape CHECK is `length = 36 and not glob '*[^0-9a-f-]*'`. Mirror it exactly and never
# lower()/coerce: an upper-case or hand-typed id is corruption, not a spelling variation.
_UUID_SHAPE = re.compile(r"^[0-9a-f-]{36}$")

# The seven frozen event names (CONTRACTS.md §7). Used only for grouping, never for validation.
_APPROVE_EVENT = "prettify_approved"
_CLICK_EVENT = "export_clicked"
_DELIVER_EVENT = "export_delivered"
_FIELDS_EVENT = "attributes_edited"

# Per-item event names whose `item_id` is required by the contract (ADR-008 forward CHECK).
_ITEM_EVENTS = frozenset(
    {"upload_started", _APPROVE_EVENT, _FIELDS_EVENT, _CLICK_EVENT, _DELIVER_EVENT}
)


def is_uuid_shaped(value: object) -> bool:
    """Return True for a 36-character lowercase-hex-and-hyphen TEXT id. No coercion."""
    return isinstance(value, str) and _UUID_SHAPE.fullmatch(value) is not None


def seller_key(subject: str) -> str:
    """The uuid suffix of `subject` (ADR-003): `anon_<uuid>` and `usr_<uuid>` share a suffix.

    No lower()/strip(): a malformed subject stays malformed and is reported as such.
    """
    head, sep, tail = subject.partition("_")
    return tail if sep else head


def parse_now(text: str) -> datetime:
    """Parse `--now` as a fixed-width ISO-8601 UTC Z timestamp."""
    return datetime.strptime(text, RECEIVED_AT_FORMAT).replace(tzinfo=UTC)


def window_bounds(now: datetime, *, days: int = WINDOW_DAYS) -> tuple[str, str]:
    """Half-open UTC window `[now - days, now)` as two comparable fixed-width strings.

    Filtering/sorting is done on `received_at` ONLY, never `client_ts` (clock skew on a phone).
    """
    end = now.astimezone(UTC)
    start = end - timedelta(days=days)
    return start.strftime(RECEIVED_AT_FORMAT), end.strftime(RECEIVED_AT_FORMAT)


def in_window(received_at: str, start: str, end: str) -> bool:
    """Half-open membership: `start <= received_at < end` (PLAN:167, §12.2(d))."""
    return start <= received_at < end


def nearest_rank(values: Sequence[float], percentile: float) -> float:
    """Nearest-rank percentile on ascending values, 0-based index `ceil(p * n) - 1`.

    p50 of [1,2,3,4] = index 1 -> 2; p90 of 30 values = index 26 -> 27th smallest.
    """
    if not values:
        raise ValueError("nearest_rank needs at least one value")
    ordered = sorted(values)
    index = math.ceil(percentile * len(ordered)) - 1
    if index < 0:
        index = 0
    if index >= len(ordered):
        index = len(ordered) - 1
    return float(ordered[index])


def arithmetic_mean(values: Sequence[float]) -> float:
    """Plain mean; never used as a percentile substitute."""
    if not values:
        raise ValueError("arithmetic_mean needs at least one value")
    return float(sum(values)) / len(values)


def excluded_rate(n: int, excluded: int) -> float:
    """`excluded / (n + excluded)`; 0.0 for an empty population (no division by zero)."""
    population = n + excluded
    return excluded / population if population else 0.0


def verdict_for(
    *,
    value: float | None,
    threshold: float,
    n: int,
    min_n: int,
    rate: float,
) -> Verdict:
    """The single per-metric judgement. Precedence mirrors PLAN:252 `3 > 1 > 2 > 0`.

    `rate > EXCLUDED_RATE_MAX` is the ONLY comparison against the exclusion threshold; the
    mirror `completeness` is never compared. `>` is strict at exactly 0.20.
    """
    if rate > EXCLUDED_RATE_MAX:
        return Verdict.DATA_UNAVAILABLE
    if n < min_n:
        return Verdict.INCONCLUSIVE
    if value is None:
        return Verdict.INCONCLUSIVE
    if value > threshold:
        return Verdict.FAIL
    return Verdict.PASS


def source_state(data: SourceData) -> SourceState:
    """Classify the source BEFORE gate rows are computed (PLAN:250, §12.2(a):706).

    * no file / no `events` table -> 3a (downgradable by --allow-inconclusive);
    * any malformed non-NULL id or join key -> 3b (data corruption, never downgraded);
    * >20 % of refund rows unattributable -> 3b (required property missing);
    * otherwise OK, even when `0 < missing_rate <= 0.20` (DEGRADED is a disclosure, not 3b).
    """
    if not data.present or not data.events_table:
        return SourceState.SOURCE_ABSENT_3A
    if data.unparseable_rows > 0 or data.malformed_event_ids:
        return SourceState.SOURCE_UNUSABLE_3B
    if data.attribution.missing_rate > EXCLUDED_RATE_MAX:
        return SourceState.SOURCE_UNUSABLE_3B
    return SourceState.OK


def build_timelines(events: Sequence[EventRow], start: str, end: str) -> dict[str, ItemTimeline]:
    """One row per `item_id`, never per event (PLAN:137-146, §12.2(d)).

    Latest event per `(item_id, event_name)` wins for point attributes. `tool_time_ms` is the
    explicit exception: it SUMS `prettify_latency_ms` over all `prettify_approved` retries,
    takes `attrs_latency_ms` from the approve attempt, and takes `export_latency_ms` from
    `export_clicked` — `export_delivered` physically cannot carry that column (reverse CHECK in
    `apps/api/repository.py`), and delivery is NOT a prerequisite for tool time. See QA.md's
    PLAN:124 reconciliation.
    """
    by_item: dict[str, list[EventRow]] = defaultdict(list)
    for event in events:
        if event.item_id is None or not in_window(event.received_at, start, end):
            continue
        by_item[event.item_id].append(event)

    timelines: dict[str, ItemTimeline] = {}
    for item_id, rows in by_item.items():
        ordered = sorted(rows, key=lambda row: (row.received_at, row.event_id))
        latest: dict[str, EventRow] = {}
        for row in ordered:
            latest[row.event] = row

        attempts = [row for row in ordered if row.event == _APPROVE_EVENT]
        approve_rows = [row for row in attempts if row.decision == "approve"]
        approved = bool(approve_rows)
        approve = approve_rows[-1] if approve_rows else None

        # SUM across retries, skipping NULL latencies; None if there were no attempts at all.
        prettify_values = [
            float(row.prettify_latency_ms)
            for row in attempts
            if row.prettify_latency_ms is not None
        ]
        prettify_sum: float | None = sum(prettify_values) if prettify_values else None

        # Point attribute for the prettify_p50/p90 gates: the LATEST attempt wins.
        latest_approve = latest.get(_APPROVE_EVENT)
        point_prettify = (
            float(latest_approve.prettify_latency_ms)
            if latest_approve is not None and latest_approve.prettify_latency_ms is not None
            else None
        )
        attrs = (
            float(approve.attrs_latency_ms)
            if approve is not None and approve.attrs_latency_ms is not None
            else None
        )

        click = latest.get(_CLICK_EVENT)
        export = (
            float(click.export_latency_ms)
            if click is not None and click.export_latency_ms is not None
            else None
        )

        fields = latest.get(_FIELDS_EVENT)
        fields_touched = (
            float(fields.fields_touched)
            if fields is not None and fields.fields_touched is not None
            else None
        )

        tool_time: float | None = None
        if approved and attrs is not None and export is not None and prettify_sum is not None:
            tool_time = prettify_sum + attrs + export

        timelines[item_id] = ItemTimeline(
            item_id=item_id,
            subject=ordered[-1].subject,
            received_at=ordered[-1].received_at,
            prettify_latency_ms=point_prettify,
            prettify_sum_ms=prettify_sum,
            attrs_latency_ms=attrs,
            export_latency_ms=export,
            fields_touched=fields_touched,
            tool_time_ms=tool_time,
            decision=approve.decision if approve is not None else None,
            approved=approved,
            delivered=_DELIVER_EVENT in latest,
            retry_count=max(0, len(attempts) - 1),
        )
    return timelines


def _measurement(
    timelines: dict[str, ItemTimeline], attribute: str
) -> tuple[list[float], int]:
    """Collect one value per item for a metric, returning values and the excluded-item count.

    An item whose required attribute is NULL is an *incomplete* row and counts as excluded, not
    as a smaller sample that quietly looks greener (PLAN:133-135).
    """
    values: list[float] = []
    missing = 0
    for timeline in timelines.values():
        candidate = getattr(timeline, attribute)
        if candidate is None:
            missing += 1
        else:
            values.append(float(candidate))
    return values, missing


def _seller_count(timelines: dict[str, ItemTimeline], values: Sequence[float]) -> int:
    """Cross-unit count printed beside `n`: how many sellers produced these items."""
    if not values:
        return 0
    return len({seller_key(timeline.subject) for timeline in timelines.values()})


def _eligible(
    timelines: dict[str, ItemTimeline], data: SourceData
) -> dict[str, ItemTimeline]:
    """Remove only POSITIVE evidence, never an item merely because `items` has no row.

    Bert ruling 1: a soft-deleted item still owns its events (history must not shrink). A
    hard-deleted orphan is NOT excluded; it is reported separately as `orphan_item_ids`. The
    only removals are a surviving `jobs` row with a contract reason code and a refunded item
    recovered from the `usage_events` ledger self-join.
    """
    removed = set(data.failed_items) | set(data.attribution.excluded_item_ids)
    return {
        item_id: timeline
        for item_id, timeline in timelines.items()
        if item_id not in removed
    }


def compute_metrics(
    data: SourceData,
    start: str,
    end: str,
) -> tuple[tuple[MetricResult, ...], tuple[ItemTimeline, ...]]:
    """Compute all eight gated rows. Every row is computed before any aggregation (no short
    circuit). Exclusions are counted per metric and never silently dropped (PLAN:171-172).
    """
    timelines = build_timelines(data.events, start, end)
    eligible = _eligible(timelines, data)

    attribute_for_gate = {
        "prettify_p50": "prettify_latency_ms",
        "prettify_p90": "prettify_latency_ms",
        "attrs_p90": "attrs_latency_ms",
        "export_p90": "export_latency_ms",
        "tooltime_p50": "tool_time_ms",
        "tooltime_p90": "tool_time_ms",
        "fields_mean": "fields_touched",
        "fields_p90": "fields_touched",
    }
    percentile_for_gate = {
        "prettify_p50": 0.50,
        "prettify_p90": 0.90,
        "attrs_p90": 0.90,
        "export_p90": 0.90,
        "tooltime_p50": 0.50,
        "tooltime_p90": 0.90,
        "fields_mean": None,
        "fields_p90": 0.90,
    }

    results: list[MetricResult] = []
    for spec in GATE_SPECS:
        attribute = attribute_for_gate[spec.name]
        percentile = percentile_for_gate[spec.name]
        values, missing = _measurement(eligible, attribute)
        n = len(values)
        explicitly_removed = len(timelines) - len(eligible)
        rate = excluded_rate(n, missing + explicitly_removed)
        if not values:
            measured = None
        elif percentile is None:
            measured = arithmetic_mean(values)
        else:
            measured = nearest_rank(values, percentile)
        verdict = verdict_for(
            value=measured, threshold=spec.threshold, n=n, min_n=spec.min_n, rate=rate
        )
        # `value` must be the literal `-` whenever n is insufficient, INDEPENDENTLY of the
        # verdict: a DATA_UNAVAILABLE row caused by a high exclusion rate must not publish a
        # number computed from a sample we just called too small (§12.2(f):776-788).
        visible_value = measured if n >= spec.min_n and measured is not None else None
        results.append(
            MetricResult(
                name=spec.name,
                n=n,
                sellers=_seller_count(eligible, values),
                value=visible_value,
                threshold=spec.threshold,
                unit=spec.unit,
                min_n=spec.min_n,
                excluded_rate=rate,
                verdict=verdict,
                gated=True,
            )
        )
    return tuple(results), tuple(timelines.values())


def activation(data: SourceData, start: str, end: str) -> tuple[int, float | None, str]:
    """Report-only north star: sellers with >=3 distinct `export_delivered` item ids divided by
    sellers with >=1, `n` in SELLERS (min 20). PLAN:281 gate table + CONTRACTS:358-361; the
    §12.2(g) draft that used `export_clicked` is void.
    """
    per_seller: dict[str, set[str]] = defaultdict(set)
    for event in data.events:
        if (
            event.event != _DELIVER_EVENT
            or event.item_id is None
            or not in_window(event.received_at, start, end)
        ):
            continue
        per_seller[seller_key(event.subject)].add(event.item_id)

    denominator = len(per_seller)
    numerator = sum(1 for items in per_seller.values() if len(items) >= ACTIVATION_MIN_ITEMS)
    if denominator < ACTIVATION_MIN_SELLERS:
        return denominator, None, f"UNMEASURED (n={denominator} sellers < 20)"
    return denominator, numerator / denominator, f"{numerator / denominator:.4f}"


def population(data: SourceData, start: str, end: str) -> tuple[int, int]:
    """Header cross-unit count: items and sellers actually observed in the window."""
    items = {
        event.item_id
        for event in data.events
        if event.item_id is not None and in_window(event.received_at, start, end)
    }
    sellers = {
        seller_key(event.subject)
        for event in data.events
        if in_window(event.received_at, start, end)
    }
    return len(items), len(sellers)


def completeness_of(metrics: Sequence[MetricResult]) -> float | None:
    """Mirror of the tool-time `excluded_rate`. PRINTED, never compared (PLAN:135/:148-150).

    Returns None when the tool-time row has no measured sample: printing `1.0000` for an empty
    population would read as "perfect completeness" when nothing was measured at all.
    """
    for metric in metrics:
        if metric.name == "tooltime_p50":
            return 1.0 - metric.excluded_rate if metric.n > 0 else None
    return None


def report_only_lines(
    data: SourceData,
    metrics: Sequence[MetricResult],
    start: str,
    end: str,
) -> tuple[str, ...]:
    """The four report-only rows. None of them participates in exit-code aggregation."""
    p50_values, _ = _measurement(
        _eligible(build_timelines(data.events, start, end), data),
        "fields_touched",
    )
    fields_p50 = f"{nearest_rank(p50_values, 0.50):g}" if p50_values else "-"

    activation_n, _, activation_text = activation(data, start, end)
    completeness = completeness_of(metrics)
    completeness_text = f"{completeness:.4f}" if completeness is not None else "-"

    return (
        f"fields_p50={fields_p50} (report only)",
        f"activation_7d={activation_text}, n={activation_n} sellers (reported, not gated)",
        "export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)",
        f"completeness={completeness_text} (mirror of excluded_rate; printed, never judged)",
    )


def build_attribution(
    *,
    refund_rows_total: int,
    refund_rows_attributable: int,
    unattributable: int,
    excluded_item_ids: frozenset[str],
) -> Attribution:
    """Compute the DEGRADED/INTACT status from ledger-row counts (never from a gate's rate)."""
    if refund_rows_total == 0:
        return Attribution(
            status=AttributionStatus.INTACT,
            refund_rows_total=0,
            refund_rows_attributable=0,
            unattributable=0,
            missing_rate=0.0,
            excluded_item_ids=excluded_item_ids,
        )
    missing_rate = unattributable / refund_rows_total
    status = (
        AttributionStatus.DEGRADED if unattributable > 0 else AttributionStatus.INTACT
    )
    return Attribution(
        status=status,
        refund_rows_total=refund_rows_total,
        refund_rows_attributable=refund_rows_attributable,
        unattributable=unattributable,
        missing_rate=missing_rate,
        excluded_item_ids=excluded_item_ids,
    )


def failure_mix_counts(data: SourceData) -> dict[str, int]:
    """Failure mix keyed by the four contract reason codes, plus the honest residual."""
    counts = {code: data.failure_mix.get(code, 0) for code in FAILURE_MIX_CODES}
    return counts


def all_rows_evaluated(metrics: Sequence[MetricResult]) -> bool:
    """True when at least one gated row had n > 0 (drives the NOT-a-pass banner)."""
    return any(metric.n > 0 for metric in metrics)


def min_sample() -> int:
    """The single minimum-sample literal for the eight gated rows."""
    return MIN_SAMPLE_ITEMS
