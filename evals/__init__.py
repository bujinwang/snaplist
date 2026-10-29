"""SnapList eval harness — SCAFFOLD ONLY. Owner: CV role (E0 tickets, then E1..E4 metrics).

`evals/` is the honesty layer of this repo: it is where a number is either measured against
fixtures on disk or printed as `UNMEASURED`. Nothing here may invent a value.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["UNMEASURED", "git_sha"]

UNMEASURED = "UNMEASURED"

REPO_ROOT = Path(__file__).resolve().parent.parent


def git_sha() -> str:
    """Short SHA of the commit a report was generated from, or 'unknown'.

    A measured number without the code that produced it is not reproducible, so both the
    harness report and the segmentation lab stamp this. Failure is a string, never an
    exception: a missing git binary must not stop a report from being written.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return "unknown"
    return result.stdout.strip() or "unknown"

