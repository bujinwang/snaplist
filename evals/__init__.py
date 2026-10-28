"""SnapList eval harness — SCAFFOLD ONLY. Owner: CV role (E0 tickets, then E1..E4 metrics).

`evals/` is the honesty layer of this repo: it is where a number is either measured against
fixtures on disk or printed as `UNMEASURED`. Nothing here may invent a value.
"""

from __future__ import annotations

__all__ = ["UNMEASURED"]

UNMEASURED = "UNMEASURED"
