"""E4-T3 — the vton quality gate and the `vton_rejected` failure taxonomy. Owner: VTON (E4).

This module is dependency-free (stdlib only) so `packages/vision/vton.py` can import it at
module load without pulling in any adapter or provider SDK. It defines:

* `VtonRejectReason` — the four-value internal taxonomy that feeds `reason_code=vton_rejected`
  (CONTRACTS §4). The contract's `reason_code` is the closed five-value enum; `vton_rejected`
  is the one this gate produces, and these four reasons are the granular "why" the gate keeps.
* `gate(result) -> (pass, reason)` — the deterministic, offline decision. It reads the four
  quality signals carried by any `VtonResult` and refuses anything that is not a clean render.

Why confidence is the last and strictest check
---------------------------------------------
A real FASHN v1.5 render carries no confidence score, so `confidence` is `None` and the gate
rejects it on the floor exactly as a low score would. That is the honest UNMEASURED->reject
mapping: we never ship an uncertified render, and we never pretend a missing measurement is a
pass. Calibrating a real confidence signal (face-similarity / garment-colour-drift per
prompts/03-vton.md) on the golden set (E0) is what flips this — until then every real render
is rejected and `cost_usd` stays `None`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

# The contract `reason_code` this gate always produces (CONTRACTS §4).
REASON_CODE = "vton_rejected"

# The confidence floor below which a render is not shippable. Mirrors the attrs floor
# (attrs_adapters/base.py) as a design target; it is deliberately NOT a measured threshold —
# the golden set (E0) is what will validate or move it, and a guessed number would reject real
# photos we could never explain.
CONFIDENCE_FLOOR = 0.60


class VtonRejectReason(StrEnum):
    """The four reasons a render is refused. Every value maps to `reason_code=vton_rejected`."""

    LOW_CONFIDENCE = "low_confidence"
    GARMENT_NOT_DETECTED = "garment_not_detected"
    PERSON_NOT_DETECTED = "person_not_detected"
    ARTIFACTS = "artifacts"


@runtime_checkable
class VtonRender(Protocol):
    """The slice of `VtonResult` the gate reads. Kept structural so the gate imports nothing."""

    person_detected: bool
    garment_detected: bool
    has_artifacts: bool
    confidence: float | None


def gate(result: VtonRender) -> tuple[bool, VtonRejectReason | None]:
    """Decide whether `result` is shippable.

    Order matters: structural failures (no person, no garment) are checked before the softer
    signals (artifacts, confidence). Returns `(True, None)` only for a render where a person
    and a garment are both present, no artifacts are flagged, and confidence is measured and
    at or above the floor. `confidence is None` (UNMEASURED) fails the floor on purpose.
    """
    if not result.person_detected:
        return False, VtonRejectReason.PERSON_NOT_DETECTED
    if not result.garment_detected:
        return False, VtonRejectReason.GARMENT_NOT_DETECTED
    if result.has_artifacts:
        return False, VtonRejectReason.ARTIFACTS
    if result.confidence is None or result.confidence < CONFIDENCE_FLOOR:
        return False, VtonRejectReason.LOW_CONFIDENCE
    return True, None
