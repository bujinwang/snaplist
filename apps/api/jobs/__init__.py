"""`apps.api.jobs` — the job state machine (E5-T3) and the worker seam.

`state_machine.py` owns the transition table from `docs/CONTRACTS.md` §3. It is deliberately
separate from the router: the API can only *read* jobs, while a worker (Modal, in production)
moves them. Putting the table in one importable module means the test suite can prove the
illegal transitions are refused without needing a worker process, and the worker cannot
invent a sixth state at 3am.
"""

from __future__ import annotations

from apps.api.jobs.state_machine import (
    LEGAL_TRANSITIONS,
    IllegalJobTransition,
    apply_transition,
    fail,
    retry_or_fail,
)

__all__ = [
    "LEGAL_TRANSITIONS",
    "IllegalJobTransition",
    "apply_transition",
    "fail",
    "retry_or_fail",
]
