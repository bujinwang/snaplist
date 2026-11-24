"""Measure the two latency assumptions in `docs/ONBOARDING.md` §0.

A1 — "upload → prettified image on screen", p50 ≤ 20 s and p90 ≤ 35 s. The only real
client-side producer today is `prettify_latency_ms` on `prettify_approved` with
`decision="approve"` (CONTRACTS.md §7: shutter/tap → pixels on screen). We report that as the
A1 proxy and say so in the table; we do not relabel a server `job_latency_ms` as an end-to-end
client number.

A2 — "client quality gate rejects an unusable frame in < 800 ms". There is **no gate implementation
and no event field for its duration** in this build, so this module cannot invent a number. It
accepts an optional JSON array of measured gate durations (from a device harness) and reports
UNMEASURED otherwise. The seam is here; the producer is not.

Both rows stay report-only. With <30 samples the value is UNMEASURED even if the arithmetic is
possible — a p90 over five frames is not the contract in ONBOARDING.md.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

from evals.telemetry.compute import nearest_rank, parse_now, window_bounds
from evals.telemetry.source import select_source
from evals.telemetry.spec import MIN_SAMPLE_ITEMS, WINDOW_SINCE

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "evals" / "reports" / "onboarding-latency.md"
DEFAULT_NOW = "2026-11-05T00:00:00.000000Z"  # pinned only when no --now is supplied

A1_P50_MAX_MS = 20_000.0
A1_P90_MAX_MS = 35_000.0
A2_P90_MAX_MS = 800.0
_GATE_EVENT = "prettify_approved"
_APPROVE = "approve"


@dataclass(frozen=True, slots=True)
class Measurement:
    """One number, or UNMEASURED with the reason it cannot be computed."""

    name: str
    value: float | None
    target: str
    n: int
    reason: str = ""

    @property
    def status(self) -> str:
        if self.value is None:
            return "UNMEASURED"
        if self.name == "A2 gate rejections":
            return "REPORT"
        limit = A1_P50_MAX_MS if self.name == "A1 p50" else (
            A1_P90_MAX_MS if self.name == "A1 p90" else A2_P90_MAX_MS
        )
        strict = self.name == "A2 p90"  # ONBOARDING says < 800ms, not <=.
        return "PASS" if (self.value < limit if strict else self.value <= limit) else "FAIL"

    def cell(self) -> str:
        return "UNMEASURED" if self.value is None else f"{self.value:.0f} ms"


def _a1_samples(*, db: str | Path | None = None, now: str = DEFAULT_NOW) -> list[float]:
    moment = parse_now(now)
    start, end = window_bounds(moment)
    source = select_source(db=db).load()
    values: list[float] = []
    for event in source.events:
        if (
            event.event != _GATE_EVENT
            or event.decision != _APPROVE
            or event.prettify_latency_ms is None
            or not (start <= event.received_at < end)
        ):
            continue
        values.append(float(event.prettify_latency_ms))
    return values


def _percentile(name: str, values: list[float], percentile: float, target: str) -> Measurement:
    if len(values) < MIN_SAMPLE_ITEMS:
        return Measurement(
            name, None, target, len(values),
            f"n={len(values)} < {MIN_SAMPLE_ITEMS} approved frames",
        )
    return Measurement(name, nearest_rank(values, percentile), target, len(values))


def _a2_measurements(durations: list[float] | None) -> tuple[Measurement, Measurement]:
    target = "< 800 ms"
    if durations is None:
        return (
            Measurement(
                "A2 gate rejections", None, target, 0,
                "no client quality gate exists in this build and no gate_duration_ms field "
                "is in EVENTS_VERSION 1.4.0",
            ),
            Measurement(
                "A2 p90", None, target, 0,
                "no measured gate durations supplied (--gate-latencies)",
            ),
        )
    if any(value < 0 for value in durations):
        raise ValueError("gate durations must be >= 0 ms")
    n = len(durations)
    return (
        Measurement("A2 gate rejections", float(n), "report only", n),
        _percentile("A2 p90", durations, 0.90, target),
    )


def measure(
    *, db: str | Path | None = None, now: str = DEFAULT_NOW,
    gate_durations: list[float] | None = None,
) -> tuple[Measurement, ...]:
    """Compute both assumptions. A2 stays UNMEASURED without an explicit device sample."""
    a1 = _a1_samples(db=db, now=now)
    return (
        _percentile("A1 p50", a1, 0.50, "<= 20,000 ms"),
        _percentile("A1 p90", a1, 0.90, "<= 35,000 ms"),
        *_a2_measurements(gate_durations),
    )


def render(rows: tuple[Measurement, ...], *, now: str, window: str) -> str:
    lines = [
        "# SnapList onboarding latency — A1 / A2",
        "",
        f"Window: last {window} ending {now}. Source: read-only telemetry events "
        "(A1 proxy: `prettify_approved.prettify_latency_ms` on `decision=approve`).",
        "",
        "| assumption | value | target | status | n |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.name} | {row.cell()} | {row.target} | {row.status} | {row.n} |"
        )
    lines.append("")
    reasons = [f"{row.name}: {row.reason}" for row in rows if row.reason]
    if reasons:
        lines.append("Why a cell is UNMEASURED:")
        lines += [f"- {reason}" for reason in reasons]
        lines.append("")
    lines += [
        "A1 is a client-measured proxy, not a server job duration. A2 has no producer with "
        "the frozen 7-event vocabulary; `--gate-latencies` is the explicit seam for a device "
        "harness, and no number is fabricated when it is absent.",
        "",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.onboarding_latency",
        description="Measure ONBOARDING.md A1/A2 latency assumptions.",
    )
    parser.add_argument("--db", default=None, help="SQLite database (default: SNAPLIST_DB)")
    parser.add_argument("--now", default=DEFAULT_NOW, help=f"ISO-8601 Z (default: {DEFAULT_NOW})")
    parser.add_argument(
        "--gate-latencies",
        type=Path,
        default=None,
        help="JSON array of measured client gate durations in ms (A2 producer seam)",
    )
    parser.add_argument(
        "--report", type=Path, default=DEFAULT_REPORT, help=f"output (default: {DEFAULT_REPORT})"
    )
    parser.add_argument("--dry-run", action="store_true", help="print; write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    durations: list[float] | None = None
    if args.gate_latencies is not None:
        raw = json.loads(args.gate_latencies.read_text())
        if not isinstance(raw, list) or not all(
            isinstance(value, int | float) and not isinstance(value, bool) for value in raw
        ):
            raise ValueError("--gate-latencies must be a JSON array of numbers (ms)")
        durations = [float(value) for value in raw]
    db = args.db if args.db is not None else os.environ.get("SNAPLIST_DB")
    rows = measure(db=db, now=args.now, gate_durations=durations)
    text = render(rows, now=args.now, window=WINDOW_SINCE)
    if args.dry_run:
        print(text)
        return 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text)
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
