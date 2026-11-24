"""Tests for the parallel Google Nano Banana adapter. No network, no credentials.

These prove the adapter's own contract: explicit-name-only registration, honest
UNMEASURED confidence/cost, token-usage capture, and the no-fake-render error mapping. They do
NOT claim that Nano Banana is a good VTON provider — that quality result is UNMEASURED until the
12-garment golden set is run (prompts/03-vton.md).
"""

from __future__ import annotations

import base64
import json
import urllib.error

import pytest

from packages.vision import StageNotImplemented
from packages.vision.vton import VtonProviderError, available_adapters, get_adapter
from packages.vision.vton_adapters import google_banana as gb
from packages.vision.vton_adapters.gate import VtonRejectReason, gate

_FAKE_PNG = b"\x89PNG\r\n\x1a\nFAKE-PNG-NOT-DECODED"
_FAKE_JPEG = b"\xff\xd8\xffFAKE-JPEG"


def _success_body() -> dict:
    return {
        "id": "v1_interaction_fake",
        "status": "completed",
        "object": "interaction",
        "model": gb.MODEL,
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {"type": "image", "data": base64.b64encode(_FAKE_PNG).decode("ascii"),
                     "mime_type": "image/png"}
                ],
            }
        ],
        "usage": {"input_tokens": 2240, "output_tokens": 1120, "total_tokens": 3360},
    }


# --------------------------------------------------------------- registration / selection

def test_google_banana_is_registered_but_not_auto_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "google_banana" in available_adapters()
    monkeypatch.delenv("FASHN_API_KEY", raising=False)
    monkeypatch.delenv("MODAL_TOKEN", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "a-key-must-not-move-production")
    # ADR-001 §8 is unchanged: having a Gemini key alone never changes the primary path.
    assert get_adapter().name == "stub"


def test_named_google_banana_without_key_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(StageNotImplemented, match="GEMINI_API_KEY"):
        get_adapter("google_banana")


def test_named_google_banana_with_key_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert get_adapter("google_banana").name == "google_banana"


# ------------------------------------------------------------------------ request / result

def test_render_sends_documented_payload_and_stays_unmeasured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    captured: dict[str, object] = {}

    def fake_post(url: str, headers: dict[str, str], payload: dict, timeout: float) -> dict:
        captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return _success_body()

    monkeypatch.setattr(gb, "_post_json", fake_post)

    result = gb.GoogleBananaVtonAdapter().render(
        _FAKE_PNG, _FAKE_PNG, model_ref="stock-model-03", category="dress"
    )

    assert captured["url"] == gb.BASE_URL
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-goog-api-key"] == "secret-key"
    assert headers["Api-Revision"] == gb.API_REVISION

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "gemini-nano-banana-2.1"
    assert payload["response_format"] == {"type": "image", "image_size": "1K"}
    inputs = payload["input"]
    assert [part["type"] for part in inputs] == ["image", "image", "text"]  # type: ignore[index]
    assert base64.b64decode(inputs[0]["data"]) == _FAKE_PNG  # type: ignore[index]
    assert base64.b64decode(inputs[1]["data"]) == _FAKE_PNG  # type: ignore[index]
    assert "dress" in inputs[2]["text"]  # type: ignore[index]

    assert result.provider == "google_banana"
    assert result.on_model_png == _FAKE_PNG
    # No confidence score from the provider -> gate rejects, exactly like FASHN/CatVTON.
    assert result.confidence is None
    assert gate(result)[1] is VtonRejectReason.LOW_CONFIDENCE
    assert result.meta.backend == "google_banana"
    assert result.meta.cost_usd is None
    assert result.meta.notes["confidence_measured"] is False
    assert result.meta.notes["usage"] == _success_body()["usage"]
    assert result.meta.notes["model_ref"] == "stock-model-03"
    assert result.meta.seconds is not None and result.meta.seconds >= 0
    assert gb.GoogleBananaVtonAdapter.cost_per_render_usd is None


def test_render_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError, match="both required"):
        gb.GoogleBananaVtonAdapter().render(b"", _FAKE_PNG)


# -------------------------------------------------------------------------- failure mapping

def test_http_error_becomes_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def raise_http(*_args: object, **_kwargs: object) -> dict:
        raise urllib.error.HTTPError(gb.BASE_URL, 429, "rate limited", {}, None)

    monkeypatch.setattr(gb, "_post_json", raise_http)
    with pytest.raises(VtonProviderError) as caught:
        gb.GoogleBananaVtonAdapter().render(_FAKE_PNG, _FAKE_PNG)
    assert caught.value.code == "429"
    assert "google_banana" in str(caught.value)


def test_completed_interaction_without_image_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    body = _success_body()
    body["steps"] = [{"type": "model_output", "content": [{"type": "text", "text": "no"}]}]
    monkeypatch.setattr(gb, "_post_json", lambda *_a, **_k: body)
    with pytest.raises(VtonProviderError) as caught:
        gb.GoogleBananaVtonAdapter().render(_FAKE_PNG, _FAKE_PNG)
    assert caught.value.code == "no_image"


def test_non_png_output_is_never_labelled_a_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    body = _success_body()
    body["steps"][0]["content"][0] = {  # type: ignore[index]
        "type": "image", "data": base64.b64encode(_FAKE_JPEG).decode("ascii"),
        "mime_type": "image/png",
    }
    monkeypatch.setattr(gb, "_post_json", lambda *_a, **_k: body)
    with pytest.raises(VtonProviderError) as caught:
        gb.GoogleBananaVtonAdapter().render(_FAKE_PNG, _FAKE_PNG)
    assert caught.value.code == "bad_mime"


def test_unparsable_response_is_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(gb, "_post_json", lambda *_a, **_k: json.loads("not-json"))
    with pytest.raises(VtonProviderError) as caught:
        gb.GoogleBananaVtonAdapter().render(_FAKE_PNG, _FAKE_PNG)
    assert caught.value.code == "bad_response"
