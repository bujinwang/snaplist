"""Shared stage I/O types for the vision pipeline.

Adding a field here is a contract change: `StageMeta` ends up in the eval reports and in
`ItemAttributes.model_version`, so write it down in docs/CONTRACTS.md first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SegmentBackend(StrEnum):
    SAM2_HOSTED = "sam2_hosted"
    BIREFNET = "birefnet"
    U2NET = "u2net"  # cheap fallback when there is no CUDA/MPS
    UNMEASURED = "unmeasured"


class PrettifyMode(StrEnum):
    PASS_THROUGH = "pass_through"  # bytes out == bytes in; no cleanup of any kind
    # Model-free pipeline (white balance, wrinkle mask, shadow attenuation). Added by E2-T2
    # round 1: it changes pixels, so it cannot be reported as `pass_through` — the report would
    # say "no cleanup applied" about a cleaned image. Still not the SDXL path.
    CLASSICAL = "classical"
    SDXL_INPAINT = "sdxl_inpaint"
    UNMEASURED = "unmeasured"


@dataclass(slots=True)
class StageMeta:
    """Everything the cost report and the honesty rules need to know about a single run."""

    stage: str
    backend: str = SegmentBackend.UNMEASURED
    device: str = "unmeasured"  # "cuda" | "mps" | "cpu"
    seconds: float | None = None  # None => UNMEASURED, never 0.0 as a placeholder
    cost_usd: float | None = None
    notes: dict[str, Any] = field(default_factory=dict)
