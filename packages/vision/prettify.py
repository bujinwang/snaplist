"""STAGE 3 — catalog-grade prettify. Owner: CV role (E2, ticket E2-T2).

Not implemented. Optional SDXL inpaint restricted to wrinkle/crease masks, never over
logos or text; white balance; even lighting; export PNG <=1600px sRGB.

Hard constraints:
  * zero hallucinated text/logo regions (manual rubric 1-5, target >= 4)
  * garment-mask SSIM >= 0.80 after prettify
  * when the generative step is off, `PrettifyMode.PASS_THROUGH` and say so in meta —
    never present a pass-through as a "cleaned" image.

    prettify(flat_png: bytes, alpha_png: bytes, *, mode: PrettifyMode) -> PrettifyResult
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.vision import StageNotImplemented
from packages.vision.types import PrettifyMode, StageMeta


@dataclass(slots=True)
class PrettifyResult:
    prettified_png: bytes
    meta: StageMeta


def prettify(
    flat_png: bytes,
    alpha_png: bytes,
    *,
    mode: PrettifyMode = PrettifyMode.PASS_THROUGH,
) -> PrettifyResult:
    """Catalog-grade prettify.

    Only `PASS_THROUGH` is implemented: the generative SDXL inpaint (wrinkle/crease masks
    only, never over logos or text) is ticket E2-T2 and still raises `StageNotImplemented`.
    Per BRIEF.md honesty rules, pass-through returns the rectified flat-lay untouched and
    says so in `meta.notes["mode"]` — it is never presented as a "cleaned" image.
    """
    if mode == PrettifyMode.PASS_THROUGH:
        return PrettifyResult(
            prettified_png=flat_png,
            meta=StageMeta(
                stage="prettify",
                backend="pass_through",
                device="n/a",
                notes={"mode": "pass_through", "generative_step": "not_implemented"},
            ),
        )
    raise StageNotImplemented(
        "packages.vision.prettify.prettify — SDXL_INPAINT not implemented (docs/PLAN.md E2-T2)"
    )
