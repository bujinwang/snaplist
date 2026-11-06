"""Pure rendering. The report is stdout-only: nothing is written to disk.

Every gated row prints three fields (value | threshold | verdict) plus `n` with a cross-unit
count. When `n` is insufficient the value is the literal `-`, never a number computed from a
sample we just called insufficient (§12.2(f)). `UNMEASURED` must differ from `PASS` in text AND
colour, and ANSI escapes are emitted only when stdout is a TTY — the `VERDICTS:` machine-readable
summary line is always plain text.

Four disclosures when attribution is DEGRADED (addendum 2, #7 + M9b):
  1. report header        `refund_attribution=degraded(n=<k>)`
  2. exclusion line       `excluded_refunded=UNMEASURED (n=<k> refund rows unattributable)`
  3. machine summary      `REFUND_ATTRIBUTION=DEGRADED(n=<k>)` on the `VERDICTS:` line
  4. `docs/QA.md`         named residual with the PLAN:250 20 % tolerance rationale
A bare `excluded_refunded=0` is NEVER printed while any refund row is unattributable.
"""

from __future__ import annotations

import os
import sys

from evals.telemetry.model import AttributionStatus, GateReport, MetricResult, SourceState, Verdict
from evals.telemetry.spec import (
    EXCLUDED_RATE_MAX,
    NEAREST_RANK_METHOD,
    UNMEASURED,
    VALUE_PLACEHOLDER,
)

_ANSI = {
    Verdict.PASS: "\x1b[32m",
    Verdict.FAIL: "\x1b[31m",
    Verdict.INCONCLUSIVE: "\x1b[33m",
    Verdict.DATA_UNAVAILABLE: "\x1b[35m",
}
_RESET = "\x1b[0m"


def _colour(verdict: Verdict, text: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{_ANSI[verdict]}{text}{_RESET}"


def _value_text(metric: MetricResult) -> str:
    """`-` for insufficient sample; otherwise the measured number with its unit."""
    if metric.value is None:
        return VALUE_PLACEHOLDER
    number = f"{metric.value:g}"
    return f"{number} {metric.unit}".strip()


def _verdict_text(metric: MetricResult) -> str:
    if metric.verdict is Verdict.INCONCLUSIVE:
        return f"{UNMEASURED} (n={metric.n} < {metric.min_n})"
    if metric.verdict is Verdict.DATA_UNAVAILABLE:
        return (
            f"DATA_UNAVAILABLE (excluded_rate={metric.excluded_rate:.2f} "
            f"> {EXCLUDED_RATE_MAX:.2f})"
        )
    return metric.verdict.value


def _threshold_text(metric: MetricResult) -> str:
    return f"<= {metric.threshold:g} {metric.unit}".strip()


def _excluded_refunded_line(report: GateReport) -> str:
    """Never a bare 0 when attribution is degraded; Q4 carries the sample count when intact."""
    attribution = report.refund_attribution
    if attribution.status is AttributionStatus.DEGRADED:
        return (
            f"excluded_refunded={UNMEASURED} "
            f"(n={attribution.unattributable} refund rows unattributable)"
        )
    if attribution.status is AttributionStatus.UNVERIFIED:
        return (
            f"excluded_refunded={UNMEASURED} "
            f"(n={attribution.refund_rows_total} refund rows; join keys unparseable)"
        )
    if attribution.refund_rows_total == 0:
        return "excluded_refunded=0 (n=0 refund rows)"
    return (
        f"excluded_refunded={report.excluded_refunded} "
        f"(n={attribution.refund_rows_total} refund rows, distinct item_id)"
    )


def _verdict_summary(report: GateReport) -> str:
    counts = {verdict: 0 for verdict in Verdict}
    for metric in report.metrics:
        counts[metric.verdict] += 1
    attribution_tag = (
        f"REFUND_ATTRIBUTION=DEGRADED(n={report.refund_attribution.unattributable})"
        if report.refund_attribution.status is AttributionStatus.DEGRADED
        else f"REFUND_ATTRIBUTION={report.refund_attribution.status.value}"
    )
    return (
        f"VERDICTS: PASS={counts[Verdict.PASS]} FAIL={counts[Verdict.FAIL]} "
        f"INCONCLUSIVE={counts[Verdict.INCONCLUSIVE]} "
        f"DATA_UNAVAILABLE={counts[Verdict.DATA_UNAVAILABLE]} {attribution_tag}"
    )


def render(report: GateReport, *, colour: bool | None = None) -> str:
    """Render the whole report to a single string. `colour` defaults to `stdout.isatty()`."""
    if colour is None:
        try:
            colour = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        except (OSError, ValueError):
            colour = False

    lines: list[str] = []
    lines.append("SnapList telemetry gate — E0-T5")
    lines.append(
        f"population={report.population_items} items (from {report.population_sellers} sellers)"
    )
    lines.append(
        f"window=[{report.window_start}, {report.window_end})  half-open UTC on received_at"
    )
    lines.append(f"percentile={report.percentile_method}")
    lines.append(f"git_sha={report.git_sha}")
    lines.append(f"source={report.source_label}")
    lines.append(f"backend={report.backend}")
    _refund_tag = (
        "sqlite-only" if report.backend == "local-dev" else f"{report.backend}-source"
    )
    lines.append(f"refund_attribution={_refund_tag}")
    if report.refund_attribution.status is AttributionStatus.DEGRADED:
        lines.append(f"refund_attribution={report.refund_attribution.label}")
    elif report.refund_attribution.status is AttributionStatus.UNVERIFIED:
        lines.append("refund_attribution=unverified (join keys unparseable)")
    if report.source_state is SourceState.SOURCE_ABSENT_3A:
        lines.append("source_state=SOURCE_ABSENT_3A")
    elif report.source_state is SourceState.SOURCE_UNUSABLE_3B:
        lines.append("source_state=SOURCE_UNUSABLE_3B")

    if report.banner:
        lines.append("")
        lines.append(report.banner)

    lines.append("")
    header = f"{'gate':<16} {'n':>5}  {'value':<14} {'threshold':<14} verdict"
    lines.append(header)
    for metric in report.metrics:
        text = _verdict_text(metric)
        lines.append(
            f"{metric.name:<16} {metric.n:>5}  {_value_text(metric):<14} "
            f"{_threshold_text(metric):<14} "
            f"{_colour(metric.verdict, text, enabled=bool(colour))}  "
            f"n={metric.n} items (from {metric.sellers} sellers)"
        )

    lines.append("")
    for line in report.report_only:
        lines.append(line)
    lines.append(_excluded_refunded_line(report))
    # R1 (docs/QA.md §10): items with events but no surviving jobs row (ADR-008 no-FK + CASCADE).
    # Accepted as a PERMANENT, intentional honesty residual — no schema change, no write path.
    lines.append(f"failure_mix_unattributable={report.failure_mix_unattributable}")
    lines.append(f"orphan_item_ids={len(report.orphan_item_ids)}")
    mix = " ".join(f"{code}={count}" for code, count in report.failure_mix.items())
    lines.append(f"failure_mix: {mix}")
    lines.append("")
    lines.append(_verdict_summary(report))
    return "\n".join(lines) + "\n"


def method_label() -> str:
    """Literal printed in the report header; pinned by `test_spec_literals.py`."""
    return NEAREST_RANK_METHOD
