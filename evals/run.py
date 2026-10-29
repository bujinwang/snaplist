"""`python -m evals.run --suite vision` — the one eval entry point every role calls.

It measures **one** metric today: segmentation mean IoU, over whichever masked set exists
(`evals/golden/` first, `evals/golden-provisional/` as a stand-in). Everything else prints
`UNMEASURED`, because no other stage has both an implementation and reference fixtures on
disk. The prerequisites are checked rather than assumed: no masked set, no model weights, or
a failing measurement each produce an `UNMEASURED` row **plus a line naming the reason**, since
"no number" and "no number because you have not run `make fetch-models`" are different fixes.

**`--gate` and `--allow-inconclusive` are PARSED BUT NOT IMPLEMENTED.** They exist so the
Makefile targets (`make evals`, `make evals-strict`) have a stable interface, and so nobody
mistakes a green exit for a passing gate. The real gate — four exit codes, the nearest-rank
percentile, the per-item `tool_time` summation, the single `excluded_rate > 0.20` guard — is
docs/PLAN.md ticket **E0-T5**, and that ticket owns every line of it. Do not implement bits of
it here in passing: a half-built gate is worse than a clearly absent one, because the exit code
starts looking trustworthy before the arithmetic is right. That is also why a measured row does
**not** turn into an exit code: the banner says `n=17 measured, but the gate itself is E0-T5`.

Current behaviour:

    --suite X                       report only: prints the table, exit 0, no banner
    --suite X --gate                RELEASE gate: refuses, exit 2 (the gate does not exist)
    --suite X --allow-inconclusive  dev gate: prints the table, then the banner
                                    `GATE NOT EVALUATED — …`. This is NOT a pass.`, exit 0

The banner exists because exit 0 in CI is simply green, and "not counted as a pass" would
otherwise survive only inside the report body — i.e. it would depend on somebody reading it,
which is the failure mode this whole line of work exists to remove.

Reports are written to evals/reports/<suite>.md and are the artifact QA pastes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals import UNMEASURED, git_sha

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "evals" / "golden" / "manifest.json"
PROVISIONAL = ROOT / "evals" / "golden-provisional" / "manifest.json"
REPORTS = ROOT / "evals" / "reports"


@dataclass(frozen=True, slots=True)
class Target:
    suite: str
    metric: str
    threshold: str
    epic: str


TARGETS: tuple[Target, ...] = (
    Target("vision", "segmentation mean IoU", ">= 0.92", "E1"),
    Target("vision", "rectify SSIM (inside mask)", ">= 0.85", "E2"),
    Target("vision", "prettify SSIM (inside mask)", ">= 0.80", "E2"),
    Target("vision", "prettify hallucinated-text rubric", ">= 4 / 5", "E2"),
    Target("vision", "attr category accuracy", ">= 0.95", "E3"),
    Target("vision", "attr colour accuracy", ">= 0.90", "E3"),
    Target("vision", "attr pattern accuracy", ">= 0.85", "E3"),
    Target("vision", "attr material accuracy", ">= 0.80", "E3"),
    Target("vision", "dedupe false-merge rate", "<= 0.05", "E3"),
    Target("vision", "cost per item (E1+E2+E3)", "<= $0.03", "E1-E3"),
    Target("vton", "on-model cost per render", "<= $0.10", "E4"),
    Target("vton", "quality gate pass rate", ">= 0.80", "E4"),
)


def golden_state() -> dict[str, object]:
    if not GOLDEN.exists():
        return {"present": False, "entries": 0, "missing": ["evals/golden/manifest.json"]}
    try:
        data = json.loads(GOLDEN.read_text())
    except json.JSONDecodeError as exc:
        return {"present": True, "entries": 0, "error": f"invalid JSON: {exc}"}
    entries = len(data) if isinstance(data, list) else len(data.get("items", []))
    return {"present": True, "entries": entries}


@dataclass(frozen=True, slots=True)
class Measurement:
    """A number, the fixture count behind it, and which set it came from.

    E0-T2's rule is "either a measured number with the fixture count that produced it, or the
    literal UNMEASURED". Keeping all three fields in one object is what makes that rule
    checkable instead of aspirational.
    """

    value: float
    n: int
    set_name: str
    note: str  # e.g. "masks are algorithmic, not hand-drawn"

    def cell(self) -> str:
        return f"{self.value:.4f} (n={self.n}, {self.set_name})"


def masked_set() -> Path | None:
    """The set a segmentation number may be measured on: golden first, provisional as a stand-in.

    Golden wins whenever it has any masks at all, because a number measured on Bert's photos
    is the number everybody wants; the provisional set only exists to keep the pipeline
    exercised until then.

    `SNAPLIST_GOLDEN_DIR` overrides both, and it exists for a licensing reason rather than a
    testing one: photos pulled off a search engine cannot legally be committed to this repo,
    but they can be measured against locally. A set pointed at from outside the tree is
    reproducible on one machine and explicitly not shipped — which is the honest shape for
    fixtures whose provenance is "found on the internet, all rights reserved".
    """
    override = os.environ.get("SNAPLIST_GOLDEN_DIR", "").strip()
    candidates = [Path(override).expanduser() / "manifest.json"] if override else []
    candidates += [GOLDEN, PROVISIONAL]

    for path in candidates:
        if not path.exists():
            continue
        try:
            entries = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(entries, list) and any(entry.get("mask_path") for entry in entries):
            return path.parent
    return None


def measurements() -> tuple[dict[str, Measurement], list[str]]:
    """Every metric this harness can actually measure right now, plus the reasons it could not.

    Today that is exactly one: segmentation IoU, because it is the only stage with both an
    implementation (`segment()`) and reference masks on disk. Everything else stays
    UNMEASURED — and a missing prerequisite is named out loud rather than silently skipped,
    because "the number is missing" and "the number is missing because you have not run
    `make fetch-models`" are different fixes.
    """
    measured: dict[str, Measurement] = {}
    notes: list[str] = []

    golden = masked_set()
    if golden is None:
        notes.append(
            "segmentation: UNMEASURED — no masked set on disk (evals/golden/manifest.json and "
            "evals/golden-provisional/manifest.json both lack an entry with a mask_path)"
        )
        return measured, notes

    try:
        from packages.vision.segment import weights_path  # local: keeps import cost off --help

        if not weights_path().is_file():
            notes.append(
                f"segmentation: UNMEASURED — model weights absent at {weights_path()} "
                "(run `make fetch-models`)"
            )
            return measured, notes
    except Exception as exc:  # noqa: BLE001 - any import failure is the same "cannot measure"
        notes.append(f"segmentation: UNMEASURED — vision package unavailable ({exc})")
        return measured, notes

    from evals.tools import seg_iou

    try:
        rows, meta = seg_iou.run(golden)
    except Exception as exc:  # noqa: BLE001 - report the reason, never a fake number
        reason = f"{type(exc).__name__}: {exc}"
        notes.append(f"segmentation: UNMEASURED — the measurement failed ({reason})")
        return measured, notes

    stats = seg_iou.summarise(rows)
    sources = sorted({row["mask_source"] for row in rows})
    measured["segmentation mean IoU"] = Measurement(
        value=stats["mean"],
        n=meta["n"],
        set_name="provisional" if "provisional" in golden.name else "golden",
        note=(
            f"backend {meta['backend']} on {meta['device']}, mask source: {', '.join(sources)}; "
            f"median {stats['median']:.4f}, worst {stats['min']:.4f}"
        ),
    )
    try:
        where = str(golden.relative_to(ROOT))
    except ValueError:
        where = f"{golden} (outside the repo, via SNAPLIST_GOLDEN_DIR — not committed)"
    notes.append(f"segmentation: measured on {where} (n={meta['n']})")
    if "provisional" in golden.name:
        notes.append(
            "that set is the web-sourced stand-in: algorithmic masks, chosen for colour "
            "separability, no dresses, no hard cases — the number is optimistic by construction"
        )
    return measured, notes


GATE_NOT_EVALUATED_BANNER = "GATE NOT EVALUATED — n=0. This is NOT a pass."

RELEASE_GATE_REFUSAL = (
    "RELEASE GATE NOT AVAILABLE — E0-T5 is not implemented yet (docs/PLAN.md).\n"
    "Refusing to report a release-ready pipeline that nobody has measured."
)


def render(
    suite: str,
    *,
    banner: bool = True,
    measured: dict[str, Measurement] | None = None,
    notes: list[str] | None = None,
) -> str:
    """Pure text. Measurements are injected so the report can be tested without a model."""
    measured = measured or {}
    notes = notes or []
    g = golden_state()
    golden_line = (
        f"present, {g['entries']} entries" if g["present"] else "MISSING"
    )
    lines = [
        f"# eval report — suite `{suite}`",
        "",
        f"- generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- git: `{git_sha()}`",
        f"- python: {platform.python_version()} on {platform.system()}/{platform.machine()}",
        f"- golden manifest: {golden_line}",
        "",
        "| metric | target | measured | epic |",
        "|---|---|---|---|",
    ]
    for t in TARGETS:
        if t.suite == suite or suite == "all":
            found = measured.get(t.metric)
            cell = found.cell() if found else UNMEASURED
            lines.append(f"| {t.metric} | {t.threshold} | {cell} | {t.epic} |")

    if measured:
        lines += ["", "## Where the measured numbers came from", ""]
        for metric, found in measured.items():
            lines.append(f"- **{metric}** — {found.set_name}, n={found.n}: {found.note}")
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines += [
            "",
            f"Every metric above is `{UNMEASURED}`: no stage has both an implementation and "
            "reference fixtures on disk yet.",
        ]
    if notes and not measured:
        lines += [""] + [f"- {note}" for note in notes]

    lines += [
        "",
        "Per BRIEF.md honesty rules a metric is either measured against fixtures on disk "
        "or reported as UNMEASURED — never estimated. A measured row names the set and the "
        "entry count that produced it; a set name containing `provisional` is not the golden "
        "set and its numbers are not release evidence.",
        "",
    ]
    if banner:
        # The banner defaults ON because the report FILE is the artifact that leaves this
        # machine: QA pastes it, a PR carries it, and six weeks from now it is all anyone
        # reads. The terminal copy is suppressed by the caller in pure report mode — a warning
        # printed on every routine run is a warning people learn to skip.
        if measured:
            total = sum(m.n for m in measured.values())
            lines += [
                f"GATE NOT EVALUATED — n={total} measured, but the gate itself is E0-T5. "
                "This is NOT a pass.",
                "",
            ]
        else:
            lines += [GATE_NOT_EVALUATED_BANNER, ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.run")
    p.add_argument("--suite", default="all", choices=["vision", "vton", "all"])
    # Parsed, not implemented. The gate itself is E0-T5; see the module docstring.
    p.add_argument(
        "--gate",
        action="store_true",
        help="gate mode (release). NOT IMPLEMENTED — parsed only, see E0-T5.",
    )
    p.add_argument(
        "--allow-inconclusive",
        action="store_true",
        help="downgrade INCONCLUSIVE to exit 0. NOT IMPLEMENTED — parsed only, see E0-T5.",
    )
    args = p.parse_args(argv)

    # Release gate: there is no measurement, so there is nothing to release against. Failing
    # loudly is the correct answer — a gate that cannot evaluate must never read as green.
    if args.gate and not args.allow_inconclusive:
        print(RELEASE_GATE_REFUSAL, file=sys.stderr)
        return 2

    # Two audiences, two rules: the file always carries the banner, the terminal only when a
    # gate is actually being asked for. Quiet terminal, honest artifact.
    measured, notes = measurements()
    report = render(args.suite, banner=True, measured=measured, notes=notes)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"{args.suite}.md"
    out.write_text(report)

    gating = args.gate or args.allow_inconclusive
    quiet = render(args.suite, banner=False, measured=measured, notes=notes)
    sys.stdout.write(report if gating else quiet)
    try:
        shown = out.relative_to(ROOT)
    except ValueError:  # tests point REPORTS at tmp_path
        shown = out
    print(f"wrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
