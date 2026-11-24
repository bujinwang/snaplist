"""Parallel Google Nano Banana VTON adapter. NOT the primary provider.

This adapter implements the Gemini interactions REST API for image editing, using
`gemini-nano-banana-2.1` to composite the garment image onto the model image. It is registered
as `google_banana` and is only reachable by an explicit `get_adapter("google_banana")` call.
`ADR-001 §8` still pins FASHN v1.5 as primary and CatVTON on Modal as fallback: automatic
selection (`get_adapter()` with no name) is unchanged, and a `GEMINI_API_KEY` never silently
changes which provider runs a seller's job.

Why it is here
--------------
It exists so the provider can be evaluated side-by-side with FASHN on the 12 golden garments.
A list-price advantage at 1K/2K is not a quality result; until the golden-set measurement and a
superseding ADR exist, this adapter is a second candidate, not a replacement.

Honesty rules inherited from the repo
-------------------------------------
* No new dependency: stdlib `urllib` only (docs/DEPS.md stays clean).
* `confidence=None` (UNMEASURED), because Nano Banana emits no per-render quality score. The
  quality gate therefore rejects the result exactly as it rejects an unmeasured FASHN render.
* `cost_per_render_usd=None` and `StageMeta.cost_usd=None`: token usage is recorded in `notes`,
  but no dollar figure is invented from list prices. `$/render` must be measured on the golden
  set (E4-T1 DoD) before it is written.
* Detection flags mirror the existing FASHN adapter's provider-success convention. They are not
  a claim that face similarity / garment colour drift were measured; `confidence_measured=False`
  in `notes` says so explicitly.

REST shape (Gemini interactions API, revision 2026-05-20)
---------------------------------------------------------
    POST https://generativelanguage.googleapis.com/v1beta/interactions
      x-goog-api-key: <GEMINI_API_KEY>
      Content-Type: application/json
      Api-Revision: 2026-05-20
      body: { "model": "gemini-nano-banana-2.1",
              "input": [ { "type": "image", "data": <b64>, "mime_type": "image/png" },
                         { "type": "image", "data": <b64>, "mime_type": "image/png" },
                         { "type": "text", "text": <prompt> } ],
              "response_format": { "type": "image", "image_size": "1K" } }
      -> { "id": ..., "status": "completed",
           "steps": [ { "type": "model_output",
                        "content": [ { "type": "image", "data": <b64>,
                                       "mime_type": "image/png" } ] } ],
           "usage": { ... } }
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

from packages.vision import StageNotImplemented
from packages.vision.types import StageMeta
from packages.vision.vton import VtonProviderError, VtonResult

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
MODEL = "gemini-nano-banana-2.1"
API_REVISION = "2026-05-20"
# 1K is the cost-sensitive default. 2K/4K change the price and must be re-measured before use.
IMAGE_SIZE = "1K"
REQUEST_TIMEOUT = 60.0

VTON_PROMPT = (
    "Put the garment shown in the second image on the person in the first image. "
    "Keep the person's face, pose, body proportions and background unchanged. "
    "Do not change the garment's colour, logo, pattern, texture or seams. "
    "Return only the edited full-body image."
)

_ALLOWED_MIME = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",
}


def _image_mime(image: bytes) -> str:
    """Sniff the input mime from magic bytes — never trust a caller-supplied extension."""
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("google_banana vton: unsupported image type (need png/jpeg/webp)")


def _image_part(image: bytes) -> dict[str, str]:
    return {
        "type": "image",
        "data": base64.b64encode(image).decode("ascii"),
        "mime_type": _image_mime(image),
    }


def _safe_read(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8", errors="replace")[:500]
    except Exception:  # noqa: BLE001 - reading the error body is best-effort
        return "<unreadable error body>"


def _post_json(url: str, headers: dict[str, str], payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_image(body: dict) -> tuple[bytes, str]:
    """Walk the documented `steps[].content[]` envelope and return (bytes, mime_type)."""
    for step in body.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for item in step.get("content") or []:
            if not isinstance(item, dict) or item.get("type") != "image":
                continue
            encoded = item.get("data")
            if not isinstance(encoded, str) or not encoded:
                continue
            mime = str(item.get("mime_type") or "")
            raw = base64.b64decode(encoded, validate=True)
            magic = _ALLOWED_MIME.get(mime)
            if magic and not raw.startswith(magic):
                raise VtonProviderError(
                    "google_banana",
                    f"response mime {mime!r} does not match the image bytes",
                    code="bad_mime",
                )
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise VtonProviderError(
                    "google_banana",
                    "Nano Banana returned a non-PNG image; VtonResult.on_model_png requires PNG",
                    code="bad_mime",
                )
            return raw, mime
    raise VtonProviderError(
        "google_banana", "completed interaction had no image output", code="no_image"
    )


def _extract_usage(body: dict) -> dict[str, object]:
    """Keep the raw token accounting for the cost harness. Never turn it into a guessed $."""
    usage = body.get("usage")
    return dict(usage) if isinstance(usage, dict) else {}


class GoogleBananaVtonAdapter:
    """Google Nano Banana 2.1 as a parallel evaluation provider. Never auto-selected."""

    name = "google_banana"
    # UNMEASURED until a golden-set run produces a real $/render (E4-T1 DoD).
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
                "google_banana vton: person_image and garment_image bytes are both required"
            )
        key = os.environ.get(GEMINI_API_KEY_ENV)
        if not key:
            raise StageNotImplemented(
                f"{GEMINI_API_KEY_ENV} is not set; Google Nano Banana renders are BLOCKED. "
                "See packages/vision/BLOCKED.md"
            )

        headers = {
            "x-goog-api-key": key,
            "Content-Type": "application/json",
            "Api-Revision": API_REVISION,
        }
        prompt = f"{VTON_PROMPT} Garment category: {category or 'auto'}."
        payload = {
            "model": MODEL,
            "input": [_image_part(person_image), _image_part(garment_image),
                      {"type": "text", "text": prompt}],
            "response_format": {"type": "image", "image_size": IMAGE_SIZE},
        }

        started = time.perf_counter()
        try:
            body = _post_json(BASE_URL, headers, payload, REQUEST_TIMEOUT)
        except urllib.error.HTTPError as exc:
            raise VtonProviderError(
                "google_banana",
                f"interaction HTTP {exc.code}: {_safe_read(exc)}",
                code=str(exc.code),
            ) from exc
        except urllib.error.URLError as exc:
            raise VtonProviderError(
                "google_banana", f"interaction network error: {exc.reason}"
            ) from exc
        except ValueError as exc:
            raise VtonProviderError(
                "google_banana", f"unparsable interaction response: {exc}", code="bad_response"
            ) from exc
        seconds = time.perf_counter() - started

        status = body.get("status")
        if status not in (None, "completed"):
            raise VtonProviderError(
                "google_banana", f"interaction status {status!r}", code=str(status)
            )

        on_model_png, response_mime = _extract_image(body)
        usage = _extract_usage(body)
        meta = StageMeta(
            stage="vton",
            backend="google_banana",
            device="api",
            seconds=seconds,  # measured wall-clock; None would mean UNMEASURED
            cost_usd=None,  # list price is not a measurement -> UNMEASURED
            notes={
                "provider": "google_banana",
                "model": MODEL,
                "image_size": IMAGE_SIZE,
                "api_revision": API_REVISION,
                "interaction_id": body.get("id"),
                "response_mime": response_mime,
                "model_ref": model_ref or "unset",
                "category": category or "auto",
                "network_calls": 1,
                "usage": usage,  # token counts for a later measured-cost report
                "confidence_measured": False,
            },
        )
        return VtonResult(
            provider="google_banana",
            on_model_png=on_model_png,
            confidence=None,  # Nano Banana emits no score -> gate rejects on the floor
            garment_detected=True,
            person_detected=True,
            has_artifacts=False,
            meta=meta,
        )
