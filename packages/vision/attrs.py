"""STAGE 4 — attributes via VLM behind an adapter interface. Owner: CV role (E3, tickets A1..A3).

E3-T1 lands here: the registry. E3-T2's wire machinery lives in `attrs_adapters/`.

The VLM provider is NOT chosen in this file, on purpose
--------------------------------------------------------
docs/RUNBOOK.md §6 (and HANDOFF-product.md's "待 Bert 拍") reserve the provider decision
for Bert. BRIEF.md's fixed decision is only "one VLM behind an adapter interface"; no vendor,
no price and no accuracy figure may be invented here. So the adapter is written against the
OpenAI-compatible chat-completions + vision *protocol* and takes its base URL, model and key
from environment config. Picking a vendor is then a config change, not a code change — which
is exactly the E3-T1 DoD.

Adapter contract (frozen by test_public_signatures_are_stable):

    class AttributeExtractor(Protocol):
        name: str
        def extract(self, image: bytes) -> ItemAttributes: ...

Rules enforced by the machinery in attrs_adapters/base.py (docs/CONTRACTS.md §4):
  * never invent `brand` — only when the mark is legible in the photo
  * never invent `material` — only with visual or care-label evidence
  * fill `confidence` per field and list sub-threshold fields in `needs_review`
  * a schema-violating VLM response is retried exactly once, then fails the job as
    `low_confidence` (never `internal` — that would hide a provider problem as our bug)

Import policy: this module imports NO provider SDK. `get_extractor()` resolves an adapter by
name through importlib, and the adapter imports its own HTTP client lazily at call time.
"""

from __future__ import annotations

import importlib
import os
from typing import Protocol, runtime_checkable

from apps.api.schemas import ItemAttributes

ATTRS_PROVIDER_ENV = "SNAPLIST_ATTRS_PROVIDER"

# "stub" is the tests/CI fixture named by PLAN.md E3-T1. "http" is the real, protocol-level
# adapter — its vendor is chosen by config, not here. No vendor name appears in this file.
_REGISTRY: dict[str, str] = {
    "stub": "packages.vision.attrs_adapters.stub:StubExtractor",
    "http": "packages.vision.attrs_adapters.http:HttpVlmExtractor",
}
_DEFAULT_PROVIDER = "http"


class AttributesUnavailable(RuntimeError):
    """No VLM is configured, so attributes cannot be produced. Not a model failure."""

    reason_code = "low_confidence"


class AttributeExtractionFailed(RuntimeError):
    """VLM output failed schema after one repair retry, or all fields fell below the floor.

    CONTRACTS §4 assigns `low_confidence` to this case. The worker must map this to
    reason_code=low_confidence, NOT internal — a provider incident must not look like our bug.
    """

    reason_code = "low_confidence"

    def __init__(self, message: str, *, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


@runtime_checkable
class AttributeExtractor(Protocol):
    """One VLM behind this interface. Implementations live in attrs_adapters/."""

    name: str

    def extract(self, image: bytes) -> ItemAttributes:  # pragma: no cover - protocol
        ...


def available_extractors() -> tuple[str, ...]:
    """Names that can be resolved, for error messages and /readyz. No imports happen here."""
    return tuple(sorted(_REGISTRY))


def get_extractor(name: str | None = None) -> AttributeExtractor:
    """Resolve the configured extractor. Named so config, not imports, choose the model.

    Resolution order: explicit `name` > `SNAPLIST_ATTRS_PROVIDER` > default ("http").
    Import is lazy: a bad/missing provider SDK can never break `import packages.vision.attrs`.
    Credentials are checked at `extract()` time, never here — `make test` runs with zero creds.
    """
    chosen = (name or os.environ.get(ATTRS_PROVIDER_ENV) or _DEFAULT_PROVIDER).strip().lower()
    if chosen not in _REGISTRY:
        options = ", ".join(available_extractors())
        raise ValueError(f"unknown attributes provider {chosen!r}; available: {options}")
    module_path, _, class_name = _REGISTRY[chosen].partition(":")
    module = importlib.import_module(module_path)
    instance: AttributeExtractor = getattr(module, class_name)()
    if not isinstance(instance, AttributeExtractor):
        raise TypeError(f"{_REGISTRY[chosen]} does not satisfy AttributeExtractor")
    return instance
