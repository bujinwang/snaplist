"""Immutable data types shared by the telemetry functional core and its shell.

Everything here is a frozen dataclass or an enum. `EventRow.item_id` is `str | None`, NOT a UUID
object: the raw `sqlite3` reader returns the 36-character TEXT stored by the API. Coercing it
with `UUID(...)` or `lower()` would silently merge two distinct items; shape validation happens
once at the adapter boundary and malformed non-NULL ids are unparseable (3b), never coerced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    """The four states of a gated row (PLAN:293-296)."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class SourceState(str, Enum):
    """Data-source classification produced by `source_state()` before gate rows are computed.

    `SOURCE_ABSENT_3A` is downgradable by `--allow-inconclusive`; `SOURCE_UNUSABLE_3B` never is
    (PLAN:268-279). This is a *source* classification, not a verdict on any metric: it feeds the
    exit aggregation independently of `verdict_for()`.
    """

    OK = "OK"
    SOURCE_ABSENT_3A = "SOURCE_ABSENT_3A"
    SOURCE_UNUSABLE_3B = "SOURCE_UNUSABLE_3B"


class AttributionStatus(str, Enum):
    """Whether the refund ledger can still attribute a refund row back to an item.

    DEGRADED carries the count of unattributable refund *rows*; INTACT means every refund row
    resolved (or there were none, vacuously); UNVERIFIED is reserved for a future Postgres reader
    where the ledger's SET NULL behaviour is not yet measured under the real RLS role.
    """

    INTACT = "INTACT"
    DEGRADED = "DEGRADED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class Attribution:
    """Refund-attribution measurement, kept separate from any gate's `excluded_rate`."""

    status: AttributionStatus = AttributionStatus.INTACT
    refund_rows_total: int = 0
    refund_rows_attributable: int = 0
    unattributable: int = 0
    missing_rate: float = 0.0
    excluded_item_ids: frozenset[str] = frozenset()

    @property
    def label(self) -> str:
        """Machine-readable status string for the report header and `VERDICTS:` line."""
        if self.status is AttributionStatus.DEGRADED:
            return f"degraded(n={self.unattributable})"
        return self.status.value.lower()


@dataclass(frozen=True, slots=True)
class EventRow:
    """One row of `events` as read by the raw, read-only sqlite3 connection.

    Only the columns the gate actually reads are modelled. `field_names` is JSON text and no
    gate uses it, so it is deliberately absent — not read, not parsed.
    """

    event_id: str
    event: str
    received_at: str
    client_ts: str
    session_id: str
    subject: str
    app_version: str
    platform: str
    item_id: str | None = None
    source: str | None = None
    capture_mode: str | None = None
    batch_index: int | None = None
    decision: str | None = None
    job_latency_ms: int | None = None
    prettify_latency_ms: int | None = None
    attrs_latency_ms: int | None = None
    edited_field_count: int | None = None
    fields_touched: int | None = None
    marketplace: str | None = None
    row_count: int | None = None
    export_latency_ms: int | None = None
    bytes: int | None = None
    delivery_latency_ms: int | None = None
    plan: str | None = None
    paywall_variant: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    paywall_trigger: str | None = None
    plan_offered: str | None = None
    payload_sha256: str = ""


@dataclass(frozen=True, slots=True)
class ItemTimeline:
    """The deduplicated, per-item view the percentiles are computed over.

    One row per `item_id`, never per event. `prettify_latency_ms` is the explicit exception to
    "latest wins": tool time sums the latency across *all* retries (PLAN:117-136), while the
    approve attempt supplies `attrs_latency_ms`. `export_clicked` supplies `export_latency_ms`;
    `export_delivered` physically cannot carry that column (reverse CHECK in repository.py:580).
    """

    item_id: str
    subject: str
    received_at: str
    # Point attribute: the latest prettify_approved row's value (one row per item wins).
    prettify_latency_ms: float | None = None
    # Tool-time exception: SUM over every prettify_approved retry, so a retry cannot make an
    # item look faster than it was (PLAN:117-136, :136-146).
    prettify_sum_ms: float | None = None
    attrs_latency_ms: float | None = None
    export_latency_ms: float | None = None
    fields_touched: float | None = None
    tool_time_ms: float | None = None
    decision: str | None = None
    approved: bool = False
    delivered: bool = False
    retry_count: int = 0


@dataclass(frozen=True, slots=True)
class MetricResult:
    """A measured gate row, before rendering. `value` is None iff n is insufficient."""

    name: str
    n: int
    sellers: int
    value: float | None
    threshold: float
    unit: str
    min_n: int
    excluded_rate: float
    verdict: Verdict
    gated: bool = True


@dataclass(frozen=True, slots=True)
class SourceData:
    """Everything the adapter hands to the pure core. No DB handle escapes this object."""

    source_label: str = ""
    backend: str = "local-dev"
    present: bool = False
    events_table: bool = False
    events: tuple[EventRow, ...] = ()
    items: frozenset[str] = frozenset()
    deleted_items: frozenset[str] = frozenset()
    failed_items: frozenset[str] = frozenset()
    failure_mix: dict[str, int] = field(default_factory=dict)
    failure_mix_unattributable: int = 0
    orphan_item_ids: frozenset[str] = frozenset()
    attribution: Attribution = field(default_factory=Attribution)
    unparseable_rows: int = 0
    malformed_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GateReport:
    """The fully computed report. `gate.py` derives the exit code from this, never from I/O."""

    metrics: tuple[MetricResult, ...]
    report_only: tuple[str, ...]
    source_state: SourceState
    # Independent of any gate's `excluded_rate` (addendum 2, #1): a DEGRADED ledger is a
    # source-quality disclosure, not a verdict on a metric.
    refund_attribution: Attribution
    completeness: float | None
    failure_mix: dict[str, int]
    failure_mix_unattributable: int
    orphan_item_ids: tuple[str, ...]
    excluded_refunded: int
    window_start: str
    window_end: str
    population_items: int
    population_sellers: int
    git_sha: str
    source_label: str
    backend: str
    percentile_method: str
    banner: str | None = None
