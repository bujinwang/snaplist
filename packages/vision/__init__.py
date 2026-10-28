"""SnapList vision pipeline — SCAFFOLD ONLY.

Owned by the CV role (E1/E2/E3) and, for `vton.py` only, the VTON role (E4).

Every module in this package is a documented stub that raises `StageNotImplemented`.
Nothing here calls a model, downloads weights, or imports torch. The pipeline order the
real implementation must respect is fixed by BRIEF.md:

    segment -> matte -> rectify -> prettify -> attrs
                                         \\-> vton  (separate job kind)

Hard rules that apply to every module (from prompts/02-vision.md):
  * never silently swap models — record `segment_backend` / `prettify_mode` in metadata
  * no CUDA/MPS -> degrade to cheap matting and say so
  * outputs are PNG, sRGB, <=1600px, neutral backdrop #F2F1EE
"""

from __future__ import annotations

__all__ = ["StageNotImplemented", "PIPELINE_STAGES"]

PIPELINE_STAGES: tuple[str, ...] = ("segment", "matte", "rectify", "prettify", "attrs")


class StageNotImplemented(NotImplementedError):
    """Raised by every stub in this package until the owning epic lands."""
