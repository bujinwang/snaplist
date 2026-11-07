"""Tests for E4 — virtual try-on (vton).

No network, no credentials. BRIEF.md forbids mocked model calls in the acceptance path:
these unit tests exercise our own adapter selection, the quality gate, and the stub, which is
exactly what can be tested without a live FASHN/CatVTON call. The real provider round-trip is
UNMEASURED until Bert provisions FASHN_API_KEY / MODAL_TOKEN (packages/vision/BLOCKED.md).

What each DoD line is proven by
--------------------------------
* "stub returns provider=='stub'"             -> test_stub_returns_provider_stub
* "gate rejects low-confidence"               -> test_gate_rejects_low_confidence
* "gate passes high-confidence"               -> test_gate_passes_high_confidence
* "adapter selection picks stub with no keys" -> test_adapter_selection_picks_stub_with_no_keys
* try_on resolves and is callable in mock mode -> test_try_on_imports_without_keys
* the four-reason taxonomy covers every failure -> test_gate_rejects_each_reason
"""

from __future__ import annotations

import pytest

from packages.vision.types import StageMeta
from packages.vision.vton import VtonOutcome, VtonResult, get_adapter, try_on
from packages.vision.vton_adapters.gate import VtonRejectReason, gate
from packages.vision.vton_adapters.stub import StubVtonAdapter

# Fake image bytes. Never decoded or sent to a network call in this file — it only needs to be
# non-empty so the stub's input validation passes and so a VtonResult has *something*.
_FAKE_IMAGE = b"\x89PNG\r\n\x1a\nFAKE-NOT-DECODED"


def _fashn_result(**overrides: object) -> VtonResult:
    """A real-looking FASHN result with sane defaults, overridden per test."""
    values: dict[str, object] = {
        "provider": "fashn",
        "on_model_png": _FAKE_IMAGE,
        "confidence": 0.9,
        "garment_detected": True,
        "person_detected": True,
        "has_artifacts": False,
        "meta": StageMeta(stage="vton"),
    }
    values.update(overrides)
    return VtonResult(
        provider=str(values["provider"]),  # type: ignore[arg-type]
        on_model_png=bytes(values["on_model_png"]),  # type: ignore[arg-type]
        confidence=(None if values["confidence"] is None else float(values["confidence"])),  # type: ignore[arg-type]
        garment_detected=bool(values["garment_detected"]),  # type: ignore[arg-type]
        person_detected=bool(values["person_detected"]),  # type: ignore[arg-type]
        has_artifacts=bool(values["has_artifacts"]),  # type: ignore[arg-type]
        meta=values["meta"],  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------- E4-T1 stub

def test_stub_returns_provider_stub() -> None:
    result = StubVtonAdapter().render(_FAKE_IMAGE, _FAKE_IMAGE)
    assert result.provider == "stub"
    # Never fake a successful render: no image bytes, and the gate must reject it.
    assert result.on_model_png == b""
    passed, _ = gate(result)
    assert passed is False


# ------------------------------------------------------- E4-T3 quality gate (taxonomy)

def test_gate_rejects_low_confidence() -> None:
    passed, reason = gate(_fashn_result(confidence=0.2))
    assert passed is False
    assert reason is VtonRejectReason.LOW_CONFIDENCE


def test_gate_passes_high_confidence() -> None:
    passed, reason = gate(_fashn_result(confidence=0.95))
    assert passed is True
    assert reason is None


def test_gate_rejects_each_reason() -> None:
    assert gate(_fashn_result(person_detected=False))[1] is VtonRejectReason.PERSON_NOT_DETECTED
    assert (
        gate(_fashn_result(garment_detected=False))[1] is VtonRejectReason.GARMENT_NOT_DETECTED
    )
    assert gate(_fashn_result(has_artifacts=True))[1] is VtonRejectReason.ARTIFACTS
    # None confidence is UNMEASURED and must fail the floor exactly like a low score.
    assert gate(_fashn_result(confidence=None))[1] is VtonRejectReason.LOW_CONFIDENCE


# ----------------------------------------------------------- E4-T2 adapter selection

def test_adapter_selection_picks_stub_with_no_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FASHN_API_KEY", raising=False)
    monkeypatch.delenv("MODAL_TOKEN", raising=False)
    assert get_adapter().name == "stub"


def test_try_on_imports_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FASHN_API_KEY", raising=False)
    monkeypatch.delenv("MODAL_TOKEN", raising=False)
    outcome = try_on(_FAKE_IMAGE, _FAKE_IMAGE)
    assert isinstance(outcome, VtonOutcome)
    assert outcome.result.provider == "stub"
    assert outcome.passed_gate is False
    assert outcome.reason_code == "vton_rejected"


def test_get_adapter_unknown_name_names_the_available_ones() -> None:
    with pytest.raises(ValueError, match="available:"):
        get_adapter("made-up-vendor")


def test_get_adapter_named_fashn_without_key_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    from packages.vision import StageNotImplemented

    monkeypatch.delenv("FASHN_API_KEY", raising=False)
    with pytest.raises(StageNotImplemented, match="FASHN_API_KEY"):
        get_adapter("fashn")
