"""E3-T2 `http` adapter — one real VLM behind the OpenAI-compatible vision protocol.

No vendor is named here. docs/RUNBOOK.md §6 leaves the provider decision to Bert and forbids
inventing a provider name, price or accuracy figure. So the endpoint, model id and key are all
read from config at call time:

    SNAPLIST_VLM_BASE_URL   e.g. an OpenAI-compatible /chat/completions base URL
    SNAPLIST_VLM_MODEL      the model id the account has access to
    VLM_API_KEY             bearer token (RUNBOOK §7 name; still pending there)

Picking a vendor is then `export` three variables, not a code change — E3-T1's DoD. Until
those are set, `extract()` raises `AttributesUnavailable` with the missing names listed; it
never pretends to have called a model and never falls back to the stub silently.

Constraints from BRIEF.md / CONTRACTS.md §4 honoured here:
  * `httpx` is imported lazily INSIDE extract(), so importing this module (or attrs.py) pulls
    in no provider SDK and `make test` works with zero credentials;
  * the provider round-trip seconds are printed per call and recorded in `StageMeta`;
  * a schema failure after one repair retry raises `AttributeExtractionFailed` whose
    reason_code is `low_confidence`, never `internal`;
  * brand/material without evidence are dropped by base._apply_evidence_and_confidence.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass

from apps.api.schemas import ItemAttributes
from packages.vision.attrs import AttributeExtractionFailed, AttributesUnavailable
from packages.vision.attrs_adapters.base import extract_from_response
from packages.vision.types import StageMeta

_BASE_URL_ENV = "SNAPLIST_VLM_BASE_URL"
_MODEL_ENV = "SNAPLIST_VLM_MODEL"
_KEY_ENV = "VLM_API_KEY"
# Optional JSON object merged verbatim into the request body. This is the vendor-neutral
# escape hatch for provider-specific knobs (thinking toggles, response_format, max_tokens).
# Keeping them in config is what makes swapping providers a config change, not a code change.
_EXTRA_BODY_ENV = "SNAPLIST_VLM_EXTRA_BODY"
_TIMEOUT_S = 45.0


def _message_content(body: object) -> str:
    """Pull the assistant text out of a response body, failing loudly on malformed shapes.

    Pure function, so it is testable with literal dicts rather than a stubbed network call
    (BRIEF.md forbids stubbed network responses in the acceptance path). Blank content raises
    ValueError so `extract_from_response` spends its ONE repair retry on it — DeepSeek's JSON
    mode replaces unparseable output with spaces, so whitespace-only text is a real failure
    mode that a prompt repair can fix.
    """
    if not isinstance(body, dict):
        raise AttributeExtractionFailed("provider response body is not an object", attempts=1)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AttributeExtractionFailed(
            f"provider returned no message content: {type(exc).__name__}: {exc}",
            attempts=1,
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise ValueError(
            "provider returned blank content (JSON mode may replace non-JSON output with "
            "spaces); retry with a corrected prompt"
        )
    return content


def build_payload(cfg: _Config, prompt: str, data_url: str) -> dict:
    """Build the chat-completions body. Pure, so vendor knobs are testable without a socket.

    `cfg.extra_body` is merged LAST and wins over the defaults: a provider that rejects
    `temperature` must be able to override it, and provider-specific fields (thinking toggle,
    response_format, max_tokens) belong in config rather than in this function.
    """
    return {
        "model": cfg.model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the attributes of this garment."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        **cfg.extra_body,
    }


def _image_mime(image: bytes) -> str:
    """Sniff the real type. Hardcoding image/jpeg silently corrupts PNG/WebP uploads."""
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    raise ValueError("unsupported image type for the VLM (need JPEG/PNG/WebP/GIF)")


@dataclass(slots=True)
class _Config:
    base_url: str
    model: str
    key: str
    extra_body: dict
    timeout: float = _TIMEOUT_S


def _config_from_env() -> _Config:
    # .env is gitignored (.gitignore:19), so a key placed there never reaches the repo.
    # python-dotenv is already in docs/DEPS.md (transitive via pydantic-settings); it is
    # imported lazily here for the same reason as httpx: no import-time side effects.
    # override=False: an explicitly exported env var always wins over the file.
    try:
        from dotenv import find_dotenv, load_dotenv

        # usecwd=True: load_dotenv() otherwise walks up from *this file's* directory, which
        # is packages/vision/attrs_adapters/ and never sees the repo-root .env.
        load_dotenv(find_dotenv(usecwd=True), override=False)
    except ImportError:  # pragma: no cover - only if the dep is ever removed
        pass

    missing = [name for name in (_BASE_URL_ENV, _MODEL_ENV, _KEY_ENV) if not os.environ.get(name)]
    if missing:
        raise AttributesUnavailable(
            "no VLM configured; missing environment variables: "
            + ", ".join(missing)
            + ". See .env.example for the DeepSeek V4.1 Flash values chosen on 2026-11-04 "
            "(ADR-006). Set all three to run the real acceptance path; do not substitute "
            "the stub."
        )
    raw_extra = os.environ.get(_EXTRA_BODY_ENV, "").strip()
    try:
        extra = json.loads(raw_extra) if raw_extra else {}
    except json.JSONDecodeError as exc:
        raise AttributesUnavailable(
            f"{_EXTRA_BODY_ENV} must be a JSON object: {exc}"
        ) from exc
    if not isinstance(extra, dict):
        raise AttributesUnavailable(f"{_EXTRA_BODY_ENV} must be a JSON object, not a list/scalar")
    return _Config(
        base_url=os.environ[_BASE_URL_ENV].rstrip("/"),
        model=os.environ[_MODEL_ENV],
        key=os.environ[_KEY_ENV],
        extra_body=extra,
    )


class HttpVlmExtractor:
    """Real provider call. `last_meta` carries the measured round-trip for the cost report."""

    name = "http"

    def __init__(self) -> None:
        self.last_meta: StageMeta | None = None

    def extract(self, image: bytes) -> ItemAttributes:
        if not image:
            raise ValueError("http extractor: image bytes are empty")
        cfg = _config_from_env()

        # Lazy on purpose: no provider SDK is imported by the package, only at call time.
        import httpx

        encoded = base64.b64encode(image).decode("ascii")
        data_url = f"data:{_image_mime(image)};base64,{encoded}"
        usage_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

        def complete(prompt: str) -> str:
            payload = build_payload(cfg, prompt, data_url)
            try:
                with httpx.Client(timeout=cfg.timeout) as client:
                    response = client.post(
                        f"{cfg.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {cfg.key}"},
                        json=payload,
                    )
                    response.raise_for_status()
                    body = response.json()
            except httpx.HTTPStatusError as exc:
                # CONTRACTS §4: a provider outage must be low_confidence with the provider
                # status in error_detail, never `internal` (which would page us for their bug).
                raise AttributeExtractionFailed(
                    f"provider HTTP {exc.response.status_code}: {exc.response.text[:300]}",
                    attempts=1,
                ) from exc
            except httpx.HTTPError as exc:
                raise AttributeExtractionFailed(
                    f"provider transport error: {type(exc).__name__}: {exc}", attempts=1
                ) from exc
            # Real usage, not an estimate: BRIEF.md's <= $0.03/item ceiling is a gate and
            # cannot be reported without token counts. Summed across the repair retry too.
            usage = body.get("usage") or {}
            for key in usage_totals:
                value = usage.get(key)
                if isinstance(value, int | float) and value >= 0:
                    usage_totals[key] += int(value)
            return _message_content(body)

        started = time.perf_counter()
        outcome = extract_from_response(complete, model_version=f"http:{cfg.model}")
        seconds = time.perf_counter() - started

        # E3-T2 acceptance: print the round-trip seconds per call AND record in StageMeta.
        print(f"attrs provider round-trip: {seconds:.3f}s (attempts={outcome.attempts})")
        self.last_meta = StageMeta(
            stage="attrs",
            backend=f"http:{cfg.model}",
            device="hosted",
            seconds=round(seconds, 3),
            cost_usd=None,  # UNMEASURED until a provider and its price are chosen
            notes={
                "attempts": outcome.attempts,
                "repaired": outcome.repaired,
                "dropped_fields": list(outcome.dropped_fields),
                "model_id": cfg.model,
                "prompt_tokens": usage_totals["prompt_tokens"],
                "completion_tokens": usage_totals["completion_tokens"],
                # cost_usd stays None: RUNBOOK §6 forbids inventing a price. Convert tokens
                # to dollars only after Bert picks a provider and its rate is recorded.
            },
        )
        return outcome.attributes
