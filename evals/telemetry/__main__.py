"""Command-line shell. `python -m evals.telemetry --since 7d [--gate] [--allow-inconclusive]`.

    --since 7d                         report mode, always exit 0
    --since 7d --gate                 release gate, exit 0/1/2/3
    --since 7d --gate --allow-inconclusive   pre-launch CI (only 2 and 3a are downgraded)

This module does argparse, reads `now`, builds the report, prints to stdout and raises
SystemExit. It contains no arithmetic, no thresholds and no SQL. `--now` exists so tests can pin
the half-open window without touching the clock; it is not a production knob.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from evals.telemetry.compute import parse_now
from evals.telemetry.gate import build_report, exit_code
from evals.telemetry.report import render
from evals.telemetry.source import SqliteEventSource
from evals.telemetry.spec import WINDOW_SINCE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.telemetry",
        description="E0-T5 telemetry gate over real analytics events (stdout only).",
    )
    parser.add_argument(
        "--since",
        default=WINDOW_SINCE,
        help="window length; the gate is specified for 7d (default).",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path (defaults to the SNAPLIST_DB env var). Opened read-only.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="fixed ISO-8601 UTC Z window end for reproducible tests.",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="release gate: aggregate exit codes instead of always returning 0.",
    )
    parser.add_argument(
        "--allow-inconclusive",
        action="store_true",
        help="pre-launch CI: downgrade ONLY exit 2 and 3a; never 1 or 3b.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code; `SystemExit` is raised by the guard below."""
    args = _parser().parse_args(argv)
    if args.since != WINDOW_SINCE:
        print(
            f"UNMEASURED (--since {args.since!r}: E0-T5 is specified for 7d only)",
            file=sys.stderr,
        )
        return 3

    now = parse_now(args.now) if args.now else datetime.now(UTC)
    source = SqliteEventSource(args.db)
    data = source.load()
    report = build_report(data, now)
    sys.stdout.write(render(report))
    return exit_code(report, gate=args.gate, allow_inconclusive=args.allow_inconclusive)


if __name__ == "__main__":
    raise SystemExit(main())
