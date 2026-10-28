"""Virtual try-on — the on-model render. Owner: VTON role (E4). ONE FILE, ONE OWNER.

Not implemented. Two adapters, one interface, and the choice is config, never an import:

  * `FashnVtonAdapter`   — FASHN VTON v1.5 API, primary, commercial licence
  * `CatVtonAdapter`     — CatVTON on Modal, fallback / cheap tier

Contract:

    class VtonAdapter(Protocol):
        name: str
        cost_per_render_usd: float        # must be measured, not estimated
        def render(self, garment_png: bytes, model_ref: str, **opts) -> VtonResult: ...

Failure taxonomy is fixed by docs/CONTRACTS.md: a provider that rejects the garment or the
model reference fails the job with `reason_code=vton_rejected`, with the provider's own
code in `error_detail`. Retrying a rejected render is pointless and burns quota.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from packages.vision import StageNotImplemented
from packages.vision.types import StageMeta


@dataclass(slots=True)
class VtonResult:
    on_model_png: bytes
    meta: StageMeta


@runtime_checkable
class VtonAdapter(Protocol):
    name: str
    cost_per_render_usd: float

    def render(self, garment_png: bytes, model_ref: str) -> VtonResult:  # pragma: no cover
        ...


def get_adapter(name: str | None = None) -> VtonAdapter:
    raise StageNotImplemented("packages.vision.vton.get_adapter — see docs/PLAN.md ticket E4-T1")
