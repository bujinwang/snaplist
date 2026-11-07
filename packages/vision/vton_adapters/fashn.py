"""E4-T1 — FASHN VTON v1.5 adapter. Owner: VTON role (E4). The primary, commercial-licence path.

Implements against the documented FASHN v1.5 request/response shape:

    POST https://api.fashn.ai/v1/run
      Authorization: Bearer <FASHN_API_KEY>
      body: { "model_image": data:<mime>;base64,…, "garment_image": …, "category":
              "auto"|"tops"|"bottoms"|"one-pieces", "mode": "performance", "num_samples": 1,
              "return_base64": true, "output_format": "png" }
      -> { "id": "<run_id>", "status": "queued" }

    GET https://api.fashn.ai/v1/status/<run_id>
      -> { "status": "completed"|"processing"|"queued"|"failed", "output": ["data:…"|url, …],
           "error": <string|null>, "cost": {"usd": <float>} }   # cost shape varies by mirror

The implementation is defensive about the exact field names (it accepts `output` or
`outputs`, a data-URI or a URL, an optional `cost.usd`) because the v1.5 response envelope is
not fully pinned in the public docs this build could reach. Anything it cannot parse is turned
into a `VtonProviderError` — never a silent partial success.

IMPORTANT — no client SDK and no new dependency. This module talks to FASHN over the standard
library `urllib` so the default `uv sync` stays clean (docs/DEPS.md: no `fashn-api` client is
added). `FASHN_API_KEY` is read from the environment at `render()` time, never at import.

Confidence is `None` (UNMEASURED): FASHN v1.5 returns no per-render quality score, so the
quality gate (vton_adapters/gate.py) rejects the result on the confidence floor until a real
confidence signal is measured on the golden set (E0). `cost_per_render_usd` is likewise `None`
until the `$/render` is computed from observed latency × the published rate (E4-T1 DoD).
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

FASHN_API_KEY_ENV = "FASHN_API_KEY"

FASHN_BASE_URL = "https://api.fashn.ai"
RUN_PATH = "/v1/run"
STATUS_PATH = "/v1/status"
DEFAULT_MODE = "performance"
REQUEST_TIMEOUT = 30.0
POLL_INTERVAL = 2.0
MAX_POLLS = 90  # ~3 minutes of polling before we give up and report a timeout

# SnapList category (from attrs) -> FASHN garment category. Anything not mapped is "auto".
_FASHN_CATEGORY: dict[str, str] = {
    "top": "tops",
    "bottom": "bottoms",
    "dress": "one-pieces",
    "outerwear": "tops",
    "shoe": "auto",
    "accessory": "auto",
    "other": "auto",
}


def _image_mime(image: bytes) -> str:
    """Sniff the image mime from its magic bytes — never trust a caller-supplied extension."""
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    if image[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    raise ValueError("fashn vton: unsupported image type (need png/jpeg/webp/gif)")


def _data_url(image: bytes) -> str:
    return f"data:{_image_mime(image)};base64,{base64.b64encode(image).decode('ascii')}"


def _map_category(category: str | None) -> str:
    if not category:
        return "auto"
    return _FASHN_CATEGORY.get(category.lower(), "auto")


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


def _get_json(url: str, headers: dict[str, str], timeout: float) -> dict:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _decode_output(item: object) -> bytes:
    """FASHN returns each output as a base64 data-URI or a URL. Resolve either to raw bytes."""
    if isinstance(item, str):
        if item.startswith("data:"):
            _, _, encoded = item.partition(",")
            return base64.b64decode(encoded)
        request = urllib.request.Request(item, headers={}, method="GET")
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read()
    if isinstance(item, dict):
        encoded = item.get("b64") or item.get("base64") or item.get("image_base64")
        if encoded:
            return base64.b64decode(encoded)
        url = item.get("url")
        if url:
            return _decode_output(url)
    raise VtonProviderError("fashn", f"unrecognised output item: {item!r}", code="bad_output")


def _extract_cost(status: dict) -> float | None:
    cost = status.get("cost")
    if isinstance(cost, dict):
        value = cost.get("usd")
        return float(value) if isinstance(value, int | float) else None
    if isinstance(cost, int | float):
        return float(cost)
    return None


class FashnVtonAdapter:
    """Wraps the FASHN VTON v1.5 hosted API. The commercial-licence primary path."""

    name = "fashn"
    # Measured from observed latency × published rate once a key + golden set exist (E4-T1 DoD).
    # `None` means UNMEASURED, never a guessed 0.0.
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
                "fashn vton: person_image and garment_image bytes are both required"
            )
        key = os.environ.get(FASHN_API_KEY_ENV)
        if not key:
            raise StageNotImplemented(
                "FASHN_API_KEY is not set; real FASHN renders are BLOCKED. "
                "See packages/vision/BLOCKED.md"
            )

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model_image": _data_url(person_image),
            "garment_image": _data_url(garment_image),
            "category": _map_category(category),
            "mode": DEFAULT_MODE,
            "num_samples": 1,
            "return_base64": True,
            "output_format": "png",
        }

        try:
            run = _post_json(f"{FASHN_BASE_URL}{RUN_PATH}", headers, payload, REQUEST_TIMEOUT)
        except urllib.error.HTTPError as exc:
            raise VtonProviderError(
                "fashn", f"run request HTTP {exc.code}: {_safe_read(exc)}", code=str(exc.code)
            ) from exc
        except urllib.error.URLError as exc:
            raise VtonProviderError(
                "fashn", f"run request network error: {exc.reason}"
            ) from exc

        run_id = run.get("id")
        if not run_id:
            raise VtonProviderError(
                "fashn", f"run response had no id: {run!r}", code="no_id"
            )

        started = time.perf_counter()
        status: dict | None = None
        polls = 0
        while polls < MAX_POLLS:
            polls += 1
            try:
                status = _get_json(
                    f"{FASHN_BASE_URL}{STATUS_PATH}/{run_id}", headers, REQUEST_TIMEOUT
                )
            except urllib.error.HTTPError as exc:
                raise VtonProviderError(
                    "fashn",
                    f"status request HTTP {exc.code}: {_safe_read(exc)}",
                    code=str(exc.code),
                ) from exc
            except urllib.error.URLError as exc:
                raise VtonProviderError(
                    "fashn", f"status request network error: {exc.reason}"
                ) from exc

            state = status.get("status")
            if state == "completed":
                break
            if state == "failed":
                message = status.get("error") or status.get("message") or "unknown"
                raise VtonProviderError(
                    "fashn", f"render failed: {message}", code=status.get("error_code")
                )
            time.sleep(POLL_INTERVAL)
        else:
            last = status.get("status") if status else None
            raise VtonProviderError(
                "fashn",
                f"render timed out after {MAX_POLLS * POLL_INTERVAL:.0f}s (last status={last})",
                code="timeout",
            )

        outputs = status.get("output") or status.get("outputs") or []
        if not outputs:
            raise VtonProviderError(
                "fashn", "completed but no output images returned", code="no_output"
            )
        on_model_png = _decode_output(outputs[0])
        seconds = time.perf_counter() - started
        cost = _extract_cost(status)

        meta = StageMeta(
            stage="vton",
            backend="fashn",
            device="api",
            seconds=seconds,  # measured wall-clock; None would be UNMEASURED, never 0.0
            cost_usd=cost,  # None == UNMEASURED
            notes={
                "provider": "fashn",
                "mode": DEFAULT_MODE,
                "category": _map_category(category),
                "model_ref": model_ref or "unset",
                "run_id": run_id,
                "network_calls": polls + 1,
                "confidence_measured": False,  # FASHN v1.5 emits no score; gate rejects on this
            },
        )
        return VtonResult(
            provider="fashn",
            on_model_png=on_model_png,
            # No confidence score in the documented v1.5 response -> UNMEASURED.
            confidence=None,
            garment_detected=True,
            person_detected=True,
            has_artifacts=False,
            meta=meta,
        )
