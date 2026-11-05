"""E0-T5 — the telemetry gate.

    python -m evals.telemetry --since 7d                          # report, always exit 0
    python -m evals.telemetry --since 7d --gate                   # exit 0/1/2/3
    python -m evals.telemetry --since 7d --gate --allow-inconclusive

The package is a functional core wrapped in an imperative shell. `compute` has no I/O and
receives `now` as an argument; `source` is the only module that opens a database, and it opens
one **read-only** — it must never construct `apps.api.repository.SqliteRepository`, whose
`__init__` runs `executescript(_SCHEMA)` and would turn a genuinely absent source (3a) into an
empty-but-present table (`n = 0`).

Nothing here invents a number. A row is either a measured value with the sample count that
produced it, the literal `-` (insufficient sample), or `UNMEASURED`/`DATA_UNAVAILABLE` with the
reason. See `docs/QA.md` for the backend boundary and the named residuals.
"""

from __future__ import annotations

__all__ = ["EXCLUDED_RATE_MAX"]

from evals.telemetry.spec import EXCLUDED_RATE_MAX
