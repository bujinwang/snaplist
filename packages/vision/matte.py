"""STAGE 1b — alpha matting around the segmentation mask. Owner: CV role (E1).

Not implemented. `matte()` refines a hard mask into a soft alpha channel so hair, straps
and knit edges survive compositing onto the neutral backdrop. Cheap, deterministic, no
network call: this is the stage that must never be a generative model.

    matte(image: bytes, mask_png: bytes) -> MatteResult   # alpha_png + meta
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.vision import StageNotImplemented
from packages.vision.types import StageMeta


@dataclass(slots=True)
class MatteResult:
    alpha_png: bytes
    meta: StageMeta


def matte(image: bytes, mask_png: bytes) -> MatteResult:
    raise StageNotImplemented("packages.vision.matte.matte — see docs/PLAN.md ticket E1-T3")
