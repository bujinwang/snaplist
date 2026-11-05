"""The ONLY module that knows exit codes.

Pipeline: build the full `GateReport` (every row computed, no short-circuit) -> aggregate all
contributions with a rank map -> apply `--allow-inconclusive` (only 2 and 3a are downgradable).

Precedence `3 > 1 > 2 > 0` is a PRIORITY, not a numeric ordering. Using `max()` over the integer
codes would let an inconclusive (2) outrank a real regression (1), which is backwards; the
EXIT_RANK map exists precisely so the aggregation cannot be written that way.
"""

from __future__ import annotations

from datetime import datetime

from evals import git_sha
from evals.telemetry.compute import (
    activation,
    all_rows_evaluated,
    completeness_of,
    compute_metrics,
    failure_mix_counts,
    population,
    report_only_lines,
    source_state,
    window_bounds,
)
from evals.telemetry.model import GateReport, SourceData, SourceState, Verdict
from evals.telemetry.spec import (
    DOWNGRADABLE_STATES,
    EXIT_DATA_ERROR,
    EXIT_FAIL,
    EXIT_INCONCLUSIVE,
    EXIT_PASS,
    EXIT_RANK,
    NEAREST_RANK_METHOD,
    NO_GATE_BANNER,
)

_VERDICT_EXIT: dict[Verdict, int] = {
    Verdict.PASS: EXIT_PASS,
    Verdict.FAIL: EXIT_FAIL,
    Verdict.INCONCLUSIVE: EXIT_INCONCLUSIVE,
    Verdict.DATA_UNAVAILABLE: EXIT_DATA_ERROR,
}


def build_report(data: SourceData, now: datetime, *, sha: str | None = None) -> GateReport:
    """Compute the whole report. Nothing here short-circuits on the first bad row."""
    start, end = window_bounds(now)
    metrics, _ = compute_metrics(data, start, end)
    state = source_state(data)
    evaluated = all_rows_evaluated(metrics)
    items, sellers = population(data, start, end)

    return GateReport(
        metrics=metrics,
        report_only=report_only_lines(data, metrics, start, end),
        source_state=state,
        refund_attribution=data.attribution,
        completeness=completeness_of(metrics),
        failure_mix=failure_mix_counts(data),
        failure_mix_unattributable=data.failure_mix_unattributable,
        orphan_item_ids=tuple(sorted(data.orphan_item_ids)),
        excluded_refunded=len(data.attribution.excluded_item_ids),
        window_start=start,
        window_end=end,
        population_items=items,
        population_sellers=sellers,
        git_sha=sha if sha is not None else git_sha(),
        source_label=data.source_label,
        backend=data.backend,
        percentile_method=NEAREST_RANK_METHOD,
        banner=None if evaluated else NO_GATE_BANNER,
    )


def _state_label(state: SourceState) -> str:
    """The state name used by the downgrade whitelist (not an exit code)."""
    return state.value


def aggregate_exit(report: GateReport, *, allow_inconclusive: bool) -> int:
    """Aggregate ALL contributions first, then pick the highest-priority code. No short-circuit.

    Report-only rows are absent from `report.metrics` and therefore never contribute. The
    source-state contribution is independent of every verdict (addendum 2, layering ruling).
    Downgrading happens per-contribution BEFORE ranking, so a real FAIL (1) can never be
    hidden behind a downgraded 3a or INCONCLUSIVE (2); only those two labels may become 0.
    """
    contributions: list[tuple[int, str]] = []
    for metric in report.metrics:
        contributions.append((_VERDICT_EXIT[metric.verdict], metric.verdict.value))

    # source_state() is a DATA-QUALITY classification, not a second verdict on a metric.
    if report.source_state is SourceState.SOURCE_ABSENT_3A:
        contributions.append((EXIT_DATA_ERROR, _state_label(report.source_state)))
    elif report.source_state is SourceState.SOURCE_UNUSABLE_3B:
        contributions.append((EXIT_DATA_ERROR, _state_label(report.source_state)))

    if not contributions:
        return EXIT_PASS

    # `--allow-inconclusive` downgrades ONLY exit 2 and 3a. 1 (FAIL) and 3b / DATA_UNAVAILABLE
    # are never touched, because "we have no users yet" and "we stopped looking" must not
    # produce the same green (PLAN:268-279).
    if allow_inconclusive:
        contributions = [
            (EXIT_PASS if label in DOWNGRADABLE_STATES else code, label)
            for code, label in contributions
        ]

    # Compute every rank before choosing: this is the "compute all rows then aggregate" rule.
    # Precedence 3 > 1 > 2 > 0 is a PRIORITY, not a numeric ordering; max() over the integer
    # codes would let an inconclusive (2) outrank a real regression (1).
    ranked = [(EXIT_RANK[code], code) for code, _ in contributions]
    best_rank = max(rank for rank, _ in ranked)
    return next(code for rank, code in ranked if rank == best_rank)


def exit_code(report: GateReport, *, gate: bool, allow_inconclusive: bool) -> int:
    """Report mode always prints and exits 0; the release gate uses the aggregation.

    `--allow-inconclusive` only downgrades exit 2 (INCONCLUSIVE) and 3a (SOURCE_ABSENT_3A).
    Exit 1 (FAIL) and 3b (SOURCE_UNUSABLE_3B) stay non-zero in both modes (PLAN:268-279).
    """
    if not gate:
        return EXIT_PASS
    return aggregate_exit(report, allow_inconclusive=allow_inconclusive)


def activation_summary(data: SourceData, start: str, end: str) -> str:
    """Convenience for the report-only activation row; still never an exit contribution."""
    _, _, text = activation(data, start, end)
    return text
