"""E4-T2 — CatVTON on Modal, the fallback / cheap tier. Owner: VTON role (E4).

CatVTON runs as a Modal function (`infra/modal_vton.py`, deployed into the Modal image — NOT
the api image; docs/DEPS.md lists `modal` under "Not dependencies, on purpose"). This adapter
is the client side: it looks the deployed function up by name and calls `.remote(payload)`.

The Modal function call is STUBBED here on purpose. The deployment shape — the Modal app
name, the function name, the request payload, and the return envelope — is documented in
`infra/modal_vton.py` and frozen by the `APP_NAME` / `FUNCTION_NAME` constants shared between
the two files. The actual CatVTON inference graph and weights are not wired up in this build,
so a real CatVTON render is BLOCKED the same way FASHN is (packages/vision/BLOCKED.md).

IMPORTANT — no import at module load. `modal` and `infra.modal_vton` are imported lazily
inside `render()` and only when a real render is actually attempted (i.e. `MODAL_TOKEN` is
set). A credential-less build never imports them, so `import packages.vision.vton` and
`make test` stay clean.
"""

from __future__ import annotations

import base64
import os
import time

from packages.vision import StageNotImplemented
from packages.vision.types import StageMeta
from packages.vision.vton import VtonProviderError, VtonResult

MODAL_TOKEN_ENV = "MODAL_TOKEN"

# The deployable Modal app lives here; `APP_NAME` / `FUNCTION_NAME` are shared with the caller
# so the two agree on the function identity without a duplicated string. Imported lazily.
_DEPLOY_MODULE = "infra.modal_vton"


def _unpack(result: object) -> tuple[bytes, float | None]:
    """Resolve the Modal function's return envelope to (png_bytes, confidence).

    CatVTON itself has no confidence output, so confidence is `None` (UNMEASURED) and the
    quality gate will reject the render on the floor until that signal is measured (E0).
    """
    if isinstance(result, dict):
        raw = result.get("on_model_png") or result.get("image")
        if isinstance(raw, bytes):
            encoded = result.get("on_model_png_b64") or result.get("image_b64")
            png = base64.b64decode(encoded) if encoded else raw
        elif isinstance(raw, str):
            png = base64.b64decode(raw)
        else:
            raise VtonProviderError(
                "catvton", f"unrecognised on_model_png in result: {result!r}", code="bad_result"
            )
        confidence = result.get("confidence")
        return png, (float(confidence) if isinstance(confidence, int | float) else None)
    raise VtonProviderError(
        "catvton", f"unrecognised result envelope: {result!r}", code="bad_result"
    )


class CatVtonAdapter:
    """Client for CatVTON deployed as a Modal function. The fallback / cheap tier."""

    name = "catvton"
    # Measured once the Modal deployment + golden set exist (E4-T1 DoD). `None` == UNMEASURED.
    cost_per_render_usd: float | None = None

    def render(
        self,
        person_image: bytes,
        garment_image: bytes,
        *,
        model_ref: str | None = None,
        category: str | None = None,
    ) -> VtonResult:
        if not person_image or not garment_image:
            raise ValueError(
                "catvton: person_image and garment_image bytes are both required"
            )
        if not os.environ.get(MODAL_TOKEN_ENV):
            raise StageNotImplemented(
                "MODAL_TOKEN is not set; CatVTON on Modal is BLOCKED. "
                "See packages/vision/BLOCKED.md"
            )

        try:
            import modal

            deploy = __import__(_DEPLOY_MODULE, fromlist=["APP_NAME", "FUNCTION_NAME"])
        except ImportError as exc:
            raise StageNotImplemented(
                "catvton needs the `modal` SDK, which is installed only in the Modal image "
                f"(not the api image): {exc}. See docs/DEPS.md and infra/modal_vton.py"
            ) from exc

        app_name = deploy.APP_NAME
        function_name = deploy.FUNCTION_NAME
        try:
            function = modal.Function.lookup(app_name, function_name)
        except Exception as exc:  # noqa: BLE001 - modal raises a zoo of lookup errors
            raise VtonProviderError(
                "catvton", f"function lookup failed: {exc}", code="lookup_failed"
            ) from exc

        payload = {
            "person_image_b64": base64.b64encode(person_image).decode("ascii"),
            "garment_image_b64": base64.b64encode(garment_image).decode("ascii"),
            "category": category or "auto",
            "model_ref": model_ref or None,
        }

        started = time.perf_counter()
        try:
            # STUB of the CatVTON inference: the function is not wired up in this build, so this
            # call will fail at lookup/deploy until infra/modal_vton.py is completed (E4-T2).
            result = function.remote(payload)
        except Exception as exc:  # noqa: BLE001 - the Modal remote surface is broad
            raise VtonProviderError(
                "catvton", f"remote call failed: {exc}", code="remote_failed"
            ) from exc

        on_model_png, confidence = _unpack(result)
        seconds = time.perf_counter() - started

        meta = StageMeta(
            stage="vton",
            backend="catvton",
            device="modal-gpu",
            seconds=seconds,
            cost_usd=None,  # UNMEASURED until the deployment + golden set produce a number
            notes={
                "provider": "catvton",
                "category": category or "auto",
                "model_ref": model_ref or "unset",
                "network_calls": 1,
                "confidence_measured": False,
            },
        )
        return VtonResult(
            provider="catvton",
            on_model_png=on_model_png,
            confidence=confidence,  # None: CatVTON emits no score; the gate rejects on this
            garment_detected=True,
            person_detected=True,
            has_artifacts=False,
            meta=meta,
        )
