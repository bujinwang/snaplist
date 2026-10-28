"""`python -m evals.run --suite vision` — the one eval entry point every role calls.

Today it measures nothing: the golden manifest does not exist yet (the CV role writes it
in E0/T1 with 40 real photos) and the vision stages are stubs. So it prints `UNMEASURED`
for every target and exits 0 — a scaffold must not turn CI red just by existing.

**`--gate` and `--allow-inconclusive` are PARSED BUT NOT IMPLEMENTED.** They exist so the
Makefile targets (`make evals`, `make evals-strict`) have a stable interface, and so nobody
mistakes the current green for a passing gate. The real gate — four exit codes, the
nearest-rank percentile, the per-item `tool_time` summation, the single `excluded_rate > 0.20`
guard — is docs/PLAN.md ticket **E0-T5**, and that ticket owns every line of it. Do not
implement bits of it here in passing: a half-built gate is worse than a clearly absent one,
because the exit code starts looking trustworthy before the arithmetic is right.

Current behaviour until E0-T5 lands:

    --suite X                       report only: prints the table, exit 0, no banner
    --suite X --gate                RELEASE gate: refuses, exit 2 (no measurement exists)
    --suite X --allow-inconclusive  dev gate: prints the table, then the banner
                                    `GATE NOT EVALUATED — n=0. This is NOT a pass.`, exit 0

The banner exists because exit 0 in CI is simply green, and "not counted as a pass" would
otherwise survive only inside the report body — i.e. it would depend on somebody reading it,
which is the failure mode this whole line of work exists to remove.

Reports are written to evals/reports/<suite>.md and are the artifact QA pastes.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals import UNMEASURED

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "evals" / "golden" / "manifest.json"
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


GATE_NOT_EVALUATED_BANNER = "GATE NOT EVALUATED — n=0. This is NOT a pass."

RELEASE_GATE_REFUSAL = (
    "RELEASE GATE NOT AVAILABLE — E0-T5 is not implemented yet (docs/PLAN.md).\n"
    "Refusing to report a release-ready pipeline that nobody has measured."
)


def render(suite: str, *, banner: bool = True) -> str:
    g = golden_state()
    golden_line = (
        f"present, {g['entries']} entries" if g["present"] else "MISSING"
    )
    lines = [
        f"# eval report — suite `{suite}`",
        "",
        f"- generated: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- python: {platform.python_version()} on {platform.system()}/{platform.machine()}",
        f"- golden manifest: {golden_line}",
        "",
        "| metric | target | measured | epic |",
        "|---|---|---|---|",
    ]
    for t in TARGETS:
        if t.suite == suite or suite == "all":
            lines.append(f"| {t.metric} | {t.threshold} | {UNMEASURED} | {t.epic} |")
    lines += [
        "",
        f"Every metric above is `{UNMEASURED}`: the golden set and the pipeline stages do "
        "not exist yet.",
        "Per BRIEF.md honesty rules a metric is either measured against fixtures on disk "
        "or reported as UNMEASURED — never estimated.",
        "",
    ]
    if banner:
        # The banner defaults ON because the report FILE is the artifact that leaves this
        # machine: QA pastes it, a PR carries it, and six weeks from now it is all anyone
        # reads. The terminal copy is suppressed by the caller in pure report mode — a warning
        # printed on every routine run is a warning people learn to skip.
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
    report = render(args.suite, banner=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"{args.suite}.md"
    out.write_text(report)

    gating = args.gate or args.allow_inconclusive
    sys.stdout.write(report if gating else render(args.suite, banner=False))
    try:
        shown = out.relative_to(ROOT)
    except ValueError:  # tests point REPORTS at tmp_path
        shown = out
    print(f"wrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
