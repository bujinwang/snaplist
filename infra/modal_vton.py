"""Modal deployment for CatVTON (E4-T2, fallback / cheap tier). Owner: VTON role (E4).

This is the deployable Modal app. It is run with `modal deploy infra/modal_vton.py` INSIDE the
Modal image — the only place `modal` is installed. The api image does NOT have it
(docs/DEPS.md lists `modal` under "Not dependencies, on purpose"), and this module is never
imported by `packages/vision` at import time: `CatVtonAdapter` imports it lazily, inside
`render()`, and only when `MODAL_TOKEN` is set and a real render is being attempted.

The `APP_NAME` / `FUNCTION_NAME` constants are shared with `CatVtonAdapter` so the client and
the deployment agree on the function identity without a duplicated string.

Deploy:

    pip install modal
    modal token set --token-id $MODAL_TOKEN_ID --token-secret $MODAL_TOKEN_SECRET
    modal deploy infra/modal_vton.py

IMPORTANT: `_render` is a STUB of the CatVTON inference. It documents the exact deployment
shape — the image, the function, the request payload, and the return envelope — but does NOT
run CatVTON. The weights and the GPU inference graph are not wired up in this build. Filling
this in is a follow-up that needs the CatVTON repo + GPU quota, so a real CatVTON render is
BLOCKED the same way FASHN is (packages/vision/BLOCKED.md).
"""

from __future__ import annotations

import base64
import modal

APP_NAME = "snaplist-catvton"
FUNCTION_NAME = "render"

# TODO(E4-T2): pin versions and choose a GPU base image once the CatVTON repo + quota land.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("pillow", "torch", "catvton")  # placeholder deps; pin before deploy
)
app = modal.App(APP_NAME)


@app.function(image=image, gpu="A10G", timeout=300)
def render(payload: dict) -> dict:
    """CatVTON on-model render.

    Request payload (from `CatVtonAdapter.render`):
        {
            "person_image_b64":  str,   # base64 of the consented stock model photo
            "garment_image_b64": str,   # base64 of the garment cutout
            "category":           str,   # "auto" | "tops" | "bottoms" | "one-pieces"
            "model_ref":          str | None,
        }

    Return envelope (consumed by `CatVtonAdapter._unpack`):
        {
            "on_model_png":   bytes,        # the rendered image
            "on_model_png_b64": str,        # OR its base64 (either is accepted)
            "confidence":     float | None,  # CatVTON emits none -> None (UNMEASURED)
        }

    STUB: the body below documents the shape only. Replace with the real CatVTON pipeline
    (load weights, run the try-on, encode the result PNG) before this function is usable.
    """
    person_bytes = base64.b64decode(payload["person_image_b64"])
    garment_bytes = base64.b64decode(payload["garment_image_b64"])
    _ = (person_bytes, garment_bytes)  # inputs decoded; inference not implemented

    raise NotImplementedError(
        "infra/modal_vton.py::_render is a STUB. CatVTON inference is not wired up in this "
        "build (E4-T2). See packages/vision/BLOCKED.md for the blocking condition."
    )
