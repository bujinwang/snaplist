"""STAGE 1 — garment segmentation. Owner: CV role (E1, ticket E1-T2).

Not implemented. Contract to keep when implementing:

    segment(image: bytes) -> SegmentResult

    SegmentResult.mask_png   # 8-bit single channel, 255 = garment, soft edges allowed
    SegmentResult.meta       # StageMeta(backend=..., device=...)

Model order: SAM 2.1 hosted primary, BiRefNet then U²-Net as the cheap/no-GPU fallback.
Metric to hit: mean IoU >= 0.92 against hand masks on 20 of the 40 golden fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.vision import StageNotImplemented
from packages.vision.types import SegmentBackend, StageMeta


@dataclass(slots=True)
class SegmentResult:
    mask_png: bytes
    meta: StageMeta


def segment(image: bytes, backend: SegmentBackend = SegmentBackend.SAM2_HOSTED) -> SegmentResult:
    raise StageNotImplemented("packages.vision.segment.segment — see docs/PLAN.md ticket E1-T2")
