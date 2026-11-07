# BLOCKED.md — E4 (virtual try-on) real renders are BLOCKED

**Status: BLOCKED.** No real on-model render can be produced by this build. The code path is
fully implemented and importable; what is missing is the credential and the calibrated quality
signal, not the logic.

## What is blocked and why

| Provider | Env var required | Blocker |
|---|---|---|
| FASHN VTON v1.5 (`fashn`) | `FASHN_API_KEY` | No API key in this build. |
| CatVTON on Modal (`catvton`) | `MODAL_TOKEN` (+ `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` to deploy) | No token, and the Modal function in `infra/modal_vton.py` is a **stub** — CatVTON inference is not wired up. |

With neither env var set, `get_adapter()` (no name) returns `StubVtonAdapter`, whose
`render()` returns `provider="stub"`, an **empty** `on_model_png`, and detection flags all
`False`. The quality gate (`vton_adapters/gate.py`) rejects that result, and `try_on()` returns
`reason_code="vton_rejected"` with this BLOCKED message in `error_detail`.

**We do not fake a render.** No synthetic garment-on-model image is ever produced and passed
off as a success. A credential-less build fails honestly.

## Even with a credential, real renders are still gated out

FASHN v1.5 returns no per-render confidence score, so `VtonResult.confidence` is `None`
(UNMEASURED). The quality gate rejects any render whose confidence is `None` or below the
floor (`vton_adapters/gate.py::CONFIDENCE_FLOOR`). Shipping therefore also requires the E4
quality-gate calibration on the golden set (E0) — measuring a real confidence signal
(face-similarity / garment-colour-drift per `prompts/03-vton.md`). That is a measurement gap,
not a code gap, and `cost_per_render_usd` likewise stays `None` until `$/render` is computed
from observed latency × the published rate (E4-T1 DoD).

## Exact error text thrown

- `get_adapter("fashn")` with no key →
  `StageNotImplemented: vton provider 'fashn' needs FASHN_API_KEY (real render is BLOCKED); …`
- `FashnVtonAdapter.render()` with no key → same `StageNotImplemented`.
- `get_adapter("catvton")` with no `MODAL_TOKEN` →
  `StageNotImplemented: vton provider 'catvton' needs MODAL_TOKEN (real render is BLOCKED); …`
- `try_on()` with no keys → `VtonOutcome(reason_code="vton_rejected", …)` with
  `"E4 real render BLOCKED: no FASHN_API_KEY/MODAL_TOKEN; …"` in `error_detail`.

## How to unblock (in order)

1. Provision `FASHN_API_KEY` (commercial licence) and confirm a real render against the
   golden set; or deploy `infra/modal_vton.py` to Modal with `MODAL_TOKEN` and complete the
   CatVTON inference stub.
2. Run the E4 eval suite (`python -m evals.run --suite vton`) to measure `$/render` and a real
   confidence signal, then record them in `VtonResult.confidence` / `cost_per_render_usd`.
3. Re-validate the quality gate against the measured confidence distribution.

Until then `make test` is green on a clean checkout because the stub + gate are fully
exercised offline, and `import packages.vision.vton` resolves with zero credentials.
