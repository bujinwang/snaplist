"""E0-T4 — cost dashboard: the `$/item` table, read out of `evals/reports/cost.md`.

The DoD for this ticket is one sentence: **the dashboard reads from `evals/reports/cost.md`,
not from a spreadsheet somebody typed.** So this script owns exactly one input — that file —
and one rule: every cell is either a number parsed out of a measured report, or the literal
`UNMEASURED` with the reason it is not a number. There is no flag to type a number in, no
default, no `--assume`, and no fallback constant. If `cost.md` does not exist, the table still
renders — with every cell `UNMEASURED` — because the honest shape of "nobody has ever paid for
a render" is a table full of holes, not a plausible-looking estimate.

**`evals/reports/cost.md` does not exist in this checkout**, and the reason is not laziness:
no pipeline stage has ever run against a paid provider (E4 on-model is a stub, `FASHN_API_KEY`
and `MODAL_TOKEN` are absent — see `packages/vision/BLOCKED.md`), and there are no production
`usage_events` to derive a per-item cost from. E1/E2/E3 run locally on `onnxruntime` and
classical CV, which produces wall-clock seconds, not a billed `$/item`. Therefore the
`<= $0.03/item` verdict is **UNREACHABLE — not passing**, and the string `PASS` is only ever
printed once every stage in the total has a measured number behind it.

Exit codes (three, and the third is why this is not a decorative report):

    0  read what there was; the table was rendered (report mode always exits 0 when cost.md is
       absent, because "absent" is a fact to report, not a failure to hide)
    1  `--fail-on-unmeasured` and the total is measured and OVER the `<= $0.03/item` budget
    2  `--fail-on-unmeasured` and something is UNMEASURED (no cost.md, a missing stage, an
       unparsable cell) — i.e. the budget was not evaluated; also 2 on any refusal to write
       `docs/QA.md` (§13 markers missing), because silently appending is worse than failing

The expected format of `evals/reports/cost.md` is a Markdown table with a `$/item` column, a
stage column (`E1`/`E2`/`E3`, or a stage name such as `prettify`) and optionally a sample-count
column (`n`, `samples`). Anything else in the file is ignored. A cell may be `$0.0042`,
`` `0.0042` `` or `0.0042`; `UNMEASURED`, `n/a`, `—`, `-` and an empty cell all parse as "not a
number", which is a first-class value here rather than an error.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

GATE_DIR = Path(__file__).resolve().parent
ROOT = GATE_DIR.parents[2]
DEFAULT_REPORT = ROOT / "evals" / "reports" / "cost.md"
DEFAULT_DOC = ROOT / "docs" / "QA.md"

TARGET_PER_ITEM = 0.03
UNMEASURED = "UNMEASURED"

DOC_START = "<!-- E0-T4:COST-DASHBOARD:START -->"
DOC_END = "<!-- E0-T4:COST-DASHBOARD:END -->"

EXIT_OK = 0
EXIT_OVER_BUDGET = 1
EXIT_NOT_EVALUABLE = 2

NO_REPORT_REASON = (
    "evals/reports/cost.md does not exist — no stage has ever run against a paid provider "
    "(E4 on-model is a stub; FASHN_API_KEY and MODAL_TOKEN are absent, "
    "packages/vision/BLOCKED.md) and there are no production usage_events, so no $/item has "
    "ever been billed"
)
LOCAL_STAGES_REASON = (
    "E1/E2/E3 run locally on onnxruntime + classical CV: that produces wall-clock seconds, "
    "not a billed $/item"
)


@dataclass(frozen=True, slots=True)
class Stage:
    """One line of the cost model. `key` is what the report's stage cell is matched to."""

    key: str
    label: str
    epic: str


STAGES: tuple[Stage, ...] = (
    Stage("E1", "segmentation", "E1"),
    Stage("E2", "rectify + prettify", "E2"),
    Stage("E3", "attributes + dedupe", "E3"),
)

# Not part of the `<= $0.03/item` total: E4 is priced per render (`<= $0.10/render`), it is
# BLOCKED in this build, and folding it into a per-item total would compare two different units.
EXTRA_STAGES: tuple[Stage, ...] = (Stage("E4", "on-model render (per render, not per item)", "E4"),)

TOTAL_STAGES: tuple[str, ...] = tuple(stage.key for stage in STAGES)

COST_HEADERS = frozenset(
    {
        "$/item",
        "$/ item",
        "$ per item",
        "cost/item",
        "cost per item",
        "cost_usd_per_item",
        "cost (usd/item)",
        "unit cost",
        "usd/item",
    }
)
COUNT_HEADERS = frozenset(
    {"n", "samples", "sample", "sample_count", "samples_count", "n_items", "items", "count"}
)
STAGE_HEADERS = frozenset({"stage", "epic", "step", "stage_id", "phase"})

STAGE_ALIASES: dict[str, str] = {
    "segmentation": "E1",
    "segment": "E1",
    "rectify": "E2",
    "prettify": "E2",
    "attributes": "E3",
    "attribute": "E3",
    "attr": "E3",
    "dedupe": "E3",
    "on-model": "E4",
    "on model": "E4",
    "vton": "E4",
    "render": "E4",
}

STAGE_KEY_RE = re.compile(r"^(e[1-4])\b")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")


@dataclass(frozen=True, slots=True)
class ParsedStage:
    """One stage row exactly as it was found in the report, before any judgement."""

    key: str
    cost: float | None
    n: int | None
    raw: str
    line: str


@dataclass(frozen=True, slots=True)
class StageRow:
    """A stage as the dashboard reports it: a number, or UNMEASURED plus why."""

    stage: Stage
    cost: float | None
    n: int | None
    reason: str

    @property
    def status(self) -> str:
        return "MEASURED" if self.cost is not None else UNMEASURED


@dataclass(slots=True)
class Dashboard:
    """Everything one run decided, so rendering and the exit code cannot disagree."""

    report_path: Path
    report_state: str
    rows: list[StageRow] = field(default_factory=list)
    total: float | None = None
    total_reason: str = ""
    verdict: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def total_status(self) -> str:
        return "MEASURED" if self.total is not None else UNMEASURED

    @property
    def has_unmeasured(self) -> bool:
        """Whether the `<= $0.03/item` budget could be evaluated at all.

        Scoped to the stages that make up **this** budget: E1, E2, E3 and their total. E4 is
        reported because a reader needs to see that it exists and is blocked, but it is priced
        per *render*, has its own DoD target, and folding an absent per-render number into a
        per-item gate would fail this gate for a reason it does not own.
        """
        if self.total is None:
            return True
        return any(row.cost is None for row in self.rows if row.stage.key in TOTAL_STAGES)

    @property
    def over_budget(self) -> bool:
        return self.total is not None and self.total > TARGET_PER_ITEM


def normalise_header(cell: str) -> str:
    """Header cells are compared loosely: case, backticks, emphasis and spacing must not matter."""
    return cell.strip().strip("`*_ ").strip().lower()


def normalise_stage(cell: str) -> str | None:
    """Map a stage cell to `E1`..`E4`, or `None` when it is not a stage this dashboard knows.

    `None` is returned rather than guessed: a row labelled `misc` must not be silently counted
    as E3, because a wrong bucket is a wrong number with a confident label on it.
    """
    text = cell.strip().strip("`*_ ").strip().lower()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    match = STAGE_KEY_RE.match(text)
    if match:
        return match.group(1).upper()
    for alias, key in STAGE_ALIASES.items():
        if text == alias or text.startswith(f"{alias} ") or text.startswith(f"{alias}("):
            return key
        if text.startswith(f"{alias} +") or text.startswith(f"{alias}/"):
            return key
    return None


def parse_cost(cell: str) -> float | None:
    """`$0.0042`, ``` `0.0042` ``` and `0.0042 (± ...)` -> a float. Everything else -> `None`.

    Returns `None` for `UNMEASURED`, `n/a`, `—`, `-`, an empty cell, and anything that is not a
    number at all. `None` is the honest value for all of those, and the caller prints the cell
    verbatim next to it so nothing is lost.
    """
    text = cell.strip().strip("`*_ ").strip()
    if not text:
        return None
    cleaned = text.replace("$", "").replace(",", "").strip()
    if cleaned.lower() in {"unmeasured", "n/a", "na", "none", "tbd", "?", "-", "—", "–"}:
        return None
    match = NUMBER_RE.search(cleaned)
    if match is None:
        return None
    try:
        value = float(match.group(0))
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_count(cell: str) -> int | None:
    """Sample count, or `None`. A count of `0` is kept as `0` — it is a measurement of nothing."""
    digits = re.search(r"\d+", cell.replace(",", ""))
    if digits is None:
        return None
    try:
        return int(digits.group(0))
    except ValueError:
        return None


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(SEPARATOR_RE.match(cell.replace(" ", "")) for cell in cells)


def _table_blocks(lines: list[str]) -> list[list[str]]:
    """Group consecutive `|`-lines into table blocks. Blank lines and prose end a block."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip().startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _column(header: list[str], wanted: frozenset[str]) -> int | None:
    for index, cell in enumerate(header):
        if normalise_header(cell) in wanted:
            return index
    return None


def parse_report(text: str) -> tuple[dict[str, ParsedStage], list[str]]:
    """Every stage row in the report, plus anything that was ignored and why.

    A block is treated as a cost table only when its header names a `$/item`-style column, so
    an unrelated table in the same report cannot contribute numbers. Duplicate stage rows are
    resolved in favour of the first **measured** one; a duplicate that contradicts a measured
    value is recorded as a problem rather than silently overwritten.
    """
    parsed: dict[str, ParsedStage] = {}
    problems: list[str] = []

    for block in _table_blocks(text.splitlines()):
        if len(block) < 2:
            continue
        header = _cells(block[0])
        cost_index = _column(header, COST_HEADERS)
        if cost_index is None:
            continue
        count_index = _column(header, COUNT_HEADERS)
        stage_index = _column(header, STAGE_HEADERS)
        if stage_index is None:
            stage_index = 0

        for line in block[1:]:
            cells = _cells(line)
            if not cells or _is_separator(cells):
                continue
            if max(cost_index, stage_index) >= len(cells):
                problems.append(f"ignored a short row ({len(cells)} cells): {line.strip()}")
                continue
            key = normalise_stage(cells[stage_index])
            if key is None:
                problems.append(
                    f"ignored a row whose stage {cells[stage_index]!r} is not E1..E4: "
                    f"{line.strip()}"
                )
                continue
            cost = parse_cost(cells[cost_index])
            n = (
                parse_count(cells[count_index])
                if count_index is not None and count_index < len(cells)
                else None
            )
            candidate = ParsedStage(
                key=key, cost=cost, n=n, raw=cells[cost_index], line=line.strip()
            )
            existing = parsed.get(key)
            if existing is None:
                parsed[key] = candidate
            elif existing.cost is None and cost is not None:
                parsed[key] = candidate
            elif cost is not None and existing.cost is not None and cost != existing.cost:
                problems.append(
                    f"conflicting rows for {key} ({existing.cost} vs {cost}); kept the first, "
                    f"line: {existing.line}"
                )
            else:
                problems.append(f"duplicate row for {key}; kept the first, line: {existing.line}")
    if not parsed and text.strip():
        problems.append("no $/item table found in the report")
    return parsed, problems


def build_dashboard(report_path: Path = DEFAULT_REPORT) -> Dashboard:
    """Read the report and price every stage. Missing file is a rendered state, not an error."""
    if not report_path.exists():
        return _unmeasured_dashboard(
            report_path,
            report_state="ABSENT",
            reason=NO_REPORT_REASON,
            problems=["the file does not exist, so nothing could be parsed"],
        )
    try:
        text = report_path.read_text()
    except OSError as exc:
        return _unmeasured_dashboard(
            report_path,
            report_state="UNREADABLE",
            reason=f"the report exists but could not be read ({type(exc).__name__}: {exc})",
            problems=[str(exc)],
        )
    parsed, problems = parse_report(text)
    dash = Dashboard(report_path=report_path, report_state="PRESENT", problems=problems)

    for stage in (*STAGES, *EXTRA_STAGES):
        found = parsed.get(stage.key)
        if found is None:
            dash.rows.append(
                StageRow(
                    stage=stage,
                    cost=None,
                    n=None,
                    reason=f"no {stage.key} row in {_show(report_path)}",
                )
            )
        elif found.cost is None:
            dash.rows.append(
                StageRow(
                    stage=stage,
                    cost=None,
                    n=found.n,
                    reason=f"{stage.key} row is not a number (cell {found.raw!r})",
                )
            )
        else:
            dash.rows.append(StageRow(stage=stage, cost=found.cost, n=found.n, reason=""))

    by_key = {row.stage.key: row for row in dash.rows}
    missing = [key for key in TOTAL_STAGES if by_key[key].cost is None]
    if missing:
        parts = ", ".join(
            f"{key} ({by_key[key].reason or 'not a number'})" for key in missing
        )
        dash.total = None
        dash.total_reason = (
            f"E1+E2+E3 is not fully measured — missing: {parts}. A partial sum is not a "
            "per-item cost, so no total is printed"
        )
        dash.verdict = (
            f"UNMEASURED — the <= ${TARGET_PER_ITEM:.2f}/item verdict is UNREACHABLE, not "
            "passing: it needs a measured $/item for E1, E2 and E3"
        )
        return dash

    dash.total = sum(by_key[key].cost or 0.0 for key in TOTAL_STAGES)
    counts = [by_key[key].n for key in TOTAL_STAGES if by_key[key].n is not None]
    sample_note = f" (n={min(counts)}+ per stage, from the report)" if counts else ""
    if dash.total <= TARGET_PER_ITEM:
        dash.verdict = (
            f"PASS — E1+E2+E3 = ${dash.total:.4f}/item <= ${TARGET_PER_ITEM:.2f}/item"
            f"{sample_note}"
        )
    else:
        dash.verdict = (
            f"FAIL — E1+E2+E3 = ${dash.total:.4f}/item > ${TARGET_PER_ITEM:.2f}/item"
            f"{sample_note}"
        )
    return dash


def _unmeasured_dashboard(
    path: Path, *, report_state: str, reason: str, problems: list[str]
) -> Dashboard:
    dash = Dashboard(report_path=path, report_state=report_state, problems=list(problems))
    dash.rows = [
        StageRow(stage=stage, cost=None, n=None, reason=reason)
        for stage in (*STAGES, *EXTRA_STAGES)
    ]
    dash.total = None
    dash.total_reason = (
        f"E1+E2+E3 total is UNMEASURED — {reason}. {LOCAL_STAGES_REASON}"
    )
    dash.verdict = (
        f"UNMEASURED — the <= ${TARGET_PER_ITEM:.2f}/item verdict is UNREACHABLE, not passing: "
        "no measured $/item exists for any stage"
    )
    return dash


def _show(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _cell(value: float | None, n: int | None) -> str:
    if value is None:
        return UNMEASURED
    rendered = f"${value:.4f}"
    return f"{rendered} (n={n})" if n is not None else rendered


def table_lines(dash: Dashboard) -> list[str]:
    """The Markdown table, shared by the console and the doc so they cannot drift apart."""
    lines = [
        "| stage | what it covers | $/item | samples | status |",
        "|---|---|---|---|---|",
    ]
    for row in dash.rows:
        samples = str(row.n) if row.n is not None else "—"
        lines.append(
            f"| {row.stage.key} | {row.stage.label} | "
            f"{_cost_cell(row.cost)} | {samples} | {row.status} |"
        )
    lines.append(
        "| **E1+E2+E3** | **cost per item** | "
        f"{_cost_cell(dash.total)} | "
        f"{'—' if dash.total is None else _total_samples(dash)} | "
        f"{dash.total_status} |"
    )
    return lines


def _cost_cell(value: float | None) -> str:
    return UNMEASURED if value is None else f"${value:.4f}"


def _total_samples(dash: Dashboard) -> str:
    counts = [row.n for row in dash.rows if row.stage.key in TOTAL_STAGES and row.n is not None]
    if not counts:
        return "—"
    return f"{min(counts)}+"


def render(dash: Dashboard) -> str:
    """Console report: source, table, the reasons behind every hole, then the verdict."""
    lines = [
        "SnapList cost dashboard — E0-T4",
        f"report: {_show(dash.report_path)} ({dash.report_state})",
        f"target: <= ${TARGET_PER_ITEM:.2f}/item for E1+E2+E3",
        "",
        *table_lines(dash),
        "",
    ]
    # One line per distinct reason, listing the stages that share it: when the whole report is
    # missing, four identical paragraphs are noise and a reader stops reading them.
    grouped: dict[str, list[str]] = {}
    for row in dash.rows:
        if row.cost is None and row.reason:
            grouped.setdefault(row.reason, []).append(row.stage.key)
    if grouped:
        lines.append("Why the UNMEASURED cells are UNMEASURED:")
        for reason, keys in grouped.items():
            lines.append(f"  {', '.join(keys)}: {reason}")
        lines.append("")
    lines.append(f"E1+E2+E3 total: {dash.total_status}")
    if dash.total is None:
        lines.append(f"  {dash.total_reason}")
    lines.append(f"verdict: {dash.verdict}")
    lines += [
        "",
        "E4 is listed for context only: it is priced per render, is BLOCKED in this build "
        "(packages/vision/BLOCKED.md), and is not part of the per-item total.",
    ]
    if dash.problems:
        lines += ["", "Parse notes:"]
        lines += [f"  - {problem}" for problem in dash.problems]
    return "\n".join(lines)


def doc_block(dash: Dashboard, *, generated_at: str, git_sha: str) -> str:
    """The §13 block. Markers included, so `--update-doc` can replace it without guessing."""
    lines = [
        DOC_START,
        f"<!-- generated by infra/ci/gates/cost_dashboard.py --update-doc at {generated_at} "
        f"git_sha={git_sha} · source: {_show(dash.report_path)} ({dash.report_state}) -->",
        "",
        f"**Source:** `{_show(dash.report_path)}` — {dash.report_state}. "
        f"Target: `<= ${TARGET_PER_ITEM:.2f}/item` for E1+E2+E3.",
        "",
        *table_lines(dash),
        "",
        f"**E1+E2+E3 total:** `{dash.total_status}`"
        + ("" if dash.total is None else f" = `${dash.total:.4f}/item`"),
        "",
    ]
    if dash.total is None:
        lines += [f"> {dash.total_reason}", ""]
    lines += [f"**Verdict:** **{dash.verdict}**", ""]
    lines += [
        "E4 is listed for context only: it is priced per render, is BLOCKED in this build "
        "(`packages/vision/BLOCKED.md`), and is not part of the per-item total.",
        "",
    ]
    if dash.problems:
        lines += ["Parse notes:"] + [f"- {problem}" for problem in dash.problems] + [""]
    lines.append(DOC_END)
    return "\n".join(lines)


def update_doc(
    doc_path: Path, dash: Dashboard, *, generated_at: str, git_sha: str
) -> tuple[int, str]:
    """Replace the block between the markers. Refuses when the markers are absent.

    Appending would put a fresh table next to the old one, and a reader would have no way to
    tell which of the two is current — so a missing marker is a hard refusal (exit 2), with the
    marker text in the message.
    """
    if not doc_path.exists():
        return EXIT_NOT_EVALUABLE, f"refusing to write {_show(doc_path)}: file does not exist"
    original = doc_path.read_text()
    start = original.find(DOC_START)
    end = original.find(DOC_END)
    if start == -1 or end == -1 or end < start:
        return EXIT_NOT_EVALUABLE, (
            f"refusing to write {_show(doc_path)}: expected the markers {DOC_START} and "
            f"{DOC_END} around the dashboard block, and they are not both present in order. "
            "Nothing was written."
        )
    block = doc_block(dash, generated_at=generated_at, git_sha=git_sha)
    updated = original[:start] + block + original[end + len(DOC_END) :]
    if updated == original:
        return EXIT_OK, f"{_show(doc_path)}: §13 block already current, left unchanged"
    doc_path.write_text(updated)
    return EXIT_OK, f"wrote {_show(doc_path)}: §13 block replaced between the E0-T4 markers"


def resolve_git_sha(root: Path = ROOT, width: int = 12) -> str:
    """`HEAD` short SHA read from `.git`, no subprocess. Same rule as the regression gate."""
    git = root / ".git"
    try:
        if git.is_file():
            pointer = git.read_text().strip()
            if not pointer.startswith("gitdir:"):
                return "unknown"
            git = Path(pointer.split(":", 1)[1].strip())
            if not git.is_absolute():
                git = (root / git).resolve()
        head = (git / "HEAD").read_text().strip()
    except OSError:
        return "unknown"
    if not head.startswith("ref:"):
        return head[:width] if head else "unknown"
    ref = head.split(":", 1)[1].strip()
    try:
        return (git / ref).read_text().strip()[:width] or "unknown"
    except OSError:
        pass
    try:
        for line in (git / "packed-refs").read_text().splitlines():
            if line.startswith("#") or line.startswith("^"):
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0][:width]
    except OSError:
        return "unknown"
    return "unknown"


def exit_code(dash: Dashboard, *, fail_on_unmeasured: bool) -> int:
    """One place where the table and the process status are decided, so they cannot disagree."""
    if not fail_on_unmeasured:
        return EXIT_OK
    if dash.has_unmeasured:
        return EXIT_NOT_EVALUABLE
    if dash.over_budget:
        return EXIT_OVER_BUDGET
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python infra/ci/gates/cost_dashboard.py",
        description="E0-T4 cost dashboard: parse measured $/item out of evals/reports/cost.md.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"measured cost report (default: {DEFAULT_REPORT.relative_to(ROOT)})",
    )
    parser.add_argument(
        "--doc",
        type=Path,
        default=DEFAULT_DOC,
        help=f"document that receives the §13 block with --update-doc (default: "
        f"{DEFAULT_DOC.relative_to(ROOT)})",
    )
    parser.add_argument(
        "--update-doc",
        action="store_true",
        help="replace the block between the E0-T4 markers in --doc; without it, print only",
    )
    parser.add_argument(
        "--fail-on-unmeasured",
        action="store_true",
        help="exit 2 when anything is UNMEASURED (unreachable verdict is not a pass)",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 timestamp for the generated block (default: now, UTC)",
    )
    return parser


def main(argv: list[str] | None = None, dashboard: Dashboard | None = None) -> int:
    """CLI. `dashboard` is injectable so the tests do not touch the real report or doc."""
    args = build_parser().parse_args(argv)
    dash = dashboard if dashboard is not None else build_dashboard(args.report)
    generated_at = args.now or datetime.now(UTC).isoformat(timespec="seconds")

    print(render(dash))
    code = exit_code(dash, fail_on_unmeasured=args.fail_on_unmeasured)

    if args.update_doc:
        doc_code, message = update_doc(
            args.doc, dash, generated_at=generated_at, git_sha=resolve_git_sha()
        )
        print(message)
        if doc_code != EXIT_OK:
            return doc_code

    if args.fail_on_unmeasured and code == EXIT_NOT_EVALUABLE:
        print(
            "COST BUDGET NOT EVALUATED — exit 2. This is NOT a pass: the <= $0.03/item verdict "
            "was never computed."
        )
    elif args.fail_on_unmeasured and code == EXIT_OVER_BUDGET:
        print(
            f"COST BUDGET BREACHED — exit 1. E1+E2+E3 = ${dash.total:.4f}/item is over the "
            f"${TARGET_PER_ITEM:.2f}/item target."
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
