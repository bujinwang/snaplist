"""E1-T1 — runtime probe: device + segmentation backend policy.

This module decides *which* segmentation backend a later `segment()` call will use,
and it says so out loud. It performs no network calls, imports no model weights and
never raises on a missing accelerator — a missing CUDA build is a logged fallback,
not an exception (docs/PLAN.md E1-T1 DoD).

Policy (in order; the first rule that fires wins):

1. **Explicit override** — `SNAPLIST_SEGMENT_BACKEND=birefnet|u2net|sam2_hosted`
   selects that backend and the reason says so. An *unknown* value raises:
   a typo'd override must never be silently swapped for a default, because "no
   silent model swap" cuts both ways.
2. **Hosted GPU available** — `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` both set.
   SAM 2.1 hosted is the contract primary (BRIEF.md); it runs on Modal's GPUs,
   so it is chosen regardless of the local device.
3. **Local acceleration** — torch sees `cuda` or `mps`. Local BiRefNet is the
   strongest local fallback (packages/vision/segment.py model order).
4. **CPU only** — the cheap backend (`u2net`), with a note saying quality will be
   lower. Never silent: the note travels in `RuntimeInfo.notes` and therefore into
   `StageMeta` and the eval reports.

If torch itself is absent the device is reported as `cpu` (detection is impossible
without torch) and a note says exactly that — it does not pretend to know.

`probe()` is deliberately pure: same machine + same env ⇒ same answer, and the
whole policy is unit-testable without torch installed (see
`packages/vision/tests/test_runtime.py`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from packages.vision.types import SegmentBackend, StageMeta

OVERRIDE_ENV = "SNAPLIST_SEGMENT_BACKEND"

_KNOWN_BACKENDS: frozenset[str] = frozenset(
    b.value for b in SegmentBackend if b is not SegmentBackend.UNMEASURED
)


@dataclass(slots=True, frozen=True)
class RuntimeInfo:
    """What the runtime decided, and why. `notes` is the audit trail."""

    device: str  # "cuda" | "mps" | "cpu"
    backend: SegmentBackend
    torch_available: bool
    hosted_available: bool
    reason: str
    notes: tuple[str, ...]

    def stage_meta(self, stage: str = "segment") -> StageMeta:
        """Carry the decision into the StageMeta that every stage must populate."""
        return StageMeta(
            stage=stage,
            backend=self.backend,
            device=self.device,
            notes={"reason": self.reason, "policy_notes": list(self.notes)},
        )


def _detect_torch() -> tuple[bool, str]:
    """(torch_present, device). Import-guarded: torch is not a hard dependency.

    Ordered cuda → mps → cpu. An mps probe can raise on some torch builds
    (older versions lack `torch.backends.mps`), and that is an absence of
    acceleration, not a crash.
    """
    try:
        import torch  # noqa: PLC0415 — deliberate: torch must stay optional
    except ImportError:
        return False, "cpu"
    if torch.cuda.is_available():
        return True, "cuda"
    try:
        if torch.backends.mps.is_available():
            return True, "mps"
    except (AttributeError, RuntimeError):
        pass
    return True, "cpu"


def _hosted_available() -> bool:
    """SAM 2.1 hosted runs on Modal, so the Modal token pair is the credential."""
    return bool(os.environ.get("MODAL_TOKEN_ID")) and bool(
        os.environ.get("MODAL_TOKEN_SECRET")
    )


def probe() -> RuntimeInfo:
    """Detect the device and select the segmentation backend. Zero network calls."""
    notes: list[str] = []
    torch_available, device = _detect_torch()
    if not torch_available:
        notes.append(
            "torch is not installed: device detection is unavailable, reporting 'cpu'; "
            "no local accelerated backend can run in this process"
        )
    hosted = _hosted_available()

    override = os.environ.get(OVERRIDE_ENV, "").strip().lower()
    if override:
        if override not in _KNOWN_BACKENDS:
            raise ValueError(
                f"{OVERRIDE_ENV}={override!r} is not one of {sorted(_KNOWN_BACKENDS)}; "
                "refusing to guess — no silent model swap"
            )
        notes.append("explicit override wins over every automatic rule")
        return RuntimeInfo(
            device=device,
            backend=SegmentBackend(override),
            torch_available=torch_available,
            hosted_available=hosted,
            reason=f"explicit override via {OVERRIDE_ENV}={override}",
            notes=tuple(notes),
        )

    if hosted:
        return RuntimeInfo(
            device=device,
            backend=SegmentBackend.SAM2_HOSTED,
            torch_available=torch_available,
            hosted_available=True,
            reason=(
                "hosted GPU credentials present (MODAL_TOKEN_ID/MODAL_TOKEN_SECRET): "
                "SAM 2.1 hosted is the contract primary"
            ),
            notes=tuple(notes),
        )

    if device in ("cuda", "mps"):
        return RuntimeInfo(
            device=device,
            backend=SegmentBackend.BIREFNET,
            torch_available=torch_available,
            hosted_available=False,
            reason=(
                f"{device} available and no hosted credentials: local BiRefNet is "
                "the strongest local fallback in the segment.py model order"
            ),
            notes=tuple(notes),
        )

    notes.append(
        "no CUDA/MPS and no hosted credentials: selecting the cheap backend (u2net); "
        "segmentation quality will be lower — stated here, not silent"
    )
    return RuntimeInfo(
        device="cpu",
        backend=SegmentBackend.U2NET,
        torch_available=torch_available,
        hosted_available=False,
        reason="cpu-only machine without hosted credentials: cheap backend by policy",
        notes=tuple(notes),
    )
