"""Frozen literals for the E0-T5 gate. Standard library only.

`docs/PLAN.md` (b) gate table and §"Percentiles, joins and windows" are normative. The numbers
below are transcribed, not derived: `test_spec_literals.py` asserts each one as a literal so a
quiet edit cannot turn a gate green. Changing any number in (b) requires an ADR (PLAN (e):332).

There is exactly ONE exclusion threshold in this package — `EXCLUDED_RATE_MAX`. Both
`verdict_for()` (the per-metric judgement, PLAN:135/:148-155) and `source_state()` (the
`attribution_missing_rate` data-quality classification, PLAN:250) read this same constant. It is
deliberately *not* one of the eight gate thresholds; those are a different set of literals.
"""

from __future__ import annotations

from dataclasses import dataclass

# -----------------------------------------------------------------------------------------
# The single judgement threshold (PLAN:135, PLAN:250, HANDOFF-product §12.2(a)/:706).
# -----------------------------------------------------------------------------------------
EXCLUDED_RATE_MAX: float = 0.20

# Nearest-rank percentile, hand-written: 0-based index = ceil(p * n) - 1 (PLAN §12.2(e)).
NEAREST_RANK_METHOD: str = "nearest-rank"

# Half-open UTC window [now - 7d, now), filtered on `received_at` (never `client_ts`).
WINDOW_SINCE: str = "7d"
WINDOW_DAYS: int = 7
RECEIVED_AT_FORMAT: str = "%Y-%m-%dT%H:%M:%S.%fZ"

# Report-only diagnostics. There is deliberately NO `COMPLETENESS_MIN` here: `completeness`
# is the mirror `1 - excluded_rate`, PRINTED and never compared (PLAN:148-150). Naming a
# second threshold would invite the exact double-judgement the ticket forbids.
ACTIVATION_MIN_SELLERS: int = 20
ACTIVATION_MIN_ITEMS: int = 3

# -----------------------------------------------------------------------------------------
# (b) Gate table. `n` counts items for every gated row; activation counts sellers and is
# reported, never gated (PLAN:281-293, HANDOFF-product §12.2(b):721-727 / (g):792-798).
# -----------------------------------------------------------------------------------------
MIN_SAMPLE_ITEMS: int = 30


@dataclass(frozen=True, slots=True)
class GateSpec:
    """One row of PLAN (b). `value > threshold` on sufficient sample is a FAIL."""

    name: str
    threshold: float
    unit: str
    min_n: int
    description: str


GATE_SPECS: tuple[GateSpec, ...] = (
    GateSpec("prettify_p50", 20_000.0, "ms", 30, "p50 prettify_latency_ms"),
    GateSpec("prettify_p90", 35_000.0, "ms", 30, "p90 prettify_latency_ms"),
    GateSpec("tooltime_p50", 35_000.0, "ms", 30, "p50 tool_time_ms"),
    GateSpec("tooltime_p90", 60_000.0, "ms", 30, "p90 tool_time_ms"),
    GateSpec("attrs_p90", 8_000.0, "ms", 30, "p90 attrs_latency_ms"),
    GateSpec("export_p90", 5_000.0, "ms", 30, "p90 export_latency_ms"),
    GateSpec("fields_mean", 5.0, "", 30, "mean fields_touched"),
    GateSpec("fields_p90", 12.0, "", 30, "p90 fields_touched"),
)

# Report-only rows, printed but never gated (PLAN:293, §12.2(b)).
REPORT_ONLY_ROWS: tuple[str, ...] = (
    "fields_p50",
    "activation_7d",
    "export_burst_60m",
    "completeness",
)

# Failure-mix reason codes: items whose job ended `failed` with one of these are excluded from
# the latency and fields gates and counted *separately* (PLAN:168-172, §12.2(d)).
FAILURE_MIX_CODES: tuple[str, ...] = (
    "bad_photo",
    "no_garment",
    "low_confidence",
    "vton_rejected",
)

# -----------------------------------------------------------------------------------------
# Exit codes. Precedence 3 > 1 > 2 > 0 is a PRIORITY, not a numeric ordering: max() over the
# integer codes would let 2 outrank 1, which is backwards. `gate.py` aggregates with this map
# after every row has been computed (no short-circuit).
# -----------------------------------------------------------------------------------------
EXIT_PASS: int = 0
EXIT_FAIL: int = 1
EXIT_INCONCLUSIVE: int = 2
EXIT_DATA_ERROR: int = 3

EXIT_RANK: dict[int, int] = {
    EXIT_DATA_ERROR: 30,
    EXIT_FAIL: 20,
    EXIT_INCONCLUSIVE: 10,
    EXIT_PASS: 0,
}

# Downgraded by --allow-inconclusive, and *only* these two: exit 2 (insufficient sample) and
# 3a (source absent). 1 (FAIL) and 3b (source unusable) are never downgraded.
DOWNGRADABLE_STATES: frozenset[str] = frozenset({"INCONCLUSIVE", "SOURCE_ABSENT_3A"})

# The banner that must precede the table when no gate was genuinely evaluated (PLAN:300-306).
NO_GATE_BANNER: str = "GATE NOT EVALUATED — n=0. This is NOT a pass."

# Literal used in the value column when n is insufficient, and the literal for a source we
# cannot measure. Neither may be a number computed from a sample we just called insufficient.
VALUE_PLACEHOLDER: str = "-"
UNMEASURED: str = "UNMEASURED"

# Twenty per cent is also the source-state cut for a *required property* being missing from
# qualifying rows (PLAN:250). We reuse the same literal; `source_state()` gives it a separate
# denominator (refund rows), which is a different variable from the per-metric `excluded_rate`.
