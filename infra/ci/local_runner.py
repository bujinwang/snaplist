"""Local equivalence runner for the E0-T3 CI pipeline.

WHAT THIS IS
============
A *local equivalence* runner. It proves three things and nothing more:

1. The SAME ``infra/ci/steps.sh all`` command sequence that ci.yml drives
   step-by-step runs GREEN (every step rc=0, ``CONTRACT_GATE: OK``) on a clean
   main worktree, reusing its existing ``.venv`` and ``node_modules``.
2. The gate HAS TEETH: five semantic mutations of ``docs/CONTRACTS.md`` each
   make ``contract_gate`` fail at the expected anchor, in a detached-HEAD
   worktree. Each RED run short-circuits at ``contract_gate`` (step 2), so the
   later steps do NOT execute and no dependency install is needed.
3. The WIRING EXISTS: ci.yml references all steps in STEP_ORDER, in the same order, and
   ``contract_gate`` precedes ``install``.

It does NOT prove GitHub Actions executed. GH-side checkout/setup-* actions,
required status checks, real PR/job URLs, cache hit rate and GH runtime are
UNVERIFIED / UNMEASURED. The ``evals`` step's rc=0 is a dev/CI downgrade
(``GATE NOT EVALUATED``, n=0): NOT a pass, and NOT used as evidence that any
telemetry gate passed. GREEN is judged only by every step rc=0 plus
``CONTRACT_GATE: OK``.

Writes no file inside the repository: mutations happen in a temporary git
worktree outside the repo, and evidence goes to stdout (optionally ``--out``
to a path outside the repo). Standard library only.
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONTRACTS_REL = Path("docs") / "CONTRACTS.md"
STEPS_REL = Path("infra") / "ci" / "steps.sh"
CHECK_REL = Path("infra") / "ci" / "check_contracts.py"
WORKFLOW_REL = Path(".github") / "workflows" / "ci.yml"

STEP_START_RE = re.compile(r"^STEP ([a-z_]+): (.*)$")
STEP_RC_RE = re.compile(r"^STEP ([a-z_]+) rc=([0-9]+)$")
GATE_FAIL_RE = re.compile(r"CONTRACT_GATE: FAIL ([A-Za-z0-9]+)")
GATE_OK_RE = re.compile(r"CONTRACT_GATE: OK \([0-9]+ anchors checked\)")
WIRING_RE = re.compile(r"run: bash infra/ci/steps\.sh ([a-z_]+)")


class RunnerError(Exception):
    """A runner pre/post-condition was violated."""


class Transcript:
    """Mirror evidence to stdout while accumulating it for ``--out``."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def dump(self) -> str:
        return "\n".join(self.lines) + "\n"


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a command with stderr merged into stdout; never raise on rc != 0."""
    return subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _git(args: list[str]) -> subprocess.CompletedProcess:
    """Run git in the MAIN worktree with stdout/stderr separated."""
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )


def _git_ok(args: list[str]) -> str:
    result = _git(args)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RunnerError(f"git {' '.join(args)} failed (rc={result.returncode}): {detail}")
    return result.stdout


def _toolchain(t: Transcript) -> None:
    for argv in (
        ["python3", "--version"],
        ["node", "--version"],
        ["pnpm", "--version"],
        ["uv", "--version"],
    ):
        result = _run(argv, REPO)
        value = (result.stdout or "").strip()
        t.emit(f"  {' '.join(argv):<24} rc={result.returncode}  {value}")


def _summarize(output: str) -> list[tuple[str, str, int, str]]:
    """Split a ``steps.sh all`` transcript into (name, command, rc, tail)."""
    lines = output.splitlines()
    steps: list[tuple[str, str, int, str]] = []
    index = 0
    while index < len(lines):
        start = STEP_START_RE.fullmatch(lines[index])
        if start is None:
            index += 1
            continue
        name, command = start.group(1), start.group(2)
        body: list[str] = []
        rc: int | None = None
        cursor = index + 1
        while cursor < len(lines):
            end = STEP_RC_RE.fullmatch(lines[cursor])
            if end is not None and end.group(1) == name:
                rc = int(end.group(2))
                break
            body.append(lines[cursor])
            cursor += 1
        if rc is None:
            break
        tail = " | ".join(body[-6:])[-200:]
        steps.append((name, command, rc, tail))
        index = cursor + 1
    return steps


def _step_names() -> list[str]:
    """Read STEP_ORDER from steps.sh so the runner has no duplicate list."""
    text = (REPO / STEPS_REL).read_text(encoding="utf-8")
    block = re.search(r"readonly STEP_ORDER=\((.*?)\)", text, re.DOTALL)
    if block is None:
        raise RunnerError("cannot find STEP_ORDER in steps.sh")
    names = re.findall(r"[a-z_]+", block.group(1))
    if not names:
        raise RunnerError("STEP_ORDER in steps.sh is empty")
    return names


def _check_wiring(names: list[str], t: Transcript) -> None:
    """Verify ci.yml references every step in order, with no escape hatches."""
    raw = (REPO / WORKFLOW_REL).read_text(encoding="utf-8")
    # Strip YAML comments so prose like "no continue-on-error" is not mistaken
    # for actual configuration.
    code = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    found = WIRING_RE.findall(code)
    t.emit(f"  steps.sh STEP_ORDER ({len(names)}): {', '.join(names)}")
    t.emit(f"  ci.yml run lines    ({len(found)}): {', '.join(found)}")
    if found != names:
        raise RunnerError(f"ci.yml step order {found} != steps.sh STEP_ORDER {names}")
    for forbidden in ("continue-on-error", "|| true", "|| echo", "if: always()"):
        if forbidden in code:
            raise RunnerError(f"ci.yml contains forbidden escape hatch: {forbidden!r}")
    if names.index("contract_gate") > names.index("install"):
        raise RunnerError("contract_gate must precede install in ci.yml")
    t.emit("  WIRING: OK (all steps referenced in order; contract_gate before install;")
    t.emit("           no continue-on-error / || true / || echo / if: always())")


def _banner(t: Transcript) -> None:
    t.emit("=== SnapList E0-T3 local equivalence runner ===")
    t.emit("LOCAL EQUIVALENCE RUNNER: proves (1) the same steps.sh command sequence")
    t.emit("runs GREEN, (2) the doc<->code gate has teeth (5/5 semantic mutations RED),")
    t.emit("(3) the wiring exists (ci.yml + this runner both call steps.sh).")
    t.emit("It does NOT prove GitHub Actions executed. GH-side checkout/setup-*")
    t.emit("actions, required status checks, real PR/job URLs, cache hits and GH")
    t.emit("runtime are UNVERIFIED / UNMEASURED.")
    t.emit("evals rc=0 = dev/CI downgrade (GATE NOT EVALUATED, n=0): NOT a pass.")
    t.emit("")


def _environment(t: Transcript) -> tuple[str, bytes, str]:
    """Snapshot HEAD, toolchain, pristine bytes and pre-run git status."""
    head = _git_ok(["rev-parse", "HEAD"]).strip()
    pre_status = _git_ok(["status", "--porcelain"])
    pristine = (REPO / CONTRACTS_REL).read_bytes()
    pristine_sha = hashlib.sha256(pristine).hexdigest()
    t.emit("## ENVIRONMENT (read from this machine; nothing invented)")
    t.emit(f"  git rev-parse HEAD = {head}")
    t.emit("  origin/master      = UNVERIFIED (not queried by this runner)")
    _toolchain(t)
    t.emit(f"  PRE git status --porcelain = {pre_status!r}")
    t.emit(f"  sha256(pristine docs/CONTRACTS.md) = {pristine_sha}")
    t.emit("  untracked lines above: `?? .kilo/` (long-lived scratch dir) + the")
    t.emit("  five E0-T3 deliverables, untracked until the lead commits them; a")
    t.emit("  post-commit / real-PR checkout has an EMPTY status here.")
    t.emit("")
    return pre_status, pristine, pristine_sha


def _run_green(names: list[str], t: Transcript) -> None:
    t.emit("## GREEN — bash infra/ci/steps.sh all (MAIN worktree)")
    result = _run(["bash", str(STEPS_REL), "all"], REPO)
    steps = _summarize(result.stdout)
    t.emit(f"  {'step':<18} {'rc':>3}  command")
    for name, command, rc, tail in steps:
        t.emit(f"  {name:<18} {rc:>3}  {command}")
        if tail:
            t.emit(f"      tail: {tail}")
    t.emit(f"  overall rc = {result.returncode}")
    if result.returncode != 0:
        for line in result.stdout.splitlines()[-30:]:
            t.emit(f"      {line}")
        raise RunnerError(f"GREEN baseline rc={result.returncode}, expected 0")
    if [name for name, _, _, _ in steps] != names:
        raise RunnerError("GREEN step sequence does not match steps.sh STEP_ORDER")
    if any(rc != 0 for _, _, rc, _ in steps):
        raise RunnerError("GREEN has a step with non-zero rc")
    if GATE_OK_RE.search(result.stdout) is None:
        raise RunnerError("GREEN transcript has no CONTRACT_GATE: OK")
    t.emit("  evals rc=0 -> dev/CI downgrade: GATE NOT EVALUATED, NOT a pass.")
    t.emit("  It is NOT counted as evidence of a passing telemetry gate.")
    t.emit("  VERDICT: GREEN — every step rc=0 AND CONTRACT_GATE: OK")


def _make_worktree(t: Transcript) -> Path:
    path = Path(tempfile.mkdtemp(prefix="snaplist-e0t3-"))
    path.rmdir()  # `git worktree add` needs a path that does not exist yet
    result = _git(["worktree", "add", "--detach", str(path), "HEAD"])
    if result.returncode != 0:
        raise RunnerError(
            f"git worktree add --detach {path} HEAD failed: {result.stderr.strip()}"
        )
    t.emit(f"## WORKTREE — git worktree add --detach {path} HEAD")
    t.emit(f"  rc = {result.returncode}")
    # HEAD (3cc029e) predates E0-T3, so its checkout does NOT contain these two
    # files. Copy them as REAL FILES, never symlinks: check_contracts.py resolves
    # its repo root from __file__, so a symlink back into the main worktree would
    # silently read the pristine CONTRACTS.md and produce a false green. This
    # emulates the post-commit PR/CI checkout where both files are tracked.
    (path / "infra" / "ci").mkdir(parents=True, exist_ok=True)
    for rel in (STEPS_REL, CHECK_REL):
        (path / rel).write_bytes((REPO / rel).read_bytes())
    t.emit("  copied as real files (NOT symlinks): steps.sh, check_contracts.py")
    t.emit("  no install, no .venv/node_modules symlink: contract_gate is step 2")
    t.emit("  and short-circuits `all` before any dependency is needed.")
    return path


def _replace(data: bytes, old: bytes, new: bytes, label: str) -> bytes:
    if old not in data:
        raise RunnerError(f"{label}: byte pattern not found: {old!r}")
    return data.replace(old, new, 1)


def mutate_m1(data: bytes) -> bytes:
    """§7 headline EVENTS_VERSION drifts away from analytics.ts."""
    return _replace(data, b"EVENTS_VERSION = 1.4.0", b"EVENTS_VERSION = 9.9.9", "M1")


def mutate_m2(data: bytes) -> bytes:
    """Delete the 7th event row from the §7 table."""
    pattern = re.compile(rb"(?m)^\| 7 \| `export_delivered`[^\n]*\n")
    changed, count = pattern.subn(b"", data, count=1)
    if count != 1:
        raise RunnerError("M2: §7 table row 7 (export_delivered) not found")
    return changed


def mutate_m3(data: bytes) -> bytes:
    """Drop the trailing `other` value from the §1 GarmentCategory line."""
    old = b"GarmentCategory  top | bottom | dress | outerwear | shoe | accessory | other"
    new = b"GarmentCategory  top | bottom | dress | outerwear | shoe | accessory"
    return _replace(data, old, new, "M3")


def mutate_m4(data: bytes) -> bytes:
    """Prose event count drifts away from len(ANALYTICS_EVENTS)."""
    return _replace(data, b"**Seven events.**", b"**Six events.**", "M4")


def mutate_m5(data: bytes) -> bytes:
    """Remove the `frozen` sentinel from the Status header."""
    old = "v1.0.0 \u2014 frozen".encode()
    new = "v1.0.0 \u2014 draft".encode()
    return _replace(data, old, new, "M5")


MUTATIONS = (
    ("M1", "EVENTS_VERSION = 1.4.0 -> 9.9.9", mutate_m1, "A1"),
    ("M2", "delete §7 table row 7 (export_delivered)", mutate_m2, "A2"),
    ("M3", "drop `other` from §1 GarmentCategory line", mutate_m3, "A6"),
    ("M4", "**Seven events.** -> **Six events.**", mutate_m4, "A3"),
    ("M5", "header `frozen` -> `draft`", mutate_m5, "A5"),
)


def _run_red(worktree: Path, original: bytes, t: Transcript) -> list[dict]:
    """Inject each mutation, prove two RED runs, restore bytes immediately."""
    target = worktree / CONTRACTS_REL
    results: list[dict] = []
    for label, description, mutate, expected in MUTATIONS:
        mutated = mutate(original)
        if mutated == original:
            raise RunnerError(f"{label}: mutation was a no-op")
        target.write_bytes(mutated)
        mutated_sha = hashlib.sha256(mutated).hexdigest()
        all_run = _run(["bash", str(STEPS_REL), "all"], worktree)
        gate_run = _run(["bash", str(STEPS_REL), "contract_gate"], worktree)
        # Restore BEFORE any assertion, so a failure cannot leave a broken file.
        target.write_bytes(original)
        anchors = sorted(set(GATE_FAIL_RE.findall(gate_run.stdout)))
        short_circuit = "STEP install:" not in all_run.stdout
        red = (
            all_run.returncode != 0
            and gate_run.returncode != 0
            and "CONTRACT_GATE: FAIL" in all_run.stdout
            and "CONTRACT_GATE: FAIL" in gate_run.stdout
            and short_circuit
        )
        t.emit(f"  {label} {description}")
        t.emit(
            f"    all rc={all_run.returncode}  contract_gate rc={gate_run.returncode}"
            f"  short-circuit(before install)={short_circuit}"
        )
        t.emit(f"    FAIL anchors = {anchors}  (expected {expected})")
        for line in gate_run.stdout.splitlines()[-2:]:
            t.emit(f"      {line.strip()}")
        if not red:
            raise RunnerError(f"{label}: expected RED, got a non-short-circuit result")
        if expected not in anchors:
            raise RunnerError(f"{label}: expected anchor {expected}, got {anchors}")
        results.append(
            {
                "label": label,
                "anchors": ",".join(anchors),
                "all_rc": all_run.returncode,
                "gate_rc": gate_run.returncode,
                "sha": mutated_sha,
                "red": red,
            }
        )
    return results


def _run_restore(worktree: Path, original: bytes, pristine_sha: str, t: Transcript) -> str:
    """Write original bytes back and prove contract_gate is green again."""
    target = worktree / CONTRACTS_REL
    target.write_bytes(original)
    restored_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    result = _run(["bash", str(STEPS_REL), "contract_gate"], worktree)
    t.emit("## RESTORE — original bytes written back, contract_gate re-run")
    t.emit(f"  contract_gate rc = {result.returncode}")
    for line in result.stdout.splitlines()[-2:]:
        t.emit(f"      {line.strip()}")
    if result.returncode != 0 or GATE_OK_RE.search(result.stdout) is None:
        raise RunnerError("RESTORE did not return CONTRACT_GATE: OK with rc=0")
    t.emit(f"  sha256(pristine) = {pristine_sha}")
    t.emit(f"  sha256(restored) = {restored_sha}")
    t.emit(f"  pristine == restored : {pristine_sha == restored_sha}")
    if pristine_sha != restored_sha:
        raise RunnerError("sha256(pristine) != sha256(restored)")
    return restored_sha


def _run_junk_probe(worktree: Path, t: Transcript) -> None:
    """Prove planted untracked junk turns ``dirty_check`` RED.

    QA found that plain ``git status --porcelain`` collapses ``?? infra/ci/``
    into one directory line and therefore silently tolerates junk dropped
    inside it. With ``--untracked-files=all`` plus an exact filename whitelist,
    each junk file gets its own line and must be rejected. Runs entirely inside
    the disposable worktree; every planted file is removed in a ``finally``
    block, and we assert the baseline is GREEN again afterwards. A modified
    TRACKED file is probed too, since that produces a `` M`` line rather than
    a ``??`` line.
    """
    t.emit("## JUNK PROBE — planted files must turn dirty_check RED")
    step = ["bash", str(STEPS_REL), "dirty_check"]
    junk = (
        worktree / "infra" / "ci" / "qa-garbage.bin",
        worktree / ".github" / "workflows" / "qa-garbage.yml",
        worktree / "qa-stray.txt",
    )
    baseline = _run(step, worktree)
    t.emit(f"  baseline (clean worktree, 2 copied CI files) rc = {baseline.returncode}")
    if baseline.returncode != 0:
        raise RunnerError(f"dirty_check baseline should be green: {baseline.stdout}")
    try:
        for path in junk:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"qa planted junk\n")
            result = _run(step, worktree)
            unexpected = [
                line.strip()
                for line in result.stdout.splitlines()
                if "unexpected status line" in line
            ]
            t.emit(f"  planted {path.relative_to(worktree)} -> rc = {result.returncode}")
            for line in unexpected:
                t.emit(f"      {line}")
            if result.returncode == 0:
                raise RunnerError(f"junk {path.name} was silently tolerated (FALSE GREEN)")
            if not unexpected:
                raise RunnerError(f"junk {path.name}: no 'unexpected status line' diagnostic")
            path.unlink()
            again = _run(step, worktree)
            if again.returncode != 0:
                raise RunnerError(f"dirty_check not green after removing {path.name}")

        tracked = worktree / "pyproject.toml"
        saved = tracked.read_bytes()
        try:
            tracked.write_bytes(saved + b"\n# qa tracked edit\n")
            result = _run(step, worktree)
            t.emit(f"  modified tracked pyproject.toml -> rc = {result.returncode}")
            for line in result.stdout.splitlines():
                if "unexpected status line" in line:
                    t.emit(f"      {line.strip()}")
            if result.returncode == 0:
                raise RunnerError("tracked-file modification was silently tolerated")
        finally:
            tracked.write_bytes(saved)
    finally:
        for path in junk:
            path.unlink(missing_ok=True)
    after = _run(step, worktree)
    t.emit(f"  after all junk removed rc = {after.returncode}")
    if after.returncode != 0:
        raise RunnerError("dirty_check not green after junk cleanup")
    t.emit("  VERDICT: 3/3 planted junk + 1 tracked edit RED; baseline/post GREEN")


def _cleanup(worktree: Path, pre_status: str, t: Transcript) -> None:
    """Remove the worktree and prove the main worktree is byte-for-byte unchanged."""
    removed = _git(["worktree", "remove", "--force", str(worktree)])
    _git(["worktree", "prune"])
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    listing = _git_ok(["worktree", "list"])
    post_status = _git_ok(["status", "--porcelain"])
    t.emit("## CLEANUP / ZERO-POLLUTION PROOF")
    t.emit(f"  git worktree remove --force rc = {removed.returncode}; prune done")
    t.emit(f"  worktree still in `git worktree list` = {str(worktree) in listing}")
    t.emit(f"  PRE  git status --porcelain = {pre_status!r}")
    t.emit(f"  POST git status --porcelain = {post_status!r}")
    t.emit(f"  verbatim equal = {pre_status == post_status}")
    if str(worktree) in listing:
        raise RunnerError("worktree still registered after cleanup")
    if pre_status != post_status:
        raise RunnerError("main worktree git status changed (pollution)")
    tracked = [line for line in post_status.splitlines() if line and not line.startswith("?? ")]
    t.emit(f"  tracked-file modifications in snapshot = {len(tracked)}")
    if tracked:
        raise RunnerError(f"unexpected tracked modifications: {tracked}")
    t.emit("  VERDICT: worktree removed; main worktree unchanged (zero pollution)")
    t.emit("  (`?? .kilo/` is the only long-lived untracked scratch dir allowed)")


def _summary(results: list[dict], pristine_sha: str, restored_sha: str, t: Transcript) -> None:
    t.emit("## EVIDENCE TABLE")
    t.emit("  mutation | FAIL anchor(s) | all rc | gate rc | sha256(mut)[:12] | verdict")
    for row in results:
        t.emit(
            f"  {row['label']:<8} | {row['anchors']:<13} | {row['all_rc']:>6} | "
            f"{row['gate_rc']:>7} | {row['sha'][:12]:<19} | "
            f"{'RED' if row['red'] else 'NOT-RED'}"
        )
    red_count = sum(1 for row in results if row["red"])
    t.emit(f"  RED count = {red_count}/{len(results)}   RESTORE green = 1/1")
    t.emit(f"  sha256(pristine) = {pristine_sha}")
    t.emit(f"  sha256(mutated)  = {results[0]['sha']}   (M1, representative)")
    t.emit(f"  sha256(restored) = {restored_sha}")
    t.emit(f"  pristine == restored = {pristine_sha == restored_sha}")
    if red_count != len(results):
        raise RunnerError(f"only {red_count}/{len(results)} mutations turned red")


def _write_out(raw: Path, t: Transcript) -> None:
    resolved = raw.resolve()
    if resolved == REPO or REPO in resolved.parents:
        t.emit(f"  --out must be OUTSIDE the repo; refusing {resolved}")
        return
    t.emit(f"  transcript also written to {resolved}")
    resolved.write_text(t.dump(), encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="E0-T3 local CI equivalence runner (evidence to stdout)."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="also write the transcript here; must be OUTSIDE the repository",
    )
    return parser.parse_args()


def main() -> int:
    """Run the full GREEN / RED / RESTORE / CLEANUP sequence."""
    args = _parse_args()
    t = Transcript()
    worktree: Path | None = None
    try:
        _banner(t)
        pre_status, pristine, pristine_sha = _environment(t)
        names = _step_names()
        t.emit("## WIRING CHECK (structural; NOT a substitute for the RED/GREEN runs)")
        _check_wiring(names, t)
        t.emit("")
        _run_green(names, t)
        t.emit("")
        worktree = _make_worktree(t)
        worktree_bytes = (worktree / CONTRACTS_REL).read_bytes()
        worktree_sha = hashlib.sha256(worktree_bytes).hexdigest()
        if worktree_sha != pristine_sha:
            raise RunnerError("worktree CONTRACTS.md differs from pristine main worktree")
        t.emit(f"  sha256(worktree pristine) = {worktree_sha} (matches)")
        t.emit("")
        t.emit("## RED — five semantic mutations, each in the worktree")
        results = _run_red(worktree, pristine, t)
        t.emit("")
        restored_sha = _run_restore(worktree, pristine, pristine_sha, t)
        t.emit("")
        _run_junk_probe(worktree, t)
        t.emit("")
        _cleanup(worktree, pre_status, t)
        worktree = None
        t.emit("")
        _summary(results, pristine_sha, restored_sha, t)
        t.emit("")
        t.emit("RESULT: PASS (LOCAL EQUIVALENCE ONLY; GitHub Actions side UNVERIFIED)")
        return 0
    except RunnerError as err:
        t.emit(f"RESULT: FAIL {err}")
        return 1
    except Exception as err:
        t.emit(f"RESULT: FAIL (UNEXPECTED {type(err).__name__}) {err}")
        return 1
    finally:
        if worktree is not None:
            _git(["worktree", "remove", "--force", str(worktree)])
            _git(["worktree", "prune"])
            if worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
            t.emit("  (best-effort worktree cleanup after an early failure)")
        if args.out is not None:
            _write_out(args.out, t)


if __name__ == "__main__":
    sys.exit(main())
