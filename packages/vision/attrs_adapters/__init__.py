"""VLM adapters for stage 4. Owner: CV role (E3-T2).

This package imports NO provider SDK. `get_extractor()` in `packages/vision/attrs.py`
resolves an adapter by name; each adapter imports its own client lazily inside `extract()`.
That is the E3-T1 DoD: a missing provider credential must never break `make test` or an
import, and swapping providers is a config change rather than a code change.

No vendor, price or accuracy figure is named anywhere in this package. docs/RUNBOOK.md §6
leaves that decision to Bert; until it is made, real attribute accuracy is UNMEASURED.
"""

from __future__ import annotations

__all__: list[str] = []
