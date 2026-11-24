"""Produce `evals/reports/cost.md` from measured job/asset data — nothing else.

`infra/ci/gates/cost_dashboard.py` deliberately owns no data source: it only parses the report.
This module is the missing producer. It opens the API's SQLite database **read-only** (never
constructs `SqliteRepository`, whose DDL would mutate the snapshot), reads the fields that are
actually measured, and writes the Markdown table the dashboard expects.

The honesty rules are the same as everywhere else in this repo:

* `NULL` cost stays `UNMEASURED`. It is never coerced to `0` — an unmeasured job is not a free job.
* `jobs.cost_micros` is an **aggregate** when present. The pipeline does not record per-stage
  dollars, so it is not allocated across E1/E2/E3. Those three rows stay `UNMEASURED` even when
  the aggregate exists; a wrong bucket is worse than an empty one.
* `E4` is priced per render, not per item. Its row uses measured `kind=vton`, `state=done`
  `cost_micros` values when they exist. Otherwise it is `UNMEASURED`.
* Raw token counts found in `assets.meta` are reported as observations, never multiplied by a
  list price. `assets.byte_size` is reported as bytes, never converted into a guessed bill.

Usage
-----
    .venv/bin/python -m evals.cost_report --db /path/to/snaplist.db
    .venv/bin/python -m evals.cost_report --dry-run     # print, write nothing

With no `--db`, `SNAPLIST_DB` is used. An absent database or `:memory:` produces a truthful
all-UNMEASURED report; it does not create a database just to have something to read.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "evals" / "reports" / "cost.md"

UNMEASURED = "UNMEASURED"

_REASONS = {
    "E1": (
        "no per-stage cost is recorded by segment(); a backend name in assets.meta is not a "
        "price"
    ),
    "E2": (
        "no per-stage cost is recorded by rectify/prettify; measured seconds are not dollars"
    ),
    "E3": (
        "jobs.cost_micros is an aggregate, not an attributes-stage cost; VLM token usage is "
        "not persisted by the pipeline"
    ),
}


@dataclass(frozen=True, slots=True)
class Observations:
    """What the database actually said. `None` means the query could not answer."""

    source_state: str
    db_label: str
    all_jobs: int | None = None
    done_jobs: int | None = None
    jobs_with_cost: int | None = None
    aggregate_cost_micros: int | None = None
    vton_n: int | None = None
    vton_cost_micros: int | None = None
    assets_n: int | None = None
    assets_with_bytes: int | None = None
    total_bytes: int | None = None
    token_rows: int = 0
    total_tokens: int | None = None
    cost_usd_rows: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def e4_per_render_usd(self) -> float | None:
        """Mean of measured done-VTON rows, or None (UNMEASURED)."""
        if not self.vton_n or self.vton_cost_micros is None:
            return None
        return self.vton_cost_micros / 1_000_000 / self.vton_n


def _db_label(target: str) -> str:
    return "(no database configured; SNAPLIST_DB unset)" if not target else target


def _connect_read_only(target: str) -> sqlite3.Connection | None:
    """Open a snapshot without ever writing DDL to it. Never accepts `:memory:`."""
    if not target or target == ":memory:":
        return None
    path = Path(target)
    if not path.exists():
        return None
    uri = f"file:{path.resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _scalar(conn: sqlite3.Connection, sql: str) -> tuple | None:
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return None
    return row


def _count_meta_usage(conn: sqlite3.Connection) -> tuple[int, int | None, int]:
    """Sum token counts/flag cost_usd values only when a stage actually persisted them."""
    try:
        rows = conn.execute("select meta from assets where meta is not null").fetchall()
    except sqlite3.OperationalError:
        return 0, None, 0
    token_rows = 0
    total: int | None = None
    cost_rows = 0
    for (raw,) in rows:
        try:
            meta = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if not isinstance(meta, dict):
            continue
        usage = meta.get("usage")
        tokens: int | None = None
        if isinstance(usage, dict):
            value = usage.get("total_tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                tokens = value
            else:
                inp, out = usage.get("input_tokens"), usage.get("output_tokens")
                if isinstance(inp, int) and isinstance(out, int):
                    tokens = inp + out
        if tokens is None and isinstance(meta.get("total_tokens"), int):
            tokens = int(meta["total_tokens"])
        if tokens is not None:
            token_rows += 1
            total = tokens if total is None else total + tokens
        if "cost_usd" in meta and meta["cost_usd"] is not None:
            cost_rows += 1
    return token_rows, total, cost_rows


def collect(db_target: str | None = None) -> Observations:
    """Read the snapshot. No writes, no provider calls, no invented dollars."""
    target = db_target if db_target is not None else os.environ.get("SNAPLIST_DB", "")
    conn = _connect_read_only(target)
    if conn is None:
        state = "EMPTY_SESSION" if target == ":memory:" else "DB_ABSENT"
        return Observations(source_state=state, db_label=_db_label(target))
    try:
        jobs = _scalar(
            conn,
            "select count(*), "
            "coalesce(sum(case when state = 'done' then 1 else 0 end), 0), "
            "count(cost_micros), sum(cost_micros) from jobs",
        )
        vton = _scalar(
            conn,
            "select count(*), sum(cost_micros) from jobs "
            "where state = 'done' and kind = 'vton' and cost_micros is not null",
        )
        assets = _scalar(
            conn,
            "select count(*), count(byte_size), sum(byte_size) from assets",
        )
        token_rows, total_tokens, cost_rows = _count_meta_usage(conn)
        problems: list[str] = []
        if jobs is None:
            problems.append("jobs table is absent from this database")
        if vton is None:
            problems.append("jobs table is absent, so no VTON cost could be read")
        if assets is None:
            problems.append("assets table is absent from this database")
        return Observations(
            source_state="DB_READ",
            db_label=_db_label(target),
            all_jobs=int(jobs[0]) if jobs else None,
            done_jobs=int(jobs[1]) if jobs else None,
            jobs_with_cost=int(jobs[2]) if jobs else None,
            aggregate_cost_micros=int(jobs[3]) if jobs and jobs[3] is not None else None,
            vton_n=int(vton[0]) if vton else None,
            vton_cost_micros=int(vton[1]) if vton and vton[1] is not None else None,
            assets_n=int(assets[0]) if assets else None,
            assets_with_bytes=int(assets[1]) if assets else None,
            total_bytes=int(assets[2]) if assets and assets[2] is not None else None,
            token_rows=token_rows,
            total_tokens=total_tokens,
            cost_usd_rows=cost_rows,
            problems=problems,
        )
    finally:
        conn.close()


def _fmt_usd(value: float | None) -> str:
    return UNMEASURED if value is None else f"${value:.4f}"


def _fmt_int(value: int | None) -> str:
    return "—" if value is None else str(value)


def _stage_reason(key: str, obs: Observations) -> str:
    if key == "E4":
        if obs.e4_per_render_usd is not None:
            return (
                "mean of measured jobs.cost_micros for state='done', kind='vton' "
                f"(n={obs.vton_n}); per render, not per item"
            )
        return "no done VTON job has a measured cost_micros; NULL means UNMEASURED, not free"
    if obs.token_rows and key == "E3":
        return (
            _REASONS[key]
            + f"; {obs.token_rows} asset(s) carry token counts but no measured $/item"
        )
    return _REASONS[key]


def render(obs: Observations, *, generated_at: str) -> str:
    """The exact report shape `cost_dashboard.py::parse_report` accepts."""
    lines = [
        "# SnapList measured cost report (E0-T4)",
        "",
        f"Generated by `python -m evals.cost_report` at {generated_at}.",
        f"Source: `{obs.db_label}` ({obs.source_state}), opened read-only.",
        "NULL job cost stays UNMEASURED. Aggregate cost is never allocated to a stage that "
        "did not measure it.",
        "",
        "| stage | $/item | n | notes |",
        "|---|---|---|---|",
        f"| E1 segmentation | {UNMEASURED} | — | {_stage_reason('E1', obs)} |",
        f"| E2 rectify + prettify | {UNMEASURED} | — | {_stage_reason('E2', obs)} |",
        f"| E3 attributes + dedupe | {UNMEASURED} | — | {_stage_reason('E3', obs)} |",
        f"| E4 on-model render (per render) | {_fmt_usd(obs.e4_per_render_usd)} | "
        f"{_fmt_int(obs.vton_n)} | {_stage_reason('E4', obs)} |",
        "",
        "## Observations (not prices)",
        "",
        "| observation | value |",
        "|---|---|",
        f"| jobs (all states) | {_fmt_int(obs.all_jobs)} |",
        f"| done jobs | {_fmt_int(obs.done_jobs)} |",
        f"| jobs with measured cost_micros | {_fmt_int(obs.jobs_with_cost)} |",
        f"| aggregate measured cost_micros | {_fmt_int(obs.aggregate_cost_micros)} |",
        "| aggregate allocation | not attempted: the pipeline does not record per-stage dollars |",
        f"| assets | {_fmt_int(obs.assets_n)} |",
        f"| assets with measured byte_size | {_fmt_int(obs.assets_with_bytes)} |",
        f"| total measured asset bytes | {_fmt_int(obs.total_bytes)} |",
        f"| assets carrying token counts | {obs.token_rows} |",
        f"| total observed tokens | {_fmt_int(obs.total_tokens)} |",
        f"| assets carrying cost_usd | {obs.cost_usd_rows} |",
        "",
    ]
    if obs.problems:
        lines += ["## Read notes", ""] + [f"- {p}" for p in obs.problems] + [""]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.cost_report",
        description="Generate evals/reports/cost.md from measured job/asset data.",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite snapshot to read (default: SNAPLIST_DB, or no data if unset)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"output path (default: {DEFAULT_REPORT.relative_to(ROOT)})",
    )
    parser.add_argument("--dry-run", action="store_true", help="print only; write nothing")
    parser.add_argument("--now", default=None, help="ISO-8601 timestamp (default: now, UTC)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = args.now or datetime.now(UTC).isoformat(timespec="seconds")
    text = render(collect(args.db), generated_at=generated_at)
    if args.dry_run:
        print(text)
        return 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.report.with_suffix(args.report.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, args.report)
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
