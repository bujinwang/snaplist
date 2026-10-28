"""STAGE 4 — attributes via VLM behind an adapter interface. Owner: CV role (E3, tickets A1..A3).

Not implemented. The only thing that is already frozen is the *interface*: one VLM behind
`AttributeExtractor`, output forced through `apps.api.schemas.ItemAttributes` JSON schema.
Swapping providers must be a config change, not a code change.

Adapter contract:

    class AttributeExtractor(Protocol):
        name: str
        async def extract(self, image: bytes) -> ItemAttributes: ...

Rules the implementation must respect (docs/CONTRACTS.md):
  * never invent `brand` — only when the mark is legible in the photo
  * never invent `material` — only with visual or care-label evidence
  * fill `confidence` per field and list sub-threshold fields in `needs_review`
  * a schema-violating VLM response is retried once, then fails the job as
    `low_confidence` (never `internal` — that hides a provider problem as our bug)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from apps.api.schemas import ItemAttributes
from packages.vision import StageNotImplemented


@runtime_checkable
class AttributeExtractor(Protocol):
    """One VLM behind this interface. Implementations live beside this file."""

    name: str

    def extract(self, image: bytes) -> ItemAttributes:  # pragma: no cover - protocol
        ...


def get_extractor(name: str | None = None) -> AttributeExtractor:
    """Resolve the configured extractor. Named so config, not imports, choose the model."""
    raise StageNotImplemented("packages.vision.attrs.get_extractor — see docs/PLAN.md ticket E3-T1")
