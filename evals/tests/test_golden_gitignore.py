"""Regression guard for the copyright-frozen `evals/golden/**` zone.

Why this file exists: `.git/info/exclude` is local-only. After this clone was rebuilt on
2026-11-09 it no longer carried the golden exclusion rules, so a plain `git add -A` would
have staged all-rights-reserved photos, masks and the manifest. The protection now lives in
the tracked `.gitignore`, which travels with every clone. This suite asserts **real git
behaviour** (`check-ignore`, `ls-files`, `status --porcelain`) rather than the text of the
ignore rules: a comment saying "ignored" is not proof that git ignores anything.

This suite also pins a second, easily-lost defence: scratch artefacts written by tests into
`.pytest_tmp/` must stay VISIBLE to git. PR #9's first CI run failed at `dirty_check` because
the old tests wrote nine artefacts there; the fix was to move tests to
`tempfile.TemporaryDirectory()`, NOT to add a `.pytest_tmp/` rule to `.gitignore`. Ignoring
that path would blind the sentinel: a future test that pollutes the working tree would go
green in CI again. So this directory is deliberately NOT ignored, and that is asserted below
as real `check-ignore` behaviour rather than as a comment.

`tmp_path` is deliberately NOT used: on this host the sandbox raises EEXIST for pytest's temp
root, and these tests need no filesystem writes at all — only read-only git queries.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Binary / manifest probes that must be ignored by the tracked `.gitignore`.
FROZEN_BINARIES: tuple[str, ...] = (
    "evals/golden/photos/top-01.jpg",
    "evals/golden/masks/top-01.png",
    "evals/golden/masks_draft/top-01.png",
    "evals/golden/manifest.json",
)

#: The only five files git is allowed to track under `evals/golden/`.
EXPECTED_TRACKED: frozenset[str] = frozenset(
    {
        "evals/golden/README.md",
        "evals/golden/SOURCING.md",
        "evals/golden/manifest.draft.json",
        "evals/golden/masks/.gitkeep",
        "evals/golden/photos/.gitkeep",
    }
)

#: Tracked scaffolding that must never be ignored (negation rules + the draft manifest).
NOT_IGNORED: tuple[str, ...] = tuple(sorted(EXPECTED_TRACKED))

BINARY_SUFFIXES: frozenset[str] = frozenset({".jpg", ".jpeg", ".png"})

#: Test scratch artefacts. These must NOT be ignored: `dirty_check` runs
#: `git status --porcelain --untracked-files=all` on a clean checkout and fails on any line
#: outside its whitelist. A `.pytest_tmp/` ignore rule would hide a regression that writes
#: into the repo again. Contrast `.pytest_cache/`, which IS ignored by design (pytest's own
#: cache, never a test fixture output).
VISIBLE_SCRATCH: tuple[str, ...] = (
    ".pytest_tmp/genprop/gen/out/top-01.png",
    ".pytest_tmp/genprop/dry/golden/photos/top-01.jpg",
    ".pytest_tmp/genprop/dry/golden/manifest.draft.json",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run a read-only git command from the repo root; never raise on rc != 0."""
    return subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    """Render rc/stdout/stderr so a failing assertion shows what git actually did."""
    return f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"


def _porcelain_paths(line: str) -> list[str]:
    """Extract pathname(s) from one `git status --porcelain` line, incl. `R old -> new`."""
    if len(line) < 4:
        return []
    return [part.strip().strip('"') for part in line[3:].split(" -> ")]


@pytest.mark.parametrize("relative", FROZEN_BINARIES)
def test_frozen_binaries_are_ignored(relative: str) -> None:
    """`git check-ignore -q` rc == 0: `git add -A` cannot stage these paths."""
    result = _git("check-ignore", "-q", "--", relative)
    assert result.returncode == 0, f"{relative} is NOT ignored — {_detail(result)}"


@pytest.mark.parametrize("relative", NOT_IGNORED)
def test_scaffolding_is_not_ignored(relative: str) -> None:
    """The five tracked text files must stay visible to git."""
    result = _git("check-ignore", "-q", "--", relative)
    assert result.returncode != 0, f"{relative} IS ignored — {_detail(result)}"


@pytest.mark.parametrize(
    "relative",
    ("evals/golden/photos/.gitkeep", "evals/golden/masks/.gitkeep"),
)
def test_gitkeep_negation_survives_without_the_index(relative: str) -> None:
    """Even with `--no-index`, the `!` rule re-includes the two `.gitkeep` placeholders."""
    result = _git("check-ignore", "-q", "--no-index", "--", relative)
    assert result.returncode != 0, f"{relative} IS ignored by pattern — {_detail(result)}"


def test_photo_exclusion_comes_from_tracked_gitignore() -> None:
    """`check-ignore -v` must name `.gitignore`, not the local-only `.git/info/exclude`."""
    result = _git("check-ignore", "-v", "--", "evals/golden/photos/top-01.jpg")
    assert result.returncode == 0, f"no matching ignore rule — {_detail(result)}"
    lines = result.stdout.splitlines()
    assert lines, f"empty check-ignore -v output — {_detail(result)}"
    source = lines[0].split(":", 1)[0]
    assert source == ".gitignore", (
        f"exclusion source is {source!r}, expected tracked .gitignore — {_detail(result)}"
    )
    assert ".git/info/exclude" not in result.stdout, (
        f"local-only exclude is still in play — {_detail(result)}"
    )
    tracked = _git("ls-files", "--error-unmatch", ".gitignore")
    assert tracked.returncode == 0, f".gitignore is not tracked — {_detail(tracked)}"


def test_only_five_scaffolding_files_are_tracked() -> None:
    """`git ls-files evals/golden/` is exactly the five texts, with no binary or manifest."""
    result = _git("ls-files", "evals/golden/")
    assert result.returncode == 0, f"git ls-files failed — {_detail(result)}"
    tracked = frozenset(line for line in result.stdout.splitlines() if line)
    assert tracked == EXPECTED_TRACKED, (
        f"tracked golden set drifted: extra={sorted(tracked - EXPECTED_TRACKED)} "
        f"missing={sorted(EXPECTED_TRACKED - tracked)} — {_detail(result)}"
    )
    for relative in sorted(tracked):
        path = Path(relative)
        assert path.suffix.lower() not in BINARY_SUFFIXES, (
            f"binary committed under evals/golden/: {relative} — {_detail(result)}"
        )
        assert path.name != "manifest.json", (
            f"frozen manifest committed: {relative} — {_detail(result)}"
        )


@pytest.mark.parametrize("relative", VISIBLE_SCRATCH)
def test_test_scratch_artefacts_stay_visible_to_dirty_check(relative: str) -> None:
    """Do NOT add a `.pytest_tmp/` ignore rule — it would blind the CI dirty_check sentinel.

    `--no-index` makes this a pure rule check: it would fail even if a scratch file happened
    to be tracked, so the assertion is about the ignore rules and not about the current tree.
    """
    result = _git("check-ignore", "-q", "--no-index", "--", relative)
    assert result.returncode != 0, (
        f"{relative} IS ignored; a future test writing here would bypass CI dirty_check "
        f"(see PR #9, run 43044094502) — {_detail(result)}"
    )


def test_pytest_cache_is_still_ignored() -> None:
    """The contrast case: pytest's own cache is ignored; test FIXTURE output is not."""
    result = _git("check-ignore", "-q", "--", ".pytest_cache/probe.txt")
    assert result.returncode == 0, f".pytest_cache is not ignored — {_detail(result)}"


def test_porcelain_status_has_no_golden_binary_entries() -> None:
    """No golden binary shows up in status, now or after the 40 photos are restored."""
    result = _git("status", "--porcelain", "--untracked-files=all")
    assert result.returncode == 0, f"git status failed — {_detail(result)}"
    offenders: list[str] = []
    for line in result.stdout.splitlines():
        for path in _porcelain_paths(line):
            if not path.startswith("evals/golden/"):
                continue
            if Path(path).suffix.lower() in BINARY_SUFFIXES or Path(path).name == "manifest.json":
                offenders.append(line)
    assert not offenders, (
        f"golden binaries visible to git status (a git add -A would stage them): "
        f"{offenders} — {_detail(result)}"
    )
