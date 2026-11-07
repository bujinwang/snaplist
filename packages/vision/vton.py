"""Virtual try-on — the on-model render. Owner: VTON role (E4). ONE FILE, ONE OWNER.

Epic E4 turns a garment cutout into an on-model ("try-on") preview so a seller can show the
item worn. This module is the ONLY file in `packages/vision` the vton role owns; the adapters
it selects live in `vton_adapters/` and the quality gate lives in `vton_adapters/gate.py`.

What this file owns
------------------
* `VtonResult` / `VtonOutcome` — the render result and the pipeline-facing outcome.
* `VtonAdapter` — the provider interface every adapter satisfies.
* `get_adapter(name)` — resolves a provider by name or, with `name=None`, by environment:
  `FASHN_API_KEY` set -> fashn; `MODAL_TOKEN` set -> catvton; neither -> stub.
* `try_on(...)` — the orchestration the API job pipeline (E5-T3 worker) calls: pick an
  adapter, render, run the quality gate, and return a `VtonOutcome`.

Hard rules inherited from the brief and BRIEF.md
------------------------------------------------
1. Never fake a render. With no credentials `get_adapter()` returns `StubVtonAdapter`, whose
   `render()` returns `provider="stub"`, an EMPTY `on_model_png`, and a result the quality
   gate rejects. We never synthesise a garment-on-model image and call it a render.
2. Selection is by env, never by silent fallback. Asking for a named provider whose credential
   is missing raises `StageNotImplemented` naming the missing key — the result must always
   state which provider actually produced it.
3. A provider that rejects the garment/model pair must map to `reason_code=vton_rejected`
   (CONTRACTS §4), with the provider's own error code preserved in `error_detail`. Retrying a
   rejected render burns quota for nothing.
4. The quality gate currently cannot certify a real render: FASHN v1.5 returns no confidence
   score, so `confidence` is `None` (UNMEASURED) and the gate rejects on the confidence floor.
   Shipping requires the E4 quality-gate calibration on the golden set (E0) — that is a
   measurement gap, not a code gap, and it is documented in `packages/vision/BLOCKED.md`.

How the worker wires this (E5-T3, not in this file)
---------------------------------------------------
The worker handler calls `try_on(garment_image, person_image, model_ref=...)`. A returned
`VtonOutcome.reason_code == "vton_rejected"` is the terminal failure with the quota refunded;
`passed_gate is True` is the row that may move to `done`. A `VtonProviderError` raised from
`render()` means the provider refused — map it to `vton_rejected`, not `internal`.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from packages.vision import StageNotImplemented
from packages.vision.types import StageMeta
from packages.vision.vton_adapters.gate import REASON_CODE, VtonRejectReason, gate

FASHN_API_KEY_ENV = "FASHN_API_KEY"
MODAL_TOKEN_ENV = "MODAL_TOKEN"

# name -> "module:Class" inside `packages/vision/vton_adapters`. Resolution is lazy: importing
# this module pulls in no provider SDK, so `make test` and `import packages.vision.vton` work
# on a clean checkout with zero credentials and zero network.
_REGISTRY: dict[str, str] = {
    "stub": "packages.vision.vton_adapters.stub:StubVtonAdapter",
    "fashn": "packages.vision.vton_adapters.fashn:FashnVtonAdapter",
    "catvton": "packages.vision.vton_adapters.catvton:CatVtonAdapter",
}
# A named provider that needs a credential we do not have must refuse loudly instead of
# silently downgrading to the stub.
_PROVIDER_REQUIRES_KEY: dict[str, str] = {
    "fashn": FASHN_API_KEY_ENV,
    "catvton": MODAL_TOKEN_ENV,
}


class VtonProviderError(RuntimeError):
    """A provider returned a failed status or an unparsable response. Not our bug.

    This is what lets the worker map a provider refusal to `reason_code=vton_rejected`
    (CONTRACTS §4) rather than `internal`: the provider's own code is preserved so a
    third-party incident is never laundered into a defect we pretend is ours.
    """

    def __init__(self, provider: str, message: str, *, code: str | None = None) -> None:
        super().__init__(f"{provider} vton failed: {message}")
        self.provider = provider
        self.message = message
        self.code = code


@dataclass(slots=True)
class VtonResult:
    """One render attempt and the signals the quality gate needs to judge it.

    `provider` is always the string name of the adapter that produced it — the contract
    requires the result to state which provider ran. `confidence` is `None` when the producer
    does not emit a measured score (UNMEASURED), never a guessed middle value.
    """

    provider: str
    on_model_png: bytes
    confidence: float | None
    garment_detected: bool
    person_detected: bool
    has_artifacts: bool
    meta: StageMeta


@dataclass(slots=True)
class VtonOutcome:
    """What `try_on()` hands back to the job pipeline.

    `reason_code` is the contract value (`vton_rejected` on rejection, else `None`).
    `reject_reason` is the internal four-value taxonomy from the gate; `error_detail` is the
    human/operator string, which preserves the provider's own error code when one exists.
    """

    result: VtonResult
    passed_gate: bool
    reason_code: str | None
    reject_reason: VtonRejectReason | None
    error_detail: str | None


@runtime_checkable
class VtonAdapter(Protocol):
    """One provider behind the try-on interface. Implementations live in `vton_adapters/`."""

    name: str
    # Measured, not estimated. `None` means UNMEASURED (no key, no golden-set run yet).
    cost_per_render_usd: float | None

    def render(
        self,
        person_image: bytes,
        garment_image: bytes,
        *,
        model_ref: str | None = None,
        category: str | None = None,
    ) -> VtonResult:  # pragma: no cover - protocol
        ...


def available_adapters() -> tuple[str, ...]:
    """Names that can be resolved, for error messages and readiness probes. No import happens."""
    return tuple(sorted(_REGISTRY))


def get_adapter(name: str | None = None) -> VtonAdapter:
    """Resolve the vton adapter. Named so config, not imports, chooses the provider.

    `name=None` selects by environment (FASHN_API_KEY -> fashn, MODAL_TOKEN -> catvton,
    else stub). A named provider whose credential is absent raises `StageNotImplemented`
    naming the missing key — there is deliberately no silent downgrade to the stub.
    """
    if name is None:
        if os.environ.get(FASHN_API_KEY_ENV):
            name = "fashn"
        elif os.environ.get(MODAL_TOKEN_ENV):
            name = "catvton"
        else:
            name = "stub"
    chosen = name.strip().lower()
    if chosen not in _REGISTRY:
        options = ", ".join(available_adapters())
        raise ValueError(f"unknown vton provider {chosen!r}; available: {options}")

    required = _PROVIDER_REQUIRES_KEY.get(chosen)
    if required and not os.environ.get(required):
        raise StageNotImplemented(
            f"vton provider {chosen!r} needs {required} (real render is BLOCKED); "
            f"set it, or omit the name to get the stub. See packages/vision/BLOCKED.md"
        )

    module_path, _, class_name = _REGISTRY[chosen].partition(":")
    module = importlib.import_module(module_path)
    instance: VtonAdapter = getattr(module, class_name)()
    if not isinstance(instance, VtonAdapter):
        raise TypeError(f"{_REGISTRY[chosen]} does not satisfy VtonAdapter")
    return instance


def try_on(
    person_image: bytes,
    garment_image: bytes,
    *,
    model_ref: str | None = None,
    category: str | None = None,
    adapter_name: str | None = None,
) -> VtonOutcome:
    """Render `garment_image` onto `person_image` and run the quality gate.

    This is the seam the E5-T3 job worker calls for `kind=VTON`. A returned
    `reason_code == "vton_rejected"` is a terminal failure with the quota refunded
    (CONTRACTS §4). A `VtonProviderError` from `render()` propagates for the worker to map
    to `vton_rejected` with the provider's own code preserved in `error_detail`.

    In a credential-less build this returns the stub outcome: `provider="stub"`,
    `passed_gate=False`, `reason_code="vton_rejected"` — an honest "cannot render here",
    never a fake image.
    """
    adapter = get_adapter(adapter_name)
    result = adapter.render(
        person_image, garment_image, model_ref=model_ref, category=category
    )
    passed, reason = gate(result)
    if passed:
        return VtonOutcome(
            result=result,
            passed_gate=True,
            reason_code=None,
            reject_reason=None,
            error_detail=None,
        )

    if adapter.name == "stub":
        detail = (
            "E4 real render BLOCKED: no FASHN_API_KEY/MODAL_TOKEN; the stub adapter returns "
            "no image. See packages/vision/BLOCKED.md"
        )
    else:
        detail = (
            f"vton quality gate rejected ({reason.value if reason else 'unknown'}); "
            f"provider={adapter.name}; confidence={result.confidence}"
        )
    return VtonOutcome(
        result=result,
        passed_gate=False,
        reason_code=REASON_CODE,
        reject_reason=reason,
        error_detail=detail,
    )
