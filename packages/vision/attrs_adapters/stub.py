"""E3-T1 `stub` extractor — a tests/CI fixture, deliberately NOT a VLM.

PLAN.md E3-T1 requires at least two extractors resolvable by name, "stub for tests and CI,
one real provider". This is the stub. It exists so registry/contract tests can run with zero
credentials and zero network, and so `make test` stays green on a clean checkout.

It must never be mistaken for a measurement:
  * `model_version` is literally `stub:not-a-model`, so it shows up in every report;
  * every confidence is 0.0 and every tracked field lands in `needs_review`;
  * it emits no brand and no material (no evidence exists for either).

BRIEF.md forbids mocked model calls in the acceptance path. So the CLI only uses this when
`SNAPLIST_ATTRS_PROVIDER=stub` is set explicitly; the default is the real HTTP adapter.
"""

from __future__ import annotations

from apps.api.schemas import ItemAttributes
from packages.vision.attrs_adapters.base import _TRACKED_FIELDS


class StubExtractor:
    """Deterministic, offline, and openly fake. Never used to report accuracy."""

    name = "stub"

    def extract(self, image: bytes) -> ItemAttributes:
        if not image:
            raise ValueError("stub extractor: image bytes are empty")
        return ItemAttributes(
            category="other",
            colors=["unknown"],
            confidence={field: 0.0 for field in _TRACKED_FIELDS},
            needs_review=sorted(_TRACKED_FIELDS),
            model_version="stub:not-a-model",
        )
