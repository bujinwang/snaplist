"""Evaluation metrics (E0-T2 harness). One module per measured stage.

`evals/tools/` holds the standalone lab tools a role runs by hand; `evals/metrics/` holds the
measuring code `evals/run.py` calls. Keeping the split lets `evals/run.py` stay a report and
`evals/metrics/**` stay a library, which is what E2-T3 (`evals/metrics/**, evals/reports/
prettify.md`) needs in order to grow without touching the harness.
"""

from __future__ import annotations

__all__: list[str] = []
