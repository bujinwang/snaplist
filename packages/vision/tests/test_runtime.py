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


def _force_birefnet(
    monkeypatch: pytest.MonkeyPatch, runnable: bool, why_not: str = "pinned by test"
) -> None:
    monkeypatch.setattr(runtime, "_birefnet_available", lambda: (runnable, why_not))


# ------------------------------------------------------------------ the real machine


def test_probe_is_total_and_returns_known_values() -> None:
    """Whatever this machine is, `probe()` answers and answers with a real backend."""
    info = probe()
    assert info.device in {"cuda", "mps", "cpu"}
    assert info.backend.value in KNOWN_BACKENDS
    assert info.reason
    if not info.torch_available:
        assert info.device == "cpu"
        assert any("torch" in n for n in info.notes)


def test_this_machines_default_path_is_a_backend_that_can_run() -> None:
    """The regression this file now pins: state of the world, not a faked branch.

    E2-T2 round 2 installed torch, and rule 3 ("accelerator ⇒ BiRefNet") then selected a
    backend that `segment()` refuses — on this machine, with no vision change behind it. The
    assertion is deliberately about the *outcome*: whatever `probe()` picks must be runnable.
    """
    info = probe()
    if info.backend is SegmentBackend.BIREFNET:
        from packages.vision.segment import birefnet_available

        runnable, why_not = birefnet_available()
        assert runnable, f"probe() selected birefnet but the stage refuses it: {why_not}"
    if info.device == "cpu" and info.torch_available:
        # An accelerator may well be present; if device says cpu, a note has to say why.
        assert any("accelerator" in n or "CPU" in n for n in info.notes), info.notes


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


def test_accelerated_local_device_selects_birefnet_when_it_can_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rule 3 keeps its intent: acceleration wins *when the backend exists*."""
    _force_torch(monkeypatch, present=True, device="mps")
    _force_birefnet(monkeypatch, runnable=True)
    info = probe()
    assert info.device == "mps"
    assert info.backend is SegmentBackend.BIREFNET
    assert "runnable" in info.reason


def test_probe_asks_segment_py_instead_of_keeping_its_own_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real delegation: `_birefnet_available` must forward to `segment.birefnet_available()`.

    Every other policy test in this file monkeypatches `_birefnet_available`, which means the
    wiring between the policy and `segment.py` — the thing this round's regression was about —
    had no test at all. Measured: make `_birefnet_available` return `(True, "")`
    unconditionally with the accelerator faked to mps and the whole suite stayed green
    (193 passed, 0 failed), even though `probe()` then returns `mps birefnet`, i.e. the original
    bug reproducing line for line.

    So this test fakes only the *accelerator* and lets the real delegation run. It can only
    pass while `segment.birefnet_available()` says BiRefNet is not runnable — which is the
    point: implementing BiRefNet means changing this test and the policy together.
    """
    _force_torch(monkeypatch, present=True, device="mps")  # deliberately no _force_birefnet
    info = probe()
    assert info.backend is SegmentBackend.U2NET
    assert info.device == "cpu"
    assert any("BiRefNet cannot run" in note for note in info.notes), info.notes


def test_acceleration_without_a_runnable_birefnet_falls_back_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The E2-T2 round-2 regression: torch present, BiRefNet absent ⇒ never birefnet.

    Three separate assertions, because each one alone is satisfiable while the bug survives:
    the backend must not be birefnet, the device must not claim the unused accelerator, and
    the note must name both the accelerator and the reason. Flip `_birefnet_available` back to
    "always True" and the first one goes red.
    """
    _force_torch(monkeypatch, present=True, device="mps")
    _force_birefnet(monkeypatch, runnable=False, why_not="no implementation and no weights")
    info = probe()
    assert info.backend is SegmentBackend.U2NET
    assert info.backend is not SegmentBackend.BIREFNET
    assert info.device == "cpu", "u2net runs on the CPU EP; claiming mps would be a lie"
    assert info.torch_available is True, "torch is present; it just does not help here"
    joined = " ".join(info.notes)
    assert "mps is present" in joined
    assert "no implementation and no weights" in joined
    assert "cheap backend" in joined


def test_override_to_an_unrunnable_backend_is_honoured_but_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An override must not be silently swapped — but the note must predict the refusal."""
    _force_torch(monkeypatch, present=True, device="mps")
    _force_birefnet(monkeypatch, runnable=False, why_not="no implementation and no weights")
    monkeypatch.setenv(OVERRIDE_ENV, "birefnet")
    info = probe()
    assert info.backend is SegmentBackend.BIREFNET
    assert any("will be refused" in n for n in info.notes), info.notes


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
