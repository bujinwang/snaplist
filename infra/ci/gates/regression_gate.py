"""E0-T4 — regression gate over the metrics `evals.run.measurements()` actually measures.

Three exit codes, and the reason there are only three:

    0  PASS         every baselined metric was compared and none regressed
    1  REGRESSION   at least one metric came in below `baseline - tolerance`
    2  INCONCLUSIVE no comparison happened, or a baselined metric is UNMEASURED

**`2` is not a soft pass.** `0` is a claim that a number was compared and held; `2` says the
gate could not form an opinion. The two must never collapse into each other, so the code path
that counts comparisons also decides the exit code: `compared == 0` is hard-wired to `2`.
A gate that returns `0` after comparing nothing is the exact failure mode this ticket exists to
remove, and it is the one a green CI run makes invisible.

How it measures: it **imports** `evals.run.measurements()` in-process. No subprocess, no
network call, no provider SDK, no model *provider* call — the gate adds no I/O of its own and
never writes `evals/reports/*`. (The harness itself still does whatever the harness does: with
the segmentation weights present it runs the local ONNX model over the masked set. That cost is
the measurement, not something this script introduces.) The git revision is resolved by reading
`.git/HEAD` and the ref file directly, again so the gate can run in a sandbox with no `git`
binary and no subprocess.

The baseline is a **JSON file the team writes on purpose** — see `baseline.json` and README.md.
`--update-baseline` is the only writer, it stamps `git_sha` + `generated_at` + the set each
number came from, and it refuses the two ways a baseline rots silently:

  * it will not write a metric that is not a finite measured number (there is no such thing as
    "UNMEASURED" in a baseline: an absent number is an absent row);
  * it will not write a metric whose provenance says its references are machine-made
    (`algorithmic:*`, `ai:*`, a provisional set) unless `--allow-machine-references` is passed,
    and it will not silently drop a metric the previous baseline had unless
    `--allow-dropped-metrics` is passed.

This checkout ships an **explicitly empty** baseline: `infra/ci/gates/baseline.json` has
`"metrics": {}`. That is deliberate. The only two numbers the harness can measure today are
`0.9299` segmentation IoU (n=20, references are `ai:`/`algorithmic:`, so a pipeline check and
not E1-T4's hand-mask number) and `0.9936` prettify SSIM (n=9, synthetic labels, a destruction
budget rather than a cleaning score). Enshrining either as "the bar" would turn a self-referential
number into release evidence. So the gate exits `2` with the command that would create a real
baseline, and prints every metric it knows about next to that reason.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

GATE_DIR = Path(__file__).resolve().parent
ROOT = GATE_DIR.parents[2]
DEFAULT_BASELINE = GATE_DIR / "baseline.json"

SCHEMA_VERSION = 1
DEFAULT_TOLERANCE = 0.01
UNMEASURED = "UNMEASURED"

EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_INCONCLUSIVE = 2

# Substrings that mark a measurement's provenance as "the references are not human-made".
# Deliberately the same vocabulary `evals/run.py` and `evals/tools/seg_iou.py` use, so the
# baseline guard and the report's own caveat can never disagree about the same set.
MACHINE_REFERENCE_MARKERS: tuple[str, ...] = (
    "not hand-drawn",
    "algorithmic",
    "ai:",
    "provisional",
)

_MASK_SOURCE_RE = re.compile(r"mask source:\s*([^.;]+)")


@dataclass(frozen=True, slots=True)
class Entry:
    """One metric: the number, the sample count behind it, and where it came from.

    `tolerance` belongs to the baseline, not to the measurement: how much a metric may drop
    before the drop counts as a regression is a policy decision, so it is stored next to the
    bar rather than guessed at comparison time. Current measurements are built with the gate's
    default and only ever use the baseline's value.
    """

    value: float
    n: int
    set_name: str
    provenance: str
    tolerance: float = DEFAULT_TOLERANCE


@dataclass(frozen=True, slots=True)
class Row:
    """One line of the decision table. `verdict` is the whole truth about this metric."""

    name: str
    baseline: Entry | None
    current: Entry | None
    tolerance: float | None
    verdict: str
    detail: str


STATE_OK = "ok"
STATE_MISSING = "missing"
STATE_EMPTY = "empty"
STATE_REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Baseline:
    """The parsed baseline file plus the metadata needed to quote it honestly.

    `state` is what lets `--update-baseline` tell "the delivered empty placeholder" (fine to
    fill) apart from "a file that failed validation" (not fine to overwrite without saying so).
    Both have zero entries and a non-empty `problems`; only the state distinguishes them, and
    matching on the message text instead would be a bug waiting for a reworded sentence.
    """

    path: Path
    entries: dict[str, Entry]
    generated_at: str
    git_sha: str
    schema_version: int
    problems: tuple[str, ...]
    state: str = STATE_OK

    @property
    def exists(self) -> bool:
        return self.path.exists()


def resolve_git_sha(root: Path = ROOT, width: int = 12) -> str:
    """Short SHA of `HEAD`, read from `.git` without a subprocess.

    Failure is the string `"unknown"`, never an exception: a baseline that cannot be stamped
    is still better than a gate that cannot run. Handles the two shapes a real checkout takes
    — a loose ref under `.git/refs/`, and a `.git` **file** containing `gitdir:` (worktrees,
    submodules) — plus packed refs, because `git gc` moves refs there without warning.
    """
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


def load_measurements() -> tuple[dict[str, Entry], list[str]]:
    """Current metrics, by importing the harness. Diagnostics come back as reasons, not raises.

    Every failure path returns `({}, [reason])` so the caller prints a reason and exits `2`.
    Swallowing the exception into an empty dict alone would be dishonest: "the harness crashed"
    and "nothing is measurable" would look identical.
    """
    if str(ROOT) not in sys.path:
        # Running `python infra/ci/gates/regression_gate.py` puts the *script* directory on
        # sys.path, not the repo root, so `evals` would not import. Set it here, once.
        sys.path.insert(0, str(ROOT))
    try:
        from evals.run import measurements
    except Exception as exc:  # noqa: BLE001 - any import failure is the same "cannot measure"
        return {}, [f"cannot import evals.run.measurements ({type(exc).__name__}: {exc})"]
    try:
        measured, notes = measurements()
    except Exception as exc:  # noqa: BLE001 - report the reason, never a fake number
        return {}, [f"evals.run.measurements() raised {type(exc).__name__}: {exc}"]

    entries: dict[str, Entry] = {}
    for name, measurement in measured.items():
        entries[name] = Entry(
            value=float(measurement.value),
            n=int(measurement.n),
            set_name=str(measurement.set_name),
            provenance=str(measurement.note),
            tolerance=DEFAULT_TOLERANCE,
        )
    return entries, [str(note) for note in notes]


def mask_sources(entry: Entry) -> list[str]:
    """The `mask source:` list a measurement's own note carries, or `[]` if it carries none.

    Derived rather than re-measured: the harness already decided which sources produced the
    number, and this gate must not form a second opinion about it. A provenance that does not
    name its sources yields an empty list — honestly empty, not "unknown".
    """
    found = _MASK_SOURCE_RE.search(entry.provenance)
    if found is None:
        return []
    return [part.strip() for part in found.group(1).split(",") if part.strip()]


def _as_number(value: object, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{where}: value is not a number ({value!r})")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{where}: value is not finite ({value!r})")
    return number


def _as_count(value: object, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where}: n is not an integer ({value!r})")
    if value < 0:
        raise ValueError(f"{where}: n is negative ({value!r})")
    return int(value)


def _as_text(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {value!r} is not a non-empty string")
    return value.strip()


def _as_key_map(value: object, *, where: str, regenerate: str) -> dict[str, object]:
    """A JSON object whose **keys** matter. Values are not inspected: only coverage is a claim."""
    if not isinstance(value, dict):
        raise ValueError(
            f"{where} is {type(value).__name__}, not an object — schema {SCHEMA_VERSION} requires "
            f"it to enumerate exactly the baselined metrics ({regenerate})"
        )
    return value


def _check_coverage(
    metrics: dict[str, Entry], payload: dict[str, object], *, regenerate: str
) -> None:
    """`sets` and `mask_sources` must cover the same metric names as `metrics`.

    This is the second half of the "0 means a number was compared" guard. Deleting a row from
    `metrics` by hand does not delete it from `sets`, so the mismatch is visible; without the
    check the gate would compare one fewer metric and print PASS — the same false green that
    `compared == 0` removes, arriving from the other direction. Ungating a metric has to be an
    explicit act (`--update-baseline --allow-dropped-metrics`), never a quiet edit.

    There is a residual the file format cannot close: an edit that removes a metric from **all
    three** maps at once is indistinguishable from a deliberately smaller baseline. That is
    recorded in infra/ci/gates/README.md; git history is the only thing that catches it.
    """
    names = set(metrics)
    for field_name in ("sets", "mask_sources"):
        mapping = _as_key_map(payload.get(field_name), where=field_name, regenerate=regenerate)
        extra = sorted(set(mapping) - names)
        missing = sorted(names - set(mapping))
        if extra or missing:
            raise ValueError(
                f"{field_name} does not describe the same metrics as metrics — "
                f"missing from {field_name}: {missing!r}, not in metrics: {extra!r}. "
                "A metric row deleted by hand would otherwise shrink the gate silently; "
                f"rebuild the file deliberately with {regenerate} --allow-dropped-metrics "
                "(that flag records the coverage you are giving up)"
            )


def parse_baseline_payload(
    payload: object, *, default_tolerance: float = DEFAULT_TOLERANCE
) -> tuple[dict[str, Entry], str, str, int]:
    """Validate a decoded baseline. Fail-closed: any malformed field is a `ValueError`.

    A baseline is a claim about the past. A half-parsed one silently compares fewer metrics
    than the author believed, which reads as PASS. So every field is required and typed, and a
    single bad record invalidates the whole file rather than being skipped.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"top level is {type(payload).__name__}, not an object")
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version is {schema_version!r}, this gate only understands {SCHEMA_VERSION}"
        )
    generated_at = _as_text(payload.get("generated_at"), where="generated_at")
    git_sha = _as_text(payload.get("git_sha"), where="git_sha")
    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, dict):
        raise ValueError(f"metrics is {type(raw_metrics).__name__}, not an object")

    entries: dict[str, Entry] = {}
    for name, record in raw_metrics.items():
        if not isinstance(record, dict):
            raise ValueError(f"metric {name!r}: record is not an object")
        where = f"metric {name!r}"
        tolerance = _as_number(
            record.get("tolerance", default_tolerance), where=f"{where}.tolerance"
        )
        if tolerance < 0:
            raise ValueError(f"{where}.tolerance is negative ({tolerance!r})")
        entries[name] = Entry(
            value=_as_number(record.get("value"), where=f"{where}.value"),
            n=_as_count(record.get("n"), where=f"{where}.n"),
            set_name=_as_text(record.get("set"), where=f"{where}.set"),
            provenance=_as_text(record.get("provenance"), where=f"{where}.provenance"),
            tolerance=tolerance,
        )
    _check_coverage(entries, payload, regenerate="--update-baseline")
    return entries, generated_at, git_sha, int(schema_version)


def read_baseline(path: Path, *, default_tolerance: float = DEFAULT_TOLERANCE) -> Baseline:
    """Read the baseline file. Missing, unreadable or malformed all land in `problems`."""
    if not path.exists():
        return Baseline(
            path=path,
            entries={},
            generated_at="",
            git_sha="",
            schema_version=SCHEMA_VERSION,
            problems=(f"baseline file does not exist: {_show(path)}",),
            state=STATE_MISSING,
        )
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        return Baseline(
            path, {}, "", "", SCHEMA_VERSION, (f"cannot read {_show(path)}: {exc}",), STATE_REJECTED
        )
    except json.JSONDecodeError as exc:
        return Baseline(
            path,
            {},
            "",
            "",
            SCHEMA_VERSION,
            (f"{_show(path)} is not valid JSON: {exc}",),
            STATE_REJECTED,
        )
    try:
        entries, generated_at, git_sha, version = parse_baseline_payload(
            payload, default_tolerance=default_tolerance
        )
    except ValueError as exc:
        return Baseline(
            path, {}, "", "", SCHEMA_VERSION, (f"{_show(path)}: {exc}",), STATE_REJECTED
        )
    problems: tuple[str, ...] = ()
    state = STATE_OK
    if not entries:
        state = STATE_EMPTY
        problems = (
            f"{_show(path)} is an explicitly empty baseline (\"metrics\": {{}}) — "
            "nothing to compare",
        )
    return Baseline(path, entries, generated_at, git_sha, version, problems, state)


def has_machine_references(entry: Entry) -> bool:
    """Whether a measurement's own provenance says its references are not human-made."""
    haystack = f"{entry.provenance} {entry.set_name}".lower()
    return any(marker in haystack for marker in MACHINE_REFERENCE_MARKERS)


def evaluate(
    baseline: dict[str, Entry],
    current: dict[str, Entry],
    *,
    current_problems: list[str] | None = None,
    default_tolerance: float = DEFAULT_TOLERANCE,
) -> tuple[list[Row], int]:
    """The whole decision, as a pure function of two dicts.

    A regression (exit 1) outranks an unknown (exit 2): a definite drop is more actionable than
    a definite absence, and both are printed. Anything measured but absent from the baseline is
    reported as `NOT IN BASELINE` and never fails the run — adding a new metric must not require
    a baseline edit in the same commit, or people stop adding metrics.
    """
    rows: list[Row] = []
    regressed: list[str] = []
    unmeasured: list[str] = []
    compared = 0
    reasons = list(current_problems or [])

    for name in sorted(baseline):
        base = baseline[name]
        tolerance = base.tolerance if base.tolerance is not None else default_tolerance
        now = current.get(name)
        if now is None:
            detail = _reason_for(name, reasons) or "no measurement for this metric in this run"
            rows.append(
                Row(
                    name=name,
                    baseline=base,
                    current=None,
                    tolerance=tolerance,
                    verdict=UNMEASURED,
                    detail=detail,
                )
            )
            unmeasured.append(name)
            continue

        compared += 1
        delta = now.value - base.value
        if now.value < base.value - tolerance:
            rows.append(
                Row(
                    name=name,
                    baseline=base,
                    current=now,
                    tolerance=tolerance,
                    verdict="REGRESSION",
                    detail=(
                        f"current < baseline - tolerance "
                        f"({base.value:.4f} - {tolerance:.4f} = {base.value - tolerance:.4f}, "
                        f"current {now.value:.4f}, delta {delta:+.4f})"
                    ),
                )
            )
            regressed.append(name)
        elif delta < 0:
            rows.append(
                Row(
                    name=name,
                    baseline=base,
                    current=now,
                    tolerance=tolerance,
                    verdict="PASS",
                    detail=f"dip inside tolerance (delta {delta:+.4f}, tolerance {tolerance:.4f})",
                )
            )
        else:
            rows.append(
                Row(
                    name=name,
                    baseline=base,
                    current=now,
                    tolerance=tolerance,
                    verdict="PASS",
                    detail=f"held or improved (delta {delta:+.4f})",
                )
            )

    for name in sorted(current):
        if name in baseline:
            continue
        rows.append(
            Row(
                name=name,
                baseline=None,
                current=current[name],
                tolerance=None,
                verdict="NOT IN BASELINE",
                detail="measured in this run, but the baseline has no row to compare against",
            )
        )

    if regressed:
        return rows, EXIT_REGRESSION
    if unmeasured or compared == 0:
        # `compared == 0` is spelled out rather than implied: this is the guard against a gate
        # that reads green because it read nothing.
        return rows, EXIT_INCONCLUSIVE
    return rows, EXIT_PASS


def _reason_for(name: str, reasons: list[str]) -> str:
    """The most specific diagnostic that mentions this metric, if the harness gave one."""
    for reason in reasons:
        if reason.startswith(f"{name}:") or reason.startswith(name):
            return reason
    return ""


def render(
    rows: list[Row],
    code: int,
    baseline: Baseline,
    *,
    current_problems: list[str] | None = None,
    default_tolerance: float = DEFAULT_TOLERANCE,
) -> str:
    """The metric table this gate decided on, plus the verdict and the fix for a non-zero code."""
    problems = list(current_problems or [])
    header = (
        f"{'metric':38} {'baseline':>10} {'current':>10} {'delta':>9} {'tol':>7}  verdict"
    )
    lines = [
        "SnapList regression gate — E0-T4",
        f"baseline: {_show(baseline.path)}",
    ]
    if baseline.entries:
        lines.append(
            f"          schema={baseline.schema_version} generated_at={baseline.generated_at} "
            f"git_sha={baseline.git_sha} metrics={len(baseline.entries)} "
            f"default_tolerance={default_tolerance:.4f}"
        )
    elif baseline.state == STATE_REJECTED:
        lines.append("          baseline REJECTED — no row could be trusted, nothing was compared")
    else:
        lines.append("          no baselined metric — nothing to compare")
    lines += ["", header, "-" * len(header)]

    if not rows:
        lines.append("(no rows: neither the baseline nor the harness produced a metric)")

    for row in rows:
        base_cell = f"{row.baseline.value:.4f}" if row.baseline else "—"
        current_cell = f"{row.current.value:.4f}" if row.current else UNMEASURED
        if row.baseline and row.current:
            delta_cell = f"{row.current.value - row.baseline.value:+.4f}"
        else:
            delta_cell = "—"
        tol_cell = f"{row.tolerance:.4f}" if row.tolerance is not None else "—"
        lines.append(
            f"{row.name:38} {base_cell:>10} {current_cell:>10} {delta_cell:>9} "
            f"{tol_cell:>7}  {row.verdict}"
        )
    lines.append("-" * len(header))

    for row in rows:
        if row.verdict in {UNMEASURED, "NOT IN BASELINE", "REGRESSION"}:
            lines.append(f"  {row.verdict} · {row.name}: {row.detail}")
    for problem in problems:
        if "UNMEASURED" in problem or "cannot" in problem or "raised" in problem:
            lines.append(f"  harness · {problem}")

    compared = sum(1 for row in rows if row.current is not None and row.baseline is not None)
    lines.append("")
    if code == EXIT_PASS:
        lines.append(
            f"GATE PASS — exit 0: {compared} metric(s) compared, none below baseline - tolerance. "
            "This is a regression check against a baseline; it is not a DoD verdict and not a "
            "release gate (that is E0-T5)."
        )
    elif code == EXIT_REGRESSION:
        names = ", ".join(row.name for row in rows if row.verdict == "REGRESSION")
        lines.append(
            f"GATE REGRESSION — exit 1: {names} came in below baseline - tolerance. "
            "Either fix the regression or, if the new number is correct and the bar was wrong, "
            "justify it and re-baseline on purpose."
        )
    else:
        lines.append("GATE INCONCLUSIVE — exit 2: this is NOT a pass.")
        if baseline.state == STATE_REJECTED:
            # The file exists but failed validation, so the advice is "fix or deliberately
            # rebuild it", never "create a baseline" — `--update-baseline` refuses this file
            # on purpose, and printing the create-a-baseline hint here would send the reader
            # to a command that answers with a refusal.
            reason = baseline.problems[0] if baseline.problems else "the baseline failed validation"
            lines.append(f"  reason: {reason}")
            lines.append(
                "  the file was rejected as a whole: no row in it was trusted, so none of the "
                "numbers above was gated. This is not the same as having no baseline."
            )
        elif not baseline.entries:
            reason = baseline.problems[0] if baseline.problems else "baseline has no metrics"
            lines.append(f"  reason: {reason}")
            lines.append(
                "  the only two metrics measurable in this checkout (segmentation IoU on "
                "`ai:`/`algorithmic:` references, prettify SSIM on synthetic labels) are "
                "pipeline checks, not DoD numbers — see infra/ci/gates/README.md."
            )
            lines.append(
                "  create a baseline on purpose: "
                "python infra/ci/gates/regression_gate.py --update-baseline"
            )
        else:
            missing = [row.name for row in rows if row.verdict == UNMEASURED]
            if missing:
                names = ", ".join(missing)
                lines.append(f"  reason: baselined metric(s) UNMEASURED this run: {names}")
            if compared == 0:
                lines.append(
                    f"  reason: 0 metric(s) compared — this checkout has {len(baseline.entries)} "
                    "baselined metric(s) and none of them could be measured"
                )
            lines.append(
                "  a metric that is unmeasured is not a metric that held: the bar stays open "
                "until the harness can produce the number again"
            )
    return "\n".join(lines)


def _show(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def build_payload(
    current: dict[str, Entry],
    *,
    git_sha: str,
    now: str,
    default_tolerance: float,
) -> dict[str, object]:
    """The baseline document `--update-baseline` writes. Explicit, self-describing, auditable."""
    metrics: dict[str, dict[str, object]] = {}
    for name in sorted(current):
        entry = current[name]
        metrics[name] = {
            "value": round(entry.value, 6),
            "n": entry.n,
            "set": entry.set_name,
            "provenance": entry.provenance,
            "tolerance": entry.tolerance,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "git_sha": git_sha,
        "tool": "infra/ci/gates/regression_gate.py --update-baseline",
        "default_tolerance": default_tolerance,
        "sets": {name: current[name].set_name for name in sorted(current)},
        "mask_sources": {name: mask_sources(current[name]) for name in sorted(current)},
        "metrics": metrics,
    }


def update_baseline(
    path: Path,
    current: dict[str, Entry],
    *,
    git_sha: str,
    now: str,
    default_tolerance: float,
    allow_machine_references: bool = False,
    allow_dropped_metrics: bool = False,
) -> tuple[int, list[str]]:
    """Write the baseline, or refuse with the reason. Returns `(exit_code, messages)`.

    Four refusals, each because the alternative writes a bar nobody can trust:

    1. nothing measured at all — an empty file is what this checkout already ships; rewriting it
       adds a timestamp and no information;
    2. a non-finite value — `nan` compares false against everything and would pass forever;
    3. a metric whose provenance says its references are machine-made, unless acknowledged —
       this is what stops `0.9299` IoU against `ai:`/`algorithmic:` references from becoming
       "the bar";
    4. a metric the existing baseline had that this run could not measure, unless acknowledged —
       otherwise a transient failure quietly deletes coverage and every later run passes.
    """
    messages: list[str] = []
    if not current:
        return EXIT_INCONCLUSIVE, [
            "refusing to write a baseline: this run measured nothing",
            "an empty baseline is not a weaker baseline, it is a file that cannot gate",
        ]

    broken = [name for name, entry in current.items() if not math.isfinite(entry.value)]
    if broken:
        return EXIT_INCONCLUSIVE, [
            f"refusing to write a baseline: non-finite value(s) for {', '.join(sorted(broken))}"
        ]

    machine = [name for name in sorted(current) if has_machine_references(current[name])]
    if machine and not allow_machine_references:
        messages.append(
            "refusing to write a baseline: these metrics were measured against references "
            f"that are not hand-made: {', '.join(machine)}"
        )
        messages.append(
            "  they are pipeline checks, not DoD numbers. If a pipeline-regression baseline is "
            "what you want, say so explicitly with --allow-machine-references and it will be "
            "recorded with its provenance."
        )
        return EXIT_INCONCLUSIVE, messages

    previous = read_baseline(path, default_tolerance=default_tolerance)
    if previous.state == STATE_REJECTED and not allow_dropped_metrics:
        # A file that failed validation is not a baseline: rebuilding over it would replace an
        # unknown number of rows with this run's rows, and nobody would have counted the change.
        messages.append(
            f"refusing to write a baseline: the existing {_show(path)} failed validation "
            f"({previous.problems[0] if previous.problems else 'unknown reason'})"
        )
        messages.append(
            "  rebuilding it would silently replace however many rows it claimed to have. "
            "Pass --allow-dropped-metrics to record that you mean it."
        )
        return EXIT_INCONCLUSIVE, messages
    dropped = sorted(name for name in previous.entries if name not in current)
    if dropped and not allow_dropped_metrics:
        messages.append(
            "refusing to write a baseline: the existing baseline has metric(s) this run could "
            f"not measure: {', '.join(dropped)}"
        )
        messages.append(
            "  dropping them would delete coverage and every later run would look green. "
            "Re-run until they are measurable, or pass --allow-dropped-metrics to record the loss."
        )
        return EXIT_INCONCLUSIVE, messages

    payload = build_payload(
        current, git_sha=git_sha, now=now, default_tolerance=default_tolerance
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")

    messages.append(f"wrote baseline: {_show(path)}")
    messages.append(f"  generated_at={now} git_sha={git_sha} metrics={len(payload['metrics'])}")
    for name in sorted(current):
        entry = current[name]
        before = previous.entries.get(name)
        change = " [new metric]" if before is None else f" [was {before.value:.4f}]"
        suffix = "  [machine references acknowledged]" if has_machine_references(entry) else ""
        messages.append(
            f"  {name}: {entry.value:.4f} (n={entry.n}, {entry.set_name}, "
            f"tolerance {entry.tolerance:.4f}){change}{suffix}"
        )
    for name in dropped:
        messages.append(f"  dropped: {name} (was {previous.entries[name].value:.4f})")
    if machine:
        messages.append(
            "  WARNING: this baseline gates a pipeline check only. It does not satisfy the E1-T4 "
            "hand-mask DoD and must not be quoted as one."
        )
    return EXIT_PASS, messages


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python infra/ci/gates/regression_gate.py",
        description="E0-T4 regression gate: compares measured metrics against a baseline.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help=f"baseline JSON (default: {DEFAULT_BASELINE.relative_to(ROOT)})",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write this run's measured metrics to --baseline (explicit, refuses UNMEASURED)",
    )
    parser.add_argument(
        "--allow-machine-references",
        action="store_true",
        help="acknowledge that a metric's references are machine-made when writing a baseline",
    )
    parser.add_argument(
        "--allow-dropped-metrics",
        action="store_true",
        help="allow --update-baseline to drop metrics the previous baseline had",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"tolerance for metrics whose baseline row carries none (default {DEFAULT_TOLERANCE})",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 timestamp to stamp into the baseline (default: now, UTC)",
    )
    return parser


def main(argv: list[str] | None = None, measure=None) -> int:  # noqa: ANN001 - callable seam
    """CLI. `measure` is injectable so the tests never load a model."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.tolerance < 0:
        print(f"invalid --tolerance {args.tolerance}: must be >= 0", file=sys.stderr)
        return EXIT_INCONCLUSIVE

    measure = measure or load_measurements
    current, problems = measure()

    if args.update_baseline:
        code, messages = update_baseline(
            args.baseline,
            current,
            git_sha=resolve_git_sha(),
            now=args.now or _now(),
            default_tolerance=args.tolerance,
            allow_machine_references=args.allow_machine_references,
            allow_dropped_metrics=args.allow_dropped_metrics,
        )
        print("SnapList regression gate — E0-T4 (baseline update)")
        for message in messages:
            print(message)
        if code == EXIT_PASS:
            print("BASELINE WRITTEN — the gate compares against it from the next run on.")
        else:
            print("BASELINE NOT WRITTEN — nothing was changed.")
        return code

    baseline = read_baseline(args.baseline, default_tolerance=args.tolerance)
    rows, code = evaluate(
        baseline.entries,
        current,
        current_problems=list(problems) + list(baseline.problems),
        default_tolerance=args.tolerance,
    )
    print(render(rows, code, baseline, current_problems=problems, default_tolerance=args.tolerance))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
