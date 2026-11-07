"""E4-T1 `stub` adapter — a tests/CI fixture, deliberately NOT a try-on model.

PLAN.md E4 requires at least one offline, credential-less adapter so the registry, the gate,
and the selection path can run with zero credentials and zero network. This is it. It exists
so `make test` stays green on a clean checkout and so the default `get_adapter()` (no key) has
something honest to return.

It must never be mistaken for a render:
  * `provider` is literally `"stub"`, so it shows up in every report and outcome;
  * `on_model_png` is empty bytes — no synthetic garment-on-model image is invented;
  * every detection flag is `False` and `confidence` is `None`, so the quality gate rejects
    it (BLOCKED.md explains why real renders cannot happen without a credential).

BRIEF.md forbids mocked model calls in the acceptance path, so this is returned only when no
provider credential is configured; the default provider selection picks it precisely so a
credential-less build fails honestly instead of faking a success.
"""

from __future__ import annotations

from packages.vision.types import StageMeta
from packages.vision.vton import VtonResult


class StubVtonAdapter:
    """Deterministic, offline, and openly fake. Never used to report a render succeeded."""

    name = "stub"
    # No spend and no measurement possible: UNMEASURED, not 0.0.
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
                "stub vton: person_image and garment_image bytes are both required"
            )
        # Honest non-render. No FASHN_API_KEY / MODAL_TOKEN in this build, so no image is
        # produced. The gate rejects this result — we never fake a garment-on-model image.
        meta = StageMeta(
            stage="vton",
            backend="stub",
            device="unmeasured",
            seconds=None,
            cost_usd=None,
            notes={
                "provider": "stub",
                "blocked": True,
                "model_ref": model_ref or "unset",
                "category": category or "auto",
            },
        )
        return VtonResult(
            provider="stub",
            on_model_png=b"",
            confidence=None,
            garment_detected=False,
            person_detected=False,
            has_artifacts=False,
            meta=meta,
        )
