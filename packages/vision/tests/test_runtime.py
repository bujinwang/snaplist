"""Tests for the E1-T1 runtime probe (packages/vision/runtime.py).

The probe's whole job is to make a policy decision *honestly*: the tests force
each branch by faking the two environment inputs (torch presence, hosted
credentials) and assert on the decision, the reason and the notes. No branch may
be reachable only in production.

These tests also prove the DoD line "a missing CUDA build is a logged fallback,
not an exception": every branch returns, and only an *invalid explicit override*
raises.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

import packages.vision.runtime as runtime
from packages.vision.runtime import OVERRIDE_ENV, probe
from packages.vision.types import SegmentBackend

KNOWN_BACKENDS = {b.value for b in SegmentBackend} - {SegmentBackend.UNMEASURED.value}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test states its full environment; nothing leaks in from the shell."""
    for var in (OVERRIDE_ENV, "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"):
        monkeypatch.delenv(var, raising=False)
    yield


def _force_torch(monkeypatch: pytest.MonkeyPatch, present: bool, device: str) -> None:
    monkeypatch.setattr(runtime, "_detect_torch", lambda: (present, device))


def _force_hosted(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    monkeypatch.setattr(runtime, "_hosted_available", lambda: present)


# ------------------------------------------------------------------ the real machine


def test_probe_is_total_and_returns_known_values() -> None:
    """On this repo's venv torch is absent, so this exercises the honest-cpu path."""
    info = probe()
    assert info.device in {"cuda", "mps", "cpu"}
    assert info.backend.value in KNOWN_BACKENDS
    assert info.reason
    if not info.torch_available:
        assert info.device == "cpu"
        assert any("torch" in n for n in info.notes)


def test_probe_performs_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("probe() attempted a network call")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    probe()


# ------------------------------------------------------------------ policy branches


def test_hosted_credentials_select_sam2_hosted_even_on_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_torch(monkeypatch, present=True, device="cpu")
    _force_hosted(monkeypatch, present=True)
    info = probe()
    assert info.backend is SegmentBackend.SAM2_HOSTED
    assert info.hosted_available is True
    assert "SAM 2.1 hosted" in info.reason


def test_accelerated_local_device_selects_birefnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_torch(monkeypatch, present=True, device="mps")
    info = probe()
    assert info.device == "mps"
    assert info.backend is SegmentBackend.BIREFNET


def test_cpu_only_selects_u2net_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_torch(monkeypatch, present=True, device="cpu")
    info = probe()
    assert info.device == "cpu"
    assert info.backend is SegmentBackend.U2NET
    assert any("cheap" in n or "lower" in n for n in info.notes), (
        "the DoD requires the downgrade to be stated in notes, not silent"
    )


def test_torch_absent_is_a_logged_fallback_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_torch(monkeypatch, present=False, device="cpu")
    _force_hosted(monkeypatch, present=False)
    info = probe()  # must not raise
    assert info.torch_available is False
    assert info.device == "cpu"
    assert info.backend is SegmentBackend.U2NET
    assert any("torch is not installed" in n for n in info.notes)


# ------------------------------------------------------------------ explicit override


def test_explicit_override_wins_over_every_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_hosted(monkeypatch, present=True)
    monkeypatch.setenv(OVERRIDE_ENV, "u2net")
    info = probe()
    assert info.backend is SegmentBackend.U2NET
    assert OVERRIDE_ENV in info.reason
    assert any("override" in n for n in info.notes)


def test_invalid_override_raises_instead_of_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OVERRIDE_ENV, "gpt4o")
    with pytest.raises(ValueError, match=OVERRIDE_ENV):
        probe()


def test_override_is_case_insensitive_and_whitespace_tolerant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OVERRIDE_ENV, "  BiRefNet  ")
    info = probe()
    assert info.backend is SegmentBackend.BIREFNET


# ------------------------------------------------------------------ StageMeta plumbing


def test_stage_meta_carries_the_decision() -> None:
    info = probe()
    meta = info.stage_meta()
    assert meta.stage == "segment"
    assert meta.backend is info.backend
    assert meta.device == info.device
    assert meta.seconds is None  # UNMEASURED, never a 0.0 placeholder
    assert meta.notes["reason"] == info.reason
    assert meta.notes["policy_notes"] == list(info.notes)
