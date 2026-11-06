"""Try-on adapters for E4. Owner: VTON role (E4).

This package imports NO provider SDK at import time. `get_adapter()` in `packages/vision/vton.py`
resolves an adapter by name through importlib; each adapter imports its own HTTP/Modal client
lazily inside `render()`. That is the E4 DoD: a missing provider credential must never break
`make test` or an import, and swapping providers is a config change rather than a code change.

The quality gate (taxonomy + `gate()`) lives in `gate.py`; it is dependency-free so it can be
imported by `vton.py` at module load without pulling in any adapter.
"""

from __future__ import annotations

__all__: list[str] = []
