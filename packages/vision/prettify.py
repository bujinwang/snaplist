"""STAGE 3 — catalog-grade prettify. Owner: CV role (E2, ticket E2-T2).

    prettify(flat_png: bytes, alpha_png: bytes, *, mode: PrettifyMode,
             protect: Sequence[tuple[int, int, int, int]] | None = None) -> PrettifyResult

What is implemented here, and under which name
---------------------------------------------
E2-T2 is a two-round ticket. **Round 1** shipped no model and downloaded nothing; **round 2
(this code)** adds the generative step behind `mode=SDXL_INPAINT`, and it still downloads
nothing at run time — the 6.9 GB of weights are an explicit `make fetch-sdxl`. What exists,
executed for real:

1. **white balance** — gains estimated from the backdrop ring, so the flat-lay's background
   lands on `#F2F1EE` instead of on whatever the phone's auto-white-balance chose;
2. **wrinkle / crease mask** — high-pass luminance, local contrast, directional coherence,
   morphology, with a hard coverage cap and a hard boundary exclusion;
3. **logo / text protection** — an automatic thin-ink detector *plus* caller-supplied boxes,
   subtracted from the wrinkle mask in the last step;
4. **fill**, in two flavours that share steps 1–3 and nothing else:
   * `mode=CLASSICAL` — low-frequency luminance guidance with feathered edges. It is called
     **shadow/crease attenuation** and that name is the promise: it reduces the contrast of a
     shadow band, it does not remove a crease, and nothing here invents texture;
   * `mode=SDXL_INPAINT` — SDXL inpainting inside the feathered mask (`prettify_sdxl()`). It
     *does* synthesise fabric texture, inside the mask only, and the composite is written so
     that "outside the feathered mask, the output is the white-balanced input" is true by
     construction rather than by observation.

Both model paths (this one and `segment.py`) refuse rather than fetch: a pipeline stage that
downloads 7 GB on first call is a network call inside a zero-network contract, and it turns a
missing file into a ten-minute stall inside a job.

Why `PrettifyMode.CLASSICAL` had to be added instead of reusing `PASS_THROUGH`
------------------------------------------------------------------------------
`PASS_THROUGH` means *the bytes come back untouched*, and
`test_pass_through_is_byte_identical` pins that. The steps above obviously change pixels, so
labelling their output `pass_through` would be the exact dishonesty BRIEF.md forbids — the
report would claim "no cleanup was applied" about an image that was cleaned. `CLASSICAL` is
the honest label: non-generative, deterministic, model-free. `notes["mode"]` always carries
the mode that actually ran, and `notes["generative_step"]` says whether a model contributed.

The two hard constraints, and why the numbers are what they are
---------------------------------------------------------------
**Coverage cap `WRINKLE_COVERAGE_CAP = 0.25`** — the final wrinkle mask may not exceed a
quarter of the garment's interior area. Reason: a crease is a line, not a region. A mask that
covers 60 % of the garment is not de-wrinkling, it is repainting the garment through a
low-pass filter, and the SSIM number in `evals/reports/vision.md` would still look healthy
while the product got worse. The cap is enforced by *raising the threshold* until coverage
fits (see `_cap_threshold`), so it is a real rail: it binds whenever the content would
otherwise exceed it, and `notes["wrinkle_mask"]["cap_bound"]` records whether it did.

**Boundary band `WRINKLE_BOUNDARY_BAND_PX`** — nothing within N px of the garment's outline
may enter the mask, N = max(2, round(1.5 % of the short edge)) ≈ 8 px at 560 px. Reason: the
fill works by pulling a low-frequency guide inward, and at the silhouette the guide is
half backdrop. A mask pixel on the outline therefore paints backdrop grey into the garment
and eats the shape. The exclusion is a Chebyshev erosion of the alpha, so it cannot leak.

What this does *not* do (and the eval notes say so out loud)
------------------------------------------------------------
* `mode=CLASSICAL` removes no crease: a hard fold stays visible, dimmer. `mode=SDXL_INPAINT`
  does replace the fold's gradient with generated texture, and whether that reads as "fixed"
  is E2-T3's manual rubric, not a metric in this file.
* **A generative fill can invent plausible detail.** That is what the model is for and it is
  also the risk: inside the mask there is no ground truth to compare against. The mask is a
  quarter of the garment *at most* (`WRINKLE_COVERAGE_CAP`) and the protection region and the
  boundary band are excluded exactly, which bounds the damage — it does not make the output
  verifiable. On the two fixtures measured for round 2 the answer to "does it look repainted"
  is **yes**: the folds come back as thin hard blue-grey scratch lines. The machine is
  deterministic, protected and timed; the picture is not good enough to ship. The reason is
  geometric (a hairline mask is sub-latent for an 8×-downsampling VAE) and it is written up in
  `prettify_sdxl()` and `docs/HANDOFF-vision.md` §"E2-T2 round 2", not smoothed over here.
* The automatic logo detector is a **hand-tuned, multi-scale ink filter** — measured on
  synthetic overlays and synthetic type, never on real packaging. Recall is
  **scale-relative** and depends on the glyph string, so it is quoted per probe rather than as
  one number. Measured at the factory settings (`PROTECT_DILATE_MIN_PX = 4`, score floor 0.30)
  with `test_prettify.printed_type_case` (short edge 260 px, so the px sizes below are ~4–18 %
  of it), coverage = share of printed pixels the protection region covers:

  | probe | printed px | covered | mask leak |
  |---|---|---|---|
  | `SNAP` @48 | 1389 | 100 % | 0 |
  | `SNAP` @48, light on dark | 1389 | 100 % | 0 |
  | `SNAP` @36 (navy on white) | 737 | 100 % | 0 |
  | `SNAP` @24 | 325 | 100 % | 0 |
  | `SNAP` @14, light on dark | 108 | 100 % | 0 |
  | `SNAP` @11 (≈4 % of the short edge) | 71 | ≈58 % | 30 |
  | `SNAP` @9 | 55 | 0 % | 55 |
  | `MADE IN PORTUGAL` @36 | 1774 | ≈97 % | 66 |
  | `SNAPLIST` @36 | 1221 | ≈96 % | 54 |
  | `M` @48 | 561 | ≈92 % | 38 |

  So: **the "printed ink = 0 px" result holds on the fixture set (`ink/*.png`) and on the
  four equal-length `SNAP` probes, and it does not generalise.** Other ordinary wordmarks
  leak a few dozen pixels — thick-stroke interiors and long strings are the known failure
  mode, and single-glyph or small-string probes are the worst. Those rows are listed as known
  failures rather than asserted, because tightening the mask until they pass would cost
  de-wrinkling everywhere (the protection region already covers 22 % of the garment on average
  and 69 % on the worst fixture). It will also miss embossed and tonal prints, and it can fire
  on pinstripes, zip pulls and tartan.
* **The label *rectangle* is not fully protected without a caller box.** The printed pixels of
  the fixtures are (`ink/*.png` ground truth ⇒ 0 px overlap), but blank padding inside a label
  is pixel-identical to fabric, so a residual of a few dozen pixels per fixture survives — it is
  reported as `auto only px` by `evals/metrics/prettify.py` and pinned by an `xfail(strict)`
  test, not asserted away.
* Nothing here is colour-managed beyond 8-bit sRGB in / 8-bit sRGB out.

The signature is frozen by `test_public_signatures_are_stable`; `protect` was added as a
default-`None` keyword so no existing caller changes.
"""

from __future__ import annotations

import importlib.util
import io
import math
import os
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from packages.vision import StageNotImplemented
from packages.vision.types import PrettifyMode, StageMeta

# ----------------------------------------------------------------------------- constants

# Flat-lay backdrop, BRIEF.md / prompts/02-vision.md: neutral #F2F1EE, sRGB. White balance
# pulls the photographed backdrop onto this value; it is also the value the composite uses.
BACKDROP_RGB: tuple[int, int, int] = (242, 241, 238)

# Gain bounds. A gain outside this window is not a white-balance error, it is a different
# photograph (or a garment filling the frame, so the "background" sample is not background).
# Clamping keeps a bad estimate from washing the garment out — the DoD's "只做增益" is about
# the *kind* of correction, this bound is about the magnitude.
BALANCE_GAIN_MIN = 0.75
BALANCE_GAIN_MAX = 1.35
# Ring width used to pick backdrop pixels: "outside the garment, one band away from it", so
# the garment's own antialiased edge does not contaminate the sample.
BALANCE_RING_MIN_PX = 3
BALANCE_RING_FRACTION = 0.010
# Below this many ring pixels there is no background to measure and the gains are left at 1.0
# with `notes["white_balance_skipped"]` saying so. A garment cropped to the frame is common.
BALANCE_MIN_BACKGROUND_PX = 500

# Wrinkle mask. See the module docstring for why the cap is 0.25 and the band is 1.5 %.
WRINKLE_COVERAGE_CAP = 0.25
WRINKLE_BOUNDARY_FRACTION = 0.015
WRINKLE_BOUNDARY_MIN_PX = 2
# A crease must be at least this much darker than its local mean, in units of the local
# standard deviation. 0.55 was chosen on the 9 fixtures in `evals/fixtures/prettify/`. Two
# different numbers come out of that tuning and they must not be conflated:
#   * **natural coverage** — what the score floor alone selects, on the *interior*: 7.0–17.3 %;
#   * **final coverage** — what survives the cap, morphology and the protection subtraction:
#     4.4–12.5 % (`notes["wrinkle_mask"]["coverage"]`, and the `mask cov` column of
#     `python -m evals.metrics.prettify`).
# The *crease-band* pickup is reported as `crease_hit` by that same tool (3.6–12.3 % of the
# interior inside each drawn crease box) and is reproducible from the committed fixtures; an
# earlier draft quoted it here as "8–21 %", which the manifest cannot substantiate.
# 0.80 keeps only the darkest crease cores; 0.35 starts latching onto knit texture.
WRINKLE_SCORE_MIN = 0.55
# Floor added to the local standard deviation so a perfectly flat region cannot divide by ~0
# and turn quantisation noise into a crease.
WRINKLE_STD_EPSILON = 2.0
WRINKLE_HIGHPASS_SIGMA_FRACTION = 0.012
WRINKLE_LOCAL_STD_SIGMA_FRACTION = 0.020
WRINKLE_COHERENCE_SIGMA_FRACTION = 0.004
WRINKLE_MORPH_RADIUS_PX = 2

# Logo / text auto-detection.
#
# **Why this is a pyramid and not a single-scale filter.** The single-scale form of this
# detector (E2-T2 round 1) found thin small type and missed the two shapes that matter most on
# real garments: a large solid wordmark and light type on dark fabric. QA measured 63 % of a
# 48 px stroke mask being invaded by the wrinkle mask, and 0 % detection for white-on-dark.
# The reason is geometric, not a tuning miss: at one scale a 48 px glyph is a *hollow ring* of
# boundary pixels around an undetected interior, and a light glyph has no ink chroma at all.
# Downsampling fixes both without loosening anything — **a large glyph at 1/4 resolution is
# small type**, which is the case the filter was already good at, and the score is computed from
# luminance *deviation* (polarity-free), so white-on-dark scores exactly like dark-on-white.
# `PROTECT_SCALES` is the pyramid; each scale uses the same relative sigmas, so no constant is
# scale-specific. The threshold stays at 0.30 — QA showed 0.40 starts eating knit texture, and
# the point of the pyramid is to gain recall *without* spending threshold.
PROTECT_SCALES: tuple[int, ...] = (1, 2, 4)
PROTECT_EDGE_SIGMA_FRACTION = 0.004
# Two contrast radii, and the score takes the **stronger** of the two at each pixel. The wide
# one is the "is this pixel part of a print" question; the narrow one is what keeps 1 px type
# alive — at a wide radius a hairline stroke is averaged into the fabric and its measured
# contrast collapses, which is why the round-1 filter scored 23 % recall on 11 px glyphs.
PROTECT_CONTRAST_SIGMA_FRACTION = 0.030
PROTECT_FINE_CONTRAST_SIGMA_FRACTION = 0.008
PROTECT_SCORE_MIN = 0.30
# Margin around every detected ink pixel. 4 px at flat-lay scale because a stroke's
# antialiased halo is 1–2 px wide on each side and the score's own localisation is fuzzy at the
# same order; a 2 px margin left 18 printed pixels of the 9 fixtures unprotected (measured),
# 4 px leaves 0. The cost is over-protection, which is the safe direction: the protection
# region covers 19 % of the garment on average across the fixtures (max 65 %, a tartan belt
# whose whole weave looks like print). That number is reported in the notes, not hidden.
PROTECT_DILATE_MIN_PX = 4
PROTECT_DILATE_FRACTION = 0.006

# Fill. The guide radius is large on purpose: it must be wider than a crease so the guide
# carries the *shading* of the garment and not the crease itself.
FILL_GUIDE_SIGMA_FRACTION = 0.030
FILL_STRENGTH = 0.85
FILL_FEATHER_SIGMA_PX = 2.0
FILL_SCALE_MIN = 0.80
FILL_SCALE_MAX = 1.25

_CLASSICAL_BACKEND = "local_shadow_attenuation_v1"
_PASS_THROUGH_BACKEND = "pass_through"
_SDXL_BACKEND = "sdxl_inpaint"

# --- the round-2 generative step: SDXL inpainting -------------------------------------------
#
# Model, revision and licence. The revision is a **commit sha**, not a branch: `main` moves,
# and "we ran whatever diffusers/stable-diffusion-xl-1.0-inpainting-0.1 pointed at on the day"
# is not a reproducible measurement. The sha and the per-file byte sizes below were read off
# the Hub on 2026-11-04 and are what `make fetch-sdxl` pins.
SDXL_MODEL_ID = "diffusers/stable-diffusion-xl-1.0-inpainting-0.1"
SDXL_REVISION = "115134f363124c53c7d878647567d04daf26e41e"
SDXL_DIRNAME = "sdxl-inpaint-0.1"
SDXL_VARIANT = "fp16"
# CreativeML Open RAIL++-M: commercial use **is** permitted, and the licence carries use
# restrictions that travel with the weights — no use for unlawful, defamatory, harassing or
# deliberately harmful content, and redistribution of the model or a derivative must pass the
# same restrictions on. The pipeline here only ever *runs* the weights locally; nothing is
# redistributed. Recorded in docs/DEPS.md, not just here.
SDXL_LICENCE = "CreativeML Open RAIL++-M"
# Exactly the fp16 variant plus the configs and tokenizers needed to load it. Deliberately not
# a directory glob: `allow_patterns=["unet/*"]` pulls the fp32 weights as well, which would
# double the download (6.9 GB -> 20 GB) to produce an identical result at more RAM.
SDXL_REQUIRED_FILES: dict[str, int] = {
    "model_index.json": 690,
    "scheduler/scheduler_config.json": 479,
    "text_encoder/config.json": 746,
    "text_encoder/model.fp16.safetensors": 246_144_867,
    "text_encoder_2/config.json": 758,
    "text_encoder_2/model.fp16.safetensors": 1_389_382_884,
    "tokenizer/merges.txt": 524_619,
    "tokenizer/special_tokens_map.json": 472,
    "tokenizer/tokenizer_config.json": 737,
    "tokenizer/vocab.json": 1_059_962,
    "tokenizer_2/merges.txt": 524_619,
    "tokenizer_2/special_tokens_map.json": 460,
    "tokenizer_2/tokenizer_config.json": 725,
    "tokenizer_2/vocab.json": 1_059_962,
    "unet/config.json": 1_932,
    "unet/diffusion_pytorch_model.fp16.safetensors": 5_135_178_560,
    "vae/config.json": 659,
    "vae/diffusion_pytorch_model.fp16.safetensors": 167_335_338,
}
SDXL_APPROX_BYTES = sum(SDXL_REQUIRED_FILES.values())  # 6.94 GB as measured, not "~10 GB"

# --- generation parameters, and why these numbers ------------------------------------------
#
# **Seed.** Fixed at module level so the stage is reproducible: same input, same seed, same
# bytes. `notes["seed"]` records it on every run, and `prettify_sdxl(seed=...)` overrides it.
SDXL_SEED = 20261104
# **Steps = 30.** SDXL's inpainting pipeline is distilled enough that 30 steps is the point
# where the masked region stops visibly changing on fabric: measured on the two fixtures at
# 20 / 30 / 50 steps, 20 shows blotchy low-frequency drift inside the mask and 50 is
# indistinguishable from 30 to the eye. 30 is the cheapest step count that is not visibly
# under-baked. The *effective* denoise steps are `strength × steps`, so at strength 0.85 that
# is ~26.
SDXL_STEPS = 30
# **Strength = 0.85.** This is the one parameter with a product-shaped argument. Low strength
# (≤ 0.5) keeps the original pixels and only nudges the shadow — it cannot remove a crease,
# which is the whole point of using a generative model. High strength (1.0) re-noises the
# masked region completely and starts inventing fabric *channels* that were never there. 0.85
# leaves enough of the original low frequencies to keep the fold's location credible while
# allowing the model to replace the fold's gradient. It is a compromise, not an optimum, and
# the handoff says so.
SDXL_STRENGTH = 0.85
# **Guidance 5.0** is SDXL's own default and is deliberately *not* raised. A higher guidance
# makes the model obey "no wrinkles" harder and drift further from the actual garment — the
# failure mode here is a garment that looks like a different garment, not one that still has a
# fold.
SDXL_GUIDANCE = 5.0
SDXL_PROMPT = (
    "a professional e-commerce product photo of a garment laid flat, pressed smooth fabric, "
    "no wrinkles, no creases, even soft studio lighting, sharp fabric texture, neutral colour"
)
SDXL_NEGATIVE_PROMPT = (
    "wrinkles, creases, folds, crumpled, shadow bands, blurry, out of focus, low quality, "
    "text, letters, logo, watermark, logo distortion, seams, extra fabric, warped pattern"
)
# **Working resolution.** SDXL was trained at 1024²; running it far below that produces
# duplicated hems and doubled seams, and the artefacts land *inside* the mask, which is the one
# place we cannot hide them. So the generative step works at the long edge below and the result
# is resampled back to the input canvas. 1024 is the quality default; `prettify_sdxl(side=...)`
# exists because measuring the cost curve is the only way to answer "does this fit a gate".
SDXL_WORK_SIDE = 1024
SDXL_WORK_SIDE_MIN = 512
# Feather width as a fraction of the short edge (min 3 px). See `_outward_feather` for why
# this is an outward ring ramp and not a Gaussian of the mask.
SDXL_FEATHER_FRACTION = 0.008
SDXL_FEATHER_MIN_PX = 3
# Multiple of 64: SDXL's VAE downsamples by 8 and the UNet's attention blocks by 64, and an
# off-by-8 canvas shows up as a tiling seam.
SDXL_SIZE_MULTIPLE = 64

WEIGHTS_DIR_ENV = "SNAPLIST_MODEL_DIR"
SDXL_DIR_ENV = "SNAPLIST_SDXL_DIR"
SDXL_FETCH_TARGET = "fetch-sdxl"
# The generative stack is a **PEP 735 dependency group**, not a main dependency: a plain
# `uv sync` / `make install` must not pull 0.72 GB for a mode nobody runs by default. Both
# names live here so the refusal can tell the operator exactly which command to run instead of
# discovering the requirement from an ImportError traceback 40 lines deep.
SDXL_DEP_GROUP = "sdxl"
SDXL_INSTALL_TARGET = "install-sdxl"
# Modules `mode=SDXL_INPAINT` needs at load time. Checked with `importlib.util.find_spec`, which
# reports presence **without importing** — the check runs during pytest collection and inside
# the refusal path, and neither may pay torch's 2 s import (or fail on a machine that could
# otherwise run the whole suite).
SDXL_DEP_MODULES: tuple[str, ...] = (
    "torch",
    "diffusers",
    "transformers",
    "accelerate",
    "safetensors",
    "huggingface_hub",
)

Box = tuple[int, int, int, int]  # x, y, width, height in flat-lay pixel coordinates

_HONEST_SCOPE = (
    "shadow/crease attenuation: local contrast inside the wrinkle mask is reduced by a "
    "low-frequency luminance guide. No crease is removed, no texture is synthesised, no "
    "pixel outside the feathered mask changes"
)


class PrettifyBackendUnavailable(StageNotImplemented):
    """A prettify mode this build cannot run.

    Subclasses `StageNotImplemented` on purpose (same reasoning as `segment.py`): "SDXL needs
    10 GB of weights and torch" is deterministic, so the worker must terminate the job
    immediately instead of retrying it three times and refunding it.
    """


@dataclass(slots=True)
class PrettifyResult:
    prettified_png: bytes
    meta: StageMeta


@dataclass(slots=True)
class WrinkleMaskAudit:
    """The wrinkle mask plus the numbers a reviewer has to be able to check.

    Returned by `wrinkle_mask_for()`, which is the same computation `prettify()` runs — the
    eval harness and the tests must audit **the mask that was actually used**, not a
    re-implementation of it that could drift.
    """

    final_mask: np.ndarray  # bool, H×W — what the fill consumed
    candidate_mask: np.ndarray  # bool, H×W — after cap, before morphology and exclusions
    protect_mask: np.ndarray  # bool, H×W — auto ⊎ caller boxes, dilated
    garment: np.ndarray  # bool, H×W — alpha >= 128
    interior: np.ndarray  # bool, H×W — garment minus the boundary band
    balanced: np.ndarray  # float H×W×3 — the white-balanced image the mask was built on
    gains: tuple[float, float, float]
    notes: dict


# ------------------------------------------------------------------------- weights/model


def sdxl_weights_dir() -> Path:
    """Where the SDXL inpainting weights are looked for. Never creates it, never downloads.

    Two overrides, checked in this order: `SNAPLIST_SDXL_DIR` (this model only, which is what
    a test or a second model directory wants) and `SNAPLIST_MODEL_DIR` (the shared base that
    `segment.py` already uses for `u2net.onnx`). Default:
    `~/.cache/snaplist/models/sdxl-inpaint-0.1/`.
    """
    direct = os.environ.get(SDXL_DIR_ENV, "").strip()
    if direct:
        return Path(direct).expanduser()
    override = os.environ.get(WEIGHTS_DIR_ENV, "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".cache/snaplist/models"
    return base / SDXL_DIRNAME


def sdxl_missing_files(root: Path | None = None) -> list[str]:
    """Which of the pinned files are absent or the wrong size. Empty list == a usable tree.

    Size, not a marker file: the byte counts are pinned per file at a pinned revision, so a
    truncated download or a different model fails here instead of loading into a UNet that
    quietly produces worse pixels. A `.complete` sentinel would also have to be trusted, and
    a sentinel is the easiest thing in the world to copy next to the wrong files.
    """
    directory = root if root is not None else sdxl_weights_dir()
    problems: list[str] = []
    for relative, expected in SDXL_REQUIRED_FILES.items():
        candidate = directory / relative
        try:
            actual = candidate.stat().st_size
        except OSError:
            problems.append(f"{relative} (missing)")
            continue
        if actual != expected:
            problems.append(f"{relative} ({actual} bytes, expected {expected})")
    return problems


def sdxl_weights_available() -> bool:
    """True only when the full pinned tree is on disk. No subprocess, no import, no network."""
    return not sdxl_missing_files()


def sdxl_missing_deps() -> list[str]:
    """Which generative-stack modules are not installed. `find_spec`, so nothing is imported.

    Two reasons this must not `import torch`: it is called from `pytest`'s collection phase via
    the test skip marker (importing torch there would add ~2 s per session and would defeat the
    point of keeping the stack optional), and it is called from the refusal path, which exists
    precisely for environments that do not have the stack.
    """
    missing: list[str] = []
    for module in SDXL_DEP_MODULES:
        try:
            present = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError, AttributeError):  # pragma: no cover - broken install
            present = False
        if not present:
            missing.append(module)
    return missing


def sdxl_backend_available() -> tuple[bool, str]:
    """`(runnable, why-not)` for `mode=SDXL_INPAINT`. No imports, no model load, no network.

    Two independent things have to be true and they fail for different reasons with different
    fixes, so they are reported separately: the `sdxl` **dependency group** (0.72 GB of
    site-packages) and the pinned **weights** (6.9 GB). Telling an operator "run make
    fetch-sdxl" when what is missing is torch would send them to download 6.9 GB they already
    have — the refusal has to name the actual gap.
    """
    missing_deps = sdxl_missing_deps()
    missing_files = sdxl_missing_files()
    if not missing_deps and not missing_files:
        return True, ""
    parts: list[str] = []
    if missing_deps:
        parts.append(
            f"the `{SDXL_DEP_GROUP}` dependency group is not installed (missing: "
            f"{', '.join(missing_deps)}) — run `make {SDXL_INSTALL_TARGET}`"
        )
    if missing_files:
        parts.append(
            f"{len(missing_files)} of {len(SDXL_REQUIRED_FILES)} pinned weight files are "
            f"absent or the wrong size in {sdxl_weights_dir()} — run `make "
            f"{SDXL_FETCH_TARGET}` (or `make {SDXL_INSTALL_TARGET}`, which does both)"
        )
    return False, "; ".join(parts)


def fetch_hint() -> str:
    """The sentence a missing-weights refusal carries, so the fix is inside the error."""
    size_gb = SDXL_APPROX_BYTES / 1e9
    return (
        f"run `make {SDXL_FETCH_TARGET}` (fetches {SDXL_MODEL_ID} at revision "
        f"{SDXL_REVISION[:12]}…, {size_gb:.1f} GB, licence {SDXL_LICENCE}, into "
        f"{sdxl_weights_dir()})"
    )


def _fix_sentence() -> str:
    """The **one** command that closes the actual gap. Chosen from the gap, never guessed.

    A refusal that names the wrong command is worse than no refusal: the operator runs it,
    it succeeds, they re-run the stage, and they land on the same error with no new
    information. That is exactly what happened when this sentence unconditionally said "run
    `make fetch-sdxl`" — on a machine whose 18 weight files were all present and whose only
    gap was the optional `sdxl` dependency group, `fetch_sdxl_weights()` is idempotent, so it
    printed "already present and verified" and exited 0 having fixed nothing.

    Three cases, three sentences:
    * **both missing** — `make install-sdxl`, which does both, and say why the fetch target
      alone is not enough;
    * **only the group** — `make install-sdxl` again (same command, different reason: nothing
      will be downloaded because the fetcher verifies instead of re-downloading), and
      deliberately **no mention of `make fetch-sdxl`** as a fix, because it is not one;
    * **only the weights** — `fetch_hint()`, which is what it was written for.
    """
    missing_deps = sdxl_missing_deps()
    missing_files = sdxl_missing_files()
    if missing_deps and missing_files:
        return (
            f"run `make {SDXL_INSTALL_TARGET}` — it installs the `{SDXL_DEP_GROUP}` "
            "dependency group and then fetches the weights; "
            f"`make {SDXL_FETCH_TARGET}` on its own would leave the group missing and the "
            "same error would come back"
        )
    if missing_deps:
        return (
            f"run `make {SDXL_INSTALL_TARGET}` — it installs the `{SDXL_DEP_GROUP}` "
            "dependency group (0.72 GB of site-packages). The pinned weights are already on "
            "disk and verified, so the fetch step inside it downloads nothing"
        )
    return fetch_hint()


def _sdxl_refusal(detail: str = "") -> str:
    """The full refusal text: what is missing, how big, where it goes, and what to run."""
    size_gb = SDXL_APPROX_BYTES / 1e9
    runnable, why_not = sdxl_backend_available()
    missing = sdxl_missing_files()
    state = why_not if not runnable else "everything is present but the model failed to load"
    return (
        f"mode=SDXL_INPAINT (docs/PLAN.md ticket E2-T2) cannot run in this environment: "
        f"{state}. The model is {SDXL_MODEL_ID} at revision {SDXL_REVISION} "
        f"({SDXL_VARIANT} variant), {size_gb:.1f} GB, licence {SDXL_LICENCE} "
        "(commercial use allowed; use restrictions travel with the weights — see "
        f"docs/DEPS.md). Fix: {_fix_sentence()}. "
        "The base install deliberately excludes this stack: `mode=CLASSICAL` and every other "
        "stage work without it"
        + (f". Missing or wrong: {', '.join(missing[:4])}" + ("…" if len(missing) > 4 else "")
           if missing else "")
        + (f". Loader said: {detail}" if detail else "")
        + ". This stage never downloads or installs anything itself: the pipeline contract is "
        "zero network calls, and a stage that fetches 7 GB on first call turns a missing file "
        "into a ten-minute stall inside a job"
    )


def _require_sdxl_backend() -> Path:
    """The weights directory, or `PrettifyBackendUnavailable` naming the actual gap."""
    runnable, _why_not = sdxl_backend_available()
    if not runnable:
        raise PrettifyBackendUnavailable(_sdxl_refusal())
    return sdxl_weights_dir()


def fetch_sdxl_weights(*, force: bool = False, verbose: bool = True) -> Path:
    """Download the pinned fp16 tree into `sdxl_weights_dir()`. **Only** called by `make`.

    Never reachable from `prettify()`: the download is an explicit operator act, exactly like
    `make fetch-models` for the 176 MB u2net file. Same shape as that target, one level up:

    * download into a sibling `<dir>.partial`, never into the live directory, so a killed
      download cannot leave a half-populated tree where a loader will find it;
    * verify every file's byte count against `SDXL_REQUIRED_FILES` before promoting;
    * on any failure, delete the partial tree and re-raise — a half download is worthless and
      keeping it only makes the next attempt look like it made progress.

    Returns the live directory. Idempotent: a verified tree is left alone unless `force`.
    """
    try:
        # Lazy, and not only for the run-time path: this module must stay importable without
        # the sdxl group, so the Hub client is only reached from `make fetch-sdxl`.
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            f"huggingface_hub is not installed — it lives in the `{SDXL_DEP_GROUP}` "
            f"dependency group, which the base `make install` deliberately excludes. Run "
            f"`uv sync --group {SDXL_DEP_GROUP}` (or `make {SDXL_INSTALL_TARGET}`, which does "
            "that and then fetches the weights)"
        ) from exc

    target = sdxl_weights_dir()
    if not force and sdxl_weights_available():
        if verbose:
            print(f"SDXL weights already present and verified: {target}")
        return target

    partial = target.with_name(target.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.parent.mkdir(parents=True, exist_ok=True)
    if verbose:
        print(
            f"downloading {SDXL_MODEL_ID} @ {SDXL_REVISION[:12]} ({SDXL_VARIANT}) -> "
            f"{target} ({SDXL_APPROX_BYTES / 1e9:.1f} GB, once)"
        )
    try:
        snapshot_download(
            repo_id=SDXL_MODEL_ID,
            revision=SDXL_REVISION,
            allow_patterns=sorted(SDXL_REQUIRED_FILES),
            local_dir=str(partial),
            max_workers=4,
        )
        problems = sdxl_missing_files(partial)
        if problems:
            raise RuntimeError(
                "download finished but the tree does not match the pin: "
                + ", ".join(problems[:6])
            )
        # Promote only a complete tree. `os.replace` is atomic within a filesystem, so a
        # reader either sees the old directory or the new one, never a mixture.
        if target.exists():
            shutil.rmtree(target)
        os.replace(partial, target)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if verbose:
        print(f"verified {len(SDXL_REQUIRED_FILES)} files, {SDXL_APPROX_BYTES} bytes: {target}")
    return target


# ---------------------------------------------------------------------------- blur helpers
# Box-approximated Gaussians (three box passes) on float64. Deliberately not scipy: the
# project ships numpy + Pillow only (docs/DEPS.md), and an integral-image box blur is exact,
# deterministic and fast enough at flat-lay resolution. The sigma handed in is *nominal* —
# three passes of radius r have an effective sigma of roughly 0.85·r·√3, and every note that
# records a sigma says "nominal".


def _box_blur(array: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """Mean over a (2r+1) window along `axis`, edges replicated."""
    if radius <= 0:
        return array.astype(np.float64, copy=True)
    pad = [(0, 0), (0, 0)]
    pad[axis] = (radius, radius)
    padded = np.pad(array.astype(np.float64, copy=False), pad, mode="edge")
    cumulative = np.cumsum(padded, axis=axis)
    zero_shape = list(cumulative.shape)
    zero_shape[axis] = 1
    cumulative = np.concatenate([np.zeros(zero_shape, dtype=np.float64), cumulative], axis=axis)
    length = array.shape[axis]
    upper = np.take(cumulative, np.arange(length) + 2 * radius + 1, axis=axis)
    lower = np.take(cumulative, np.arange(length), axis=axis)
    return (upper - lower) / float(2 * radius + 1)


def _blur(array: np.ndarray, sigma: float) -> np.ndarray:
    """Three box passes ≈ Gaussian of nominal `sigma`. Deterministic, no scipy."""
    radius = max(1, int(round(float(sigma) * 0.85)))
    out = array.astype(np.float64, copy=True)
    for _ in range(3):
        out = _box_blur(out, radius, 0)
        out = _box_blur(out, radius, 1)
    return out


def _binary_dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Exact Chebyshev dilation (MaxFilter). Geometry, not numerics — it cannot leak."""
    if radius <= 0:
        return mask.copy()
    image = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8))
    return np.asarray(image.filter(ImageFilter.MaxFilter(2 * radius + 1))) > 0


def _binary_erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """Exact Chebyshev erosion (MinFilter)."""
    if radius <= 0:
        return mask.copy()
    image = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8))
    return np.asarray(image.filter(ImageFilter.MinFilter(2 * radius + 1))) > 0


# ------------------------------------------------------------------------------- decoding


def _decode_rgb(image_bytes: bytes, what: str) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            frame = opened.load()  # force the decode: a truncated file fails here
            del frame
            return np.asarray(opened.convert("RGB"), dtype=np.float64)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - PIL raises a zoo of formats for bad bytes
        raise ValueError(f"prettify(): could not decode {what} bytes ({exc})") from exc


def _decode_l(png_bytes: bytes, what: str) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(png_bytes)) as opened:
            frame = opened.load()
            del frame
            if opened.mode in ("RGBA", "LA", "PA"):
                channel = opened.getchannel("A")
            else:
                channel = opened.convert("L")
            return np.asarray(channel, dtype=np.uint8)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"prettify(): could not decode {what} bytes ({exc})") from exc


def _encode_rgb(pixels: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec. 601 luma on 0..255. Only used as a shape signal, not for colour maths."""
    return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]


def _rect_px(fraction: float, height: int, width: int, minimum: int) -> int:
    return max(minimum, int(round(fraction * min(height, width))))


# -------------------------------------------------------------------------- white balance


def _white_balance(
    rgb: np.ndarray, garment: np.ndarray
) -> tuple[np.ndarray, tuple[float, float, float], dict]:
    """Per-channel gain that puts the backdrop ring on `#F2F1EE`. Gains only, never offsets.

    The sample is the **median** of the ring, not its mean: a flat-lay backdrop usually has a
    shadow on one side, and a mean would read the shadow as a colour cast and correct for a
    gradient that is not a cast. Median plus a bounded gain is what makes this "correct the
    illuminant" instead of "flatten the lighting" (even lighting is a different ticket).

    Multiplicative only: an additive offset would raise blacks and grey the garment out, and
    it is exactly the operation that "washes the colour off" — the DoD's prohibition.
    """
    height, width = garment.shape
    ring_px = _rect_px(BALANCE_RING_FRACTION, height, width, BALANCE_RING_MIN_PX)
    ring = ~_binary_dilate(garment, ring_px)
    sample = rgb[ring]
    if sample.shape[0] < BALANCE_MIN_BACKGROUND_PX:
        note = {
            "skipped": True,
            "reason": (
                f"only {int(sample.shape[0])} backdrop pixels outside a {ring_px}px ring "
                f"(< {BALANCE_MIN_BACKGROUND_PX}); no reliable cast estimate, gains left at 1.0"
            ),
            "ring_px": ring_px,
        }
        return rgb.copy(), (1.0, 1.0, 1.0), note

    median = np.median(sample, axis=0)
    target = np.array(BACKDROP_RGB, dtype=np.float64)
    # A channel median of 0 would divide by zero; guarded rather than clipped afterwards so
    # the note can report the real sample instead of a manufactured 1.0.
    safe = np.maximum(median, 1.0)
    raw = target / safe
    gains = np.clip(raw, BALANCE_GAIN_MIN, BALANCE_GAIN_MAX)
    balanced = np.clip(rgb * gains, 0.0, 255.0)
    after = np.median(balanced[ring], axis=0)
    note = {
        "skipped": False,
        "ring_px": ring_px,
        "background_px": int(sample.shape[0]),
        "background_before": [round(float(value), 2) for value in median],
        "background_after": [round(float(value), 2) for value in after],
        "clamped": bool(np.any(np.abs(raw - gains) > 1e-9)),
    }
    return balanced, (float(gains[0]), float(gains[1]), float(gains[2])), note


# ------------------------------------------------------------------------- wrinkle mask


def _coherence(lum: np.ndarray, sigma: float) -> np.ndarray:
    """Structure-tensor coherence in [0, 1]: 1 = a line, 0 = isotropic texture.

    A crease is a *line*; a crease detector that ignores direction fires on knit texture and
    on print noise, which is how a wrinkle mask ends up covering half the garment.
    """
    grad_y, grad_x = np.gradient(lum)
    jxx = _blur(grad_x * grad_x, sigma)
    jyy = _blur(grad_y * grad_y, sigma)
    jxy = _blur(grad_x * grad_y, sigma)
    trace = jxx + jyy
    difference = np.sqrt((jxx - jyy) ** 2 + 4.0 * jxy * jxy)
    return np.where(trace > 1e-9, difference / np.maximum(trace, 1e-9), 0.0)


def _wrinkle_scores(lum: np.ndarray, height: int, width: int) -> np.ndarray:
    """How much each pixel looks like a shadow band: dark, locally contrasty, directional."""
    sigma_hp = _rect_px(WRINKLE_HIGHPASS_SIGMA_FRACTION, height, width, 2)
    sigma_std = _rect_px(WRINKLE_LOCAL_STD_SIGMA_FRACTION, height, width, 3)
    sigma_tensor = _rect_px(WRINKLE_COHERENCE_SIGMA_FRACTION, height, width, 1)

    high_pass = lum - _blur(lum, sigma_hp)
    dark = np.maximum(-high_pass, 0.0)  # a crease is darker than its surroundings
    local_mean = _blur(lum, sigma_std)
    local_variance = np.maximum(_blur(lum * lum, sigma_std) - local_mean * local_mean, 0.0)
    local_std = np.sqrt(local_variance)
    standardised = dark / (local_std + WRINKLE_STD_EPSILON)
    # Soft, not a gate: directionality scales the score instead of vetoing it, because a
    # hard veto on `coherence` empties the mask on soft jersey, where creases are genuinely
    # low-contrast and only mildly linear.
    return standardised * (0.5 + 0.5 * _coherence(lum, sigma_tensor))


def _cap_threshold(scores: np.ndarray, pool: np.ndarray, cap: float) -> float:
    """The threshold that keeps at most `cap` of `pool` above it.

    This is the coverage rail, expressed as a threshold instead of as a truncation: the mask
    stays a predicate on the score, so raising the cap really does let more pixels through
    and lowering it really does remove the weakest ones. Returns `inf` when the pool is empty
    or the cap allows nothing, which yields an empty mask — the honest answer for an empty
    garment.
    """
    count = int(pool.sum())
    if count == 0:
        return math.inf
    allowed = int(math.floor(cap * count))
    if allowed <= 0:
        return math.inf
    values = np.sort(scores[pool])[::-1]
    return float(values[min(allowed, values.size) - 1])


def _boxes_to_mask(boxes: Sequence[Box], height: int, width: int) -> np.ndarray:
    """Caller-supplied rectangles -> one boolean region. Out-of-frame parts are clipped."""
    mask = np.zeros((height, width), dtype=bool)
    for box in boxes:
        if len(tuple(box)) != 4:
            raise ValueError(f"prettify(): protect box {box!r} needs four integers (x, y, w, h)")
        x, y, box_w, box_h = (int(value) for value in box)
        if box_w <= 0 or box_h <= 0:
            raise ValueError(f"prettify(): protect box {box!r} has non-positive extent")
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + box_w, width), min(y + box_h, height)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return mask


def _downsample_mean(array: np.ndarray, factor: int) -> np.ndarray:
    """Mean-pool by an integer factor, cropping the remainder.

    Mean pooling (not subsampling) so a thin glyph cannot fall between samples and vanish —
    which would turn the pyramid into a coin flip. A step edge survives pooling at full
    amplitude (it becomes a one-pixel ramp), so the score's contrast term is scale-stable.
    """
    if factor <= 1:
        return array.astype(np.float64, copy=True)
    height, width = array.shape[:2]
    cropped_h = (height // factor) * factor
    cropped_w = (width // factor) * factor
    cropped = array[:cropped_h, :cropped_w]
    if array.ndim == 3:
        pooled = cropped.reshape(cropped_h // factor, factor, cropped_w // factor, factor, -1)
        return pooled.mean(axis=(1, 3))
    pooled = cropped.reshape(cropped_h // factor, factor, cropped_w // factor, factor)
    return pooled.mean(axis=(1, 3))


def _upsample_nearest(mask: np.ndarray, height: int, width: int, factor: int) -> np.ndarray:
    """Nearest-neighbour expansion back to the full canvas, zero-padded on the remainder."""
    expanded = mask if factor <= 1 else np.repeat(np.repeat(mask, factor, axis=0), factor, axis=1)
    out = np.zeros((height, width), dtype=bool)
    out[: expanded.shape[0], : expanded.shape[1]] = expanded
    return out


def _protect_score(
    lum: np.ndarray, chroma: np.ndarray, *, height: int, width: int
) -> np.ndarray:
    """One scale of the ink score: edges + local contrast, boosted by ink chroma.

    Polarity-free by construction (`abs` deviation from the local mean), which is what makes
    white type on dark fabric score like dark type on white — the round-1 form compared
    *darkness*, and a white glyph is not darker than anything.
    """
    sigma_edge = _rect_px(PROTECT_EDGE_SIGMA_FRACTION, height, width, 1)
    sigma_wide = _rect_px(PROTECT_CONTRAST_SIGMA_FRACTION, height, width, 2)
    sigma_fine = _rect_px(PROTECT_FINE_CONTRAST_SIGMA_FRACTION, height, width, 1)
    grad_y, grad_x = np.gradient(lum)
    edge_density = _blur(np.hypot(grad_x, grad_y), sigma_edge) / 255.0
    # Strongest of two contrast radii: the wide one asks "is this part of a print", the narrow
    # one keeps a 1 px stroke from being averaged away before it can be measured.
    contrast_wide = np.abs(lum - _blur(lum, sigma_wide)) / 255.0
    contrast_fine = np.abs(lum - _blur(lum, sigma_fine)) / 255.0
    local_contrast = np.maximum(contrast_wide, contrast_fine)
    # Square root on the edge term: type is thin, so its *mean* gradient over a small window is
    # small even when the edge itself is hard. A linear weight misses small type.
    score = np.sqrt(np.maximum(edge_density, 0.0)) * np.clip(local_contrast * 4.0, 0.0, 1.0)
    return score * (0.6 + 0.4 * np.clip(chroma * 3.0, 0.0, 1.0))


def _auto_protect_mask(rgb: np.ndarray, garment: np.ndarray, *, height: int, width: int) -> tuple[
    np.ndarray, dict
]:
    """Print / logo / text pixels inside the garment silhouette, over a resolution pyramid.

    Nothing here reads a font, and nothing here is trained: it is a hand-tuned detector, and
    `evals/fixtures/prettify/README.md` records exactly what it was tuned on. The three
    properties it keys on are edge density, local luminance contrast (either polarity) and ink
    chroma; the pyramid is what makes those properties scale-free, so a 48 px wordmark and 8 px
    type are the same problem.

    Dilation is per scale and grows with the scale factor, because a nearest-upsampled reduced
    pixel is already a f×f block and its boundary sits up to f−1 px inside the true edge.
    """
    lum = _luminance(rgb)
    chroma = (rgb.max(axis=2) - rgb.min(axis=2)) / 255.0
    base_radius = _rect_px(PROTECT_DILATE_FRACTION, height, width, PROTECT_DILATE_MIN_PX)

    region = np.zeros((height, width), dtype=bool)
    raw_by_scale: dict[str, int] = {}
    for factor in PROTECT_SCALES:
        scale_lum = lum if factor == 1 else _downsample_mean(lum, factor)
        scale_chroma = chroma if factor == 1 else _downsample_mean(chroma, factor)
        scale_h, scale_w = scale_lum.shape
        if min(scale_h, scale_w) < 8:
            continue
        score = _protect_score(scale_lum, scale_chroma, height=scale_h, width=scale_w)
        raw = score > PROTECT_SCORE_MIN
        raw_by_scale[str(factor)] = int(raw.sum())
        if not raw.any():
            continue
        expanded = _upsample_nearest(raw, height, width, factor) & garment
        region |= _binary_dilate(expanded, base_radius + factor - 1)

    note = {
        "auto_score_threshold": PROTECT_SCORE_MIN,
        "auto_scales": list(PROTECT_SCALES),
        "auto_px": raw_by_scale.get("1", 0),
        "auto_px_by_scale": raw_by_scale,
        "auto_region_px": int((region & garment).sum()),
        "auto_dilate_px": base_radius,
    }
    return region, note


def _build_wrinkle_mask(
    rgb: np.ndarray,
    alpha: np.ndarray,
    *,
    protect: Sequence[Box] | None,
) -> WrinkleMaskAudit:
    """The wrinkle mask, in the order the constraints require.

    1. mask the garment, then erode a band off its outline -> `interior`;
    2. score every interior pixel for "dark, contrasty, directional";
    3. threshold, with the coverage cap allowed to raise the threshold;
    4. morphology (closing then opening) — cleaning, not growing;
    5. **last**, subtract the protection region (auto ⊎ caller boxes) and re-intersect with
       `interior`. This ordering is the DoD: *the inpaint mask never overlaps the logo/text
       region*. Subtracting before the morphology would let the closing step creep back over
       a 3 px-wide stroke.
    """
    height, width = alpha.shape
    garment = alpha >= 128
    if not garment.any():
        raise ValueError("prettify(): alpha contains no garment pixels (no_garment)")

    band_px = _rect_px(WRINKLE_BOUNDARY_FRACTION, height, width, WRINKLE_BOUNDARY_MIN_PX)
    interior = _binary_erode(garment, band_px)

    balanced, gains, balance_note = _white_balance(rgb, garment)
    lum = _luminance(balanced)
    scores = _wrinkle_scores(lum, height, width)

    cap_threshold = _cap_threshold(scores, interior, WRINKLE_COVERAGE_CAP)
    threshold = max(WRINKLE_SCORE_MIN, cap_threshold)
    candidate = interior & (scores > threshold)
    # What the score floor alone would have selected. Reported so the rail is observable: with
    # only `candidate_coverage` in the notes, "the cap never binds" and "the cap is absent"
    # look identical, and the difference is the whole point of having a cap.
    natural = interior & (scores > WRINKLE_SCORE_MIN)

    morph_radius = WRINKLE_MORPH_RADIUS_PX
    cleaned = _binary_erode(_binary_dilate(candidate, morph_radius), morph_radius)
    # Morphology may widen the mask past the cap on ragged scores; re-applying the threshold
    # removes whatever the closing added that the score never justified.
    cleaned = cleaned & (scores > threshold) & interior

    auto_region, protect_note = _auto_protect_mask(
        balanced, garment, height=height, width=width
    )
    caller_region = _boxes_to_mask(protect or (), height, width)
    protect_region = auto_region | caller_region

    final = cleaned & interior & ~protect_region
    prototype_overlap = int((cleaned & protect_region).sum())

    interior_px = int(interior.sum())
    coverage = float(final.sum()) / interior_px if interior_px else 0.0
    candidate_coverage = float(candidate.sum()) / interior_px if interior_px else 0.0
    natural_coverage = float(natural.sum()) / interior_px if interior_px else 0.0
    notes = {
        "white_balance": [round(gains[0], 4), round(gains[1], 4), round(gains[2], 4)],
        "white_balance_detail": balance_note,
        "wrinkle_mask": {
            "coverage": round(coverage, 4),
            "candidate_coverage": round(candidate_coverage, 4),
            "natural_coverage": round(natural_coverage, 4),
            "coverage_cap": WRINKLE_COVERAGE_CAP,
            "cap_bound": bool(natural_coverage > WRINKLE_COVERAGE_CAP),
            "threshold": round(threshold, 4),
            "score_floor": WRINKLE_SCORE_MIN,
            "boundary_band_px": band_px,
            "erode_radius_px": band_px,
            "morph_radius_px": morph_radius,
            "interior_px": interior_px,
            "mask_px": int(final.sum()),
        },
        "protect": {
            **protect_note,
            "caller_boxes": [list(box) for box in (protect or ())],
            # Only list a source that actually produced pixels. The round-1 form appended
            # "auto" unconditionally, so a silent detector failure still reported
            # `['auto', 'caller']` — the audit said the detector ran when it had found nothing.
            "sources": (["auto"] if auto_region.any() else [])
            + (["caller"] if caller_region.any() else []),
            "region_px": int(protect_region.sum()),
            "pixels_removed_by_protection": prototype_overlap,
            "final_mask_overlap_px": int((final & protect_region).sum()),
        },
    }
    return WrinkleMaskAudit(
        final_mask=final,
        candidate_mask=candidate,
        protect_mask=protect_region,
        garment=garment,
        interior=interior,
        balanced=balanced,
        gains=gains,
        notes=notes,
    )


def wrinkle_mask_for(
    flat_png: bytes,
    alpha_png: bytes,
    *,
    protect: Sequence[Box] | None = None,
) -> WrinkleMaskAudit:
    """The mask `prettify()` would use for this pair, with its audit numbers.

    Public because the DoD's "the inpaint mask never overlaps the logo/text region" is a
    claim about the *mask*, and a test or an eval that recomputed the mask itself would be
    auditing a copy. This is the one implementation.
    """
    if not flat_png:
        raise ValueError("prettify(): flat_png bytes are empty")
    if not alpha_png:
        raise ValueError("prettify(): alpha_png bytes are empty")
    rgb = _decode_rgb(flat_png, "flat_png")
    alpha = _decode_l(alpha_png, "alpha_png")
    if alpha.shape != rgb.shape[:2]:
        raise ValueError(
            f"prettify(): alpha shape {alpha.shape} does not match flat shape {rgb.shape[:2]}"
            "; rectified flat-lay and alpha must be the same canvas (rectify does not return "
            "the warped alpha — resize it to the flat-lay before calling prettify)"
        )
    return _build_wrinkle_mask(rgb, alpha, protect=protect)


# ---------------------------------------------------------------------------------- fill


def _attenuate(
    rgb: np.ndarray, mask: np.ndarray, *, height: int, width: int
) -> tuple[np.ndarray, dict]:
    """Reduce local luminance contrast inside `mask`. Deterministic; no texture, no model.

    `guide = blur(luminance)` is the garment's shading without its creases. Where the guide is
    brighter than the pixel, the pixel is a shadow and is lifted towards the guide; where it
    is darker, a highlight is pulled down. The correction is applied as a *ratio* on all
    three channels, so hue and saturation are carried through untouched — that is the
    "preserve chroma" requirement, and a per-channel offset would fail it.

    The mask is feathered with a Gaussian before use: a hard mask edge in a luminance-domain
    correction leaves a visible step, which reads as "AI-edited" more strongly than the crease
    did.
    """
    lum = _luminance(rgb)
    sigma_guide = _rect_px(FILL_GUIDE_SIGMA_FRACTION, height, width, 4)
    guide = _blur(lum, sigma_guide)
    delta = guide - lum
    weight = _blur(mask.astype(np.float64), FILL_FEATHER_SIGMA_PX)
    weight = np.clip(weight, 0.0, 1.0)

    # Guard the divisor: a near-black garment pixel would otherwise be multiplied by a huge
    # ratio. 8/255 is below any garment's specular floor and above pure noise.
    scale = 1.0 + weight * FILL_STRENGTH * delta / np.maximum(lum, 8.0)
    scale = np.clip(scale, FILL_SCALE_MIN, FILL_SCALE_MAX)
    out = np.clip(rgb * scale[:, :, None], 0.0, 255.0)
    changed = int(np.any(np.abs(out - rgb) > 0.5, axis=2).sum())
    note = {
        "method": "low_frequency_luminance_transfer",
        "guide_sigma_px_nominal": sigma_guide,
        "strength": FILL_STRENGTH,
        "feather_sigma_px_nominal": FILL_FEATHER_SIGMA_PX,
        "scale_range": [FILL_SCALE_MIN, FILL_SCALE_MAX],
        "pixels_changed": changed,
        "chroma_handling": "single per-pixel ratio applied to R, G and B (hue preserved)",
        "texture_synthesis": False,
    }
    return out, note


# ------------------------------------------------------------------- the generative step
#
# Everything below this line is the round-2 SDXL inpainting path. It is imported lazily, inside
# the branch that needs it, and that is a hard rule rather than tidiness: torch is 581 MB of
# site-packages, and a module-level `import torch` here would make `make test` — and the API
# process — fail to start on any machine that has not pulled the round-2 extras. The cheapest
# backend (`u2net` through onnxruntime) deliberately does not need torch, and nothing in this
# file may take that away.

_SDXL_HONEST_SCOPE = (
    "generative inpaint of the wrinkle mask only: inside the feathered mask, SDXL inpainting "
    "synthesises fabric texture. Pixels inside the logo/text protection region, inside the "
    "boundary band, or outside the feathered mask are not generated — they are the "
    "white-balanced input. Because it synthesises texture, this mode CAN invent plausible "
    "detail that was not in the photograph, which is exactly why the logo protection is a "
    "hard subtraction and not a hint. MEASURED ON THE FIXTURES, THAT IS WHAT IT DOES: the "
    "folds return as thin hard scratch lines, so this mode is implemented and honest but not "
    "yet a product-quality de-wrinkler — see the known-failure paragraph in prettify_sdxl()"
)


def _sdxl_device_and_dtype() -> tuple[str, str]:
    """`(device, dtype-name)` for the generative step. Imported torch is local to this call.

    MPS when available, CPU otherwise. `float16` on MPS because the weights are shipped fp16
    and a fp32 copy would double 7 GB of RAM for nothing measurable; `float32` on CPU because
    Apple's CPU kernels for fp16 are slower than fp32 and MPS-only ops make fp16 CPU paths
    fragile.
    """
    import torch

    if torch.backends.mps.is_available():
        return "mps", "float16"
    return "cpu", "float32"


@lru_cache(maxsize=1)
def _load_sdxl_pipeline(weights_dir: str, device: str, dtype_name: str) -> object:
    """Load the pipeline once per process. `local_files_only=True` — **never** a download.

    Cached because the alternative makes the second call in a worker pay 7 GB of disk read
    again, and because a "warm" latency number is only meaningful if the model is warm. The
    cache key includes device and dtype so a test can force CPU without poisoning the MPS
    entry.
    """
    import torch
    from diffusers import StableDiffusionXLInpaintPipeline

    dtype = getattr(torch, dtype_name)
    pipe = StableDiffusionXLInpaintPipeline.from_pretrained(
        weights_dir,
        dtype=dtype,
        variant=SDXL_VARIANT,
        use_safetensors=True,
        local_files_only=True,  # the whole point: a missing file raises, it does not fetch
    )
    pipe.set_progress_bar_config(disable=True)
    if device == "mps":
        # 24 GB of unified memory is shared with the OS. Attention slicing trades a little
        # speed for a peak activation cost that fits; it is a memory decision, recorded in
        # `notes["generative"]["attention_slicing"]` so nobody has to guess why it is on.
        pipe.enable_attention_slicing()
    # `pipe.enable_vae_slicing()` was removed from the pipeline class in diffusers 0.42 (it
    # forwards nothing now); the method lives on the VAE. Guarded rather than version-sniffed,
    # because the point is "slice when the VAE can", not "diffusers is at least version X".
    if hasattr(pipe.vae, "enable_slicing"):
        pipe.vae.enable_slicing()
    pipe = pipe.to(device)
    pipe.unet.eval()
    pipe.vae.eval()
    return pipe


def _make_generator(torch_module: object, device: str, seed: int) -> object:
    """A seeded generator, on the device when the backend supports one.

    `torch.Generator(device="mps")` is not available on every torch build, and the CPU
    generator is what diffusers' `randn_tensor` moves across anyway; so the fallback is not a
    degradation, it is the portable path.
    """
    torch = torch_module
    try:
        return torch.Generator(device=device).manual_seed(seed)
    except (RuntimeError, TypeError):  # pragma: no cover - depends on the torch build
        return torch.Generator(device="cpu").manual_seed(seed)


def _sdxl_work_size(height: int, width: int, side: int) -> tuple[int, int]:
    """`(work_height, work_width)` — aspect preserved, both sides multiples of 64.

    SDXL is trained at 1024² and duplicates hems and seams well below ~512, so the long edge is
    put at `side` (default `SDXL_WORK_SIDE`) and the short edge follows the aspect. Never zero,
    because a degenerate canvas is a crash inside the UNet rather than an error message.
    """
    multiple = SDXL_SIZE_MULTIPLE
    long_edge = max(height, width)
    scale = float(side) / float(long_edge)
    work_h = max(multiple, int(round(height * scale / multiple)) * multiple)
    work_w = max(multiple, int(round(width * scale / multiple)) * multiple)
    return work_h, work_w


def _outward_feather(mask: np.ndarray, radius: int) -> np.ndarray:
    """1.0 on `mask`, a raised-cosine ramp to 0.0 over `radius` px *outside* it, 0 further out.

    Why not a Gaussian blur of the mask, which is what `_attenuate` uses: the wrinkle mask is a
    network of **thin lines**. Blurring a 2 px-wide line with sigma ≈ 2 drops its centre value
    below 0.5, so a blurred-mask alpha repaints almost nothing — measured on
    `crease-01-breeches-leg`, `weight > 0.5` covered **22** pixels of a 4412 px mask. The
    classical fill never noticed because a weight of 0.3 still attenuates a shadow; a
    generative step needs the *core* of the mask to be fully regenerated.

    So the ramp is built outward, by exact Chebyshev rings (`_binary_dilate`), with a raised
    cosine profile: full strength inside, `0.5·(1+cos(π·d/(R+1)))` at ring `d`, zero at
    `R+1`. Deterministic (integer geometry, no float convolution), it cannot leak past
    `radius`, and the value decays monotonically — which is the "repaint strength decays with
    the feather" requirement, expressed as the thing itself rather than as a hope.
    """
    weight = mask.astype(np.float64)
    if radius <= 0:
        return weight
    previous = mask
    for ring in range(1, int(radius) + 1):
        grown = _binary_dilate(mask, ring)
        value = 0.5 * (1.0 + math.cos(math.pi * ring / (radius + 1.0)))
        weight[grown & ~previous] = value
        previous = grown
    return weight


def _sdxl_feather_radius(height: int, width: int) -> int:
    """Feather width in pixels: 0.8 % of the short edge, at least 3 px.

    Resolution-relative for the same reason every other window in this file is: 4 px on a
    480 px fixture and 4 px on a 1600 px flat-lay are not the same feather. 0.8 % is where the
    seam stops being visible on fabric texture — measured by eye on the two fixture outputs,
    see `docs/HANDOFF-vision.md`.
    """
    return max(SDXL_FEATHER_MIN_PX, int(round(SDXL_FEATHER_FRACTION * min(height, width))))


def _feathered_repaint_weight(audit: WrinkleMaskAudit) -> tuple[np.ndarray, int]:
    """Inpaint strength per pixel, in [0, 1], plus the radius that produced it.

    Three properties, and each one is load-bearing:

    * **only the final wrinkle mask** — the mask that already had the protection region and
      the boundary band subtracted, so this function cannot re-introduce either;
    * **feathered outward**, because a hard mask edge in a generative fill reads as a sticker:
      the generated texture stops dead on a contour;
    * **strength decays with the feather** — the value *is* the composite alpha, so a pixel on
      the outermost ring is 10 % generated and 90 % original. This is why the soft edge is
      applied here and not by the pipeline: `StableDiffusionXLInpaintPipeline` binarises
      `mask_image` at 0.5 before building its 9-channel UNet input, so a soft mask handed to
      the model is a hard mask with a differently-placed contour. The feather has to be ours.

    The returned weight is clipped to `interior & ~protect_mask`, so the ring can never bleed
    into the boundary band or over printed ink — the same two exclusions the mask itself
    obeys, applied to the feather as well.
    """
    height, width = audit.final_mask.shape
    radius = _sdxl_feather_radius(height, width)
    support = audit.interior & ~audit.protect_mask
    return _outward_feather(audit.final_mask, radius) * support, radius


def _quantise(rgb: np.ndarray) -> np.ndarray:
    return np.rint(np.clip(rgb, 0.0, 255.0)).astype(np.uint8)


def prettify_sdxl(
    flat_png: bytes,
    alpha_png: bytes,
    *,
    protect: Sequence[Box] | None = None,
    steps: int | None = None,
    strength: float | None = None,
    guidance: float | None = None,
    seed: int | None = None,
    side: int | None = None,
) -> PrettifyResult:
    """`mode=SDXL_INPAINT`: the same mask pipeline, then SDXL inpainting inside it.

    READ THIS FIRST — THREE MEASURED FAILURES, NOT CAVEATS
    -----------------------------------------------------
    This mode is implemented, deterministic, protected and timed. It does **not** meet the
    product bar, and the three numbers below are why. They are at the top of the docstring,
    before the design, because the next person to read this file will be someone wondering
    whether they can "just switch SDXL on".

    1. **SSIM inside the mask is 0.6920 / 0.7415 against a 0.80 floor** — E0-T5's
       `prettify SSIM (inside mask) >= 0.80`. Two fixtures, same definition as
       `evals/metrics/prettify.py`. The classical path scores 0.9856 / 0.9792 on the same two.
       The generative step moves pixels inside the region it was allowed to touch roughly
       twenty times as far.
    2. **It is ~2× over the latency gate.** E0-T5 asks p50 ≤ 20 s / p90 ≤ 35 s. Measured warm:
       **38.3 s** (`crease-01-breeches-leg`) and **42.5 s** (`logo-01-tee-chest`), MPS fp16,
       30 steps, strength 0.85, canvas 704×1024 and 1024×768. Both are above *p90*, so these
       two samples alone put p50 at ~40 s. Cold adds a ~10 s model load on top.
    3. **The output is traced, not removed — it looks repainted.** A soft fold comes back as a
       thin, hard, blue-grey **scratch line**; on a real photo (`--in .../top-01.jpg` through
       the full CLI) the garment looks like someone drew on it with a fine pen. Not "slightly
       retouched": drawn on.

    **Why, because this is the useful part and it is not a tuning miss.** The wrinkle mask is a
    hairline/speckle field — 1–3 px lines plus isolated pixels. SDXL's VAE downsamples 8×, so
    on a 704×1024 canvas the mask is **0.25–0.75 latent pixels wide**: the UNet is asked to
    fill a feature it cannot represent, and the decoder resolves that as a high-frequency ridge
    along the contour. Measured attempts to escape it: widening the mask by 2 / 4 / 8 px takes
    mask coverage from 7.7 % to **57.7 % / 85.7 % / 103 %** of the garment interior (the mask is
    scattered, so area grows with the *square* of the radius) and produces flat grey blocks;
    lowering strength to 0.45 leaves the scratch lines and only keeps more of the original
    cloth. **SDXL inpainting wants a region, not a network of creases.** Making this work needs
    a crease detector that emits bands, which is a different ticket — and see
    `docs/HANDOFF-vision.md` §9.6 for why "use it for stains instead" is not a product direction
    here: `ItemAttributes.defects` exists because listings must disclose defects.

    The rest of this docstring describes a correctly built machine whose output is not shippable.

    Knobs
    -----
    This function is the one place the round-2 knobs are reachable from. `prettify()` keeps
    its frozen four-parameter signature (`test_public_signatures_are_stable`) and calls this
    with the module defaults; the CLI passes `--sdxl-steps` / `--sdxl-seed` / `--sdxl-side`
    through here so a cost curve can be measured without editing constants.

    **What is reused, not re-implemented.** White balance, the wrinkle score, the coverage
    cap, the boundary band, morphology and the protection subtraction all come from
    `_build_wrinkle_mask` — the same function `mode=CLASSICAL` consumes and the same one the
    eval harness audits. A second mask implementation for the generative path would be a
    second thing to keep honest.

    **What is new.** The mask is turned into a feathered per-pixel repaint strength
    (`_feathered_repaint_weight`), the model is run at a 64-aligned working resolution, and
    the result is composited back with `out = balanced·(1 − w) + generated·w` at the *input*
    resolution. The composite is what makes the two guarantees checkable rather than hopeful:
    where `w == 0`, the output is the white-balanced input bit for bit, so nothing the model
    invented can leak into the logo, the boundary band, or the backdrop. Those two guarantees
    hold and are asserted:
    `test_sdxl_output_equals_the_white_balanced_input_where_the_weight_is_zero` and
    `test_repaint_weight_never_enters_the_protection_region_or_the_boundary_band`.

    **Dependencies are optional.** This needs the `sdxl` dependency group (0.72 GB) and 6.9 GB
    of pinned weights; a plain `make install` has neither. Both are checked with
    `importlib.util.find_spec` + per-file byte counts before anything is imported, so a missing
    group is a one-sentence refusal naming `make install-sdxl`, not an `ImportError` traceback.
    It never downloads or installs anything itself.

    Raises `PrettifyBackendUnavailable` (a `StageNotImplemented`, so the worker treats it as
    terminal) when the dependency group or the pinned weights are missing. See
    `docs/HANDOFF-vision.md` §"E2-T2 round 2" for the full table and the cost curve.
    """
    if not flat_png:
        raise ValueError("prettify(): flat_png bytes are empty")
    if not alpha_png:
        raise ValueError("prettify(): alpha_png bytes are empty")

    started = time.perf_counter()
    rgb = _decode_rgb(flat_png, "flat_png")
    alpha = _decode_l(alpha_png, "alpha_png")
    if alpha.shape != rgb.shape[:2]:
        raise ValueError(
            f"prettify(): alpha shape {alpha.shape} does not match flat shape {rgb.shape[:2]}"
            "; rectified flat-lay and alpha must be the same canvas (rectify does not return "
            "the warped alpha — resize it to the flat-lay before calling prettify)"
        )
    if not (alpha >= 128).any():
        raise ValueError("prettify(): alpha contains no garment pixels (no_garment)")

    height, width = rgb.shape[:2]
    audit = _build_wrinkle_mask(rgb, alpha, protect=protect)
    weight, feather_radius = _feathered_repaint_weight(audit)
    balanced = _quantise(audit.balanced)

    resolved_steps = SDXL_STEPS if steps is None else int(steps)
    resolved_strength = SDXL_STRENGTH if strength is None else float(strength)
    resolved_guidance = SDXL_GUIDANCE if guidance is None else float(guidance)
    resolved_seed = SDXL_SEED if seed is None else int(seed)
    resolved_side = SDXL_WORK_SIDE if side is None else int(side)
    if resolved_steps < 1:
        raise ValueError(f"prettify_sdxl(): steps={resolved_steps} must be >= 1")
    if not 0.0 < resolved_strength <= 1.0:
        raise ValueError(f"prettify_sdxl(): strength={resolved_strength} must be in (0, 1]")
    if resolved_guidance <= 0.0:
        raise ValueError(f"prettify_sdxl(): guidance={resolved_guidance} must be > 0")
    if resolved_side < SDXL_WORK_SIDE_MIN:
        raise ValueError(
            f"prettify_sdxl(): side={resolved_side} is below {SDXL_WORK_SIDE_MIN}; SDXL below "
            "that duplicates hems and seams, so a smaller canvas would be a different stage"
        )

    notes: dict = dict(audit.notes)
    notes.update(
        {
            "mode": PrettifyMode.SDXL_INPAINT.value,
            "seed": resolved_seed,
            "network_calls": 0,
            "canvas": [int(width), int(height)],
            "honest_scope": _SDXL_HONEST_SCOPE,
            "ssim_note": (
                "SSIM here is output vs input over the garment, i.e. a destruction budget: "
                "it says how far the pixels moved, not whether the crease went away. A "
                "generative inpaint necessarily scores far below the classical fill on this "
                "metric and that is not by itself evidence of a better or worse result"
            ),
        }
    )

    repaint_px = int(audit.final_mask.sum())
    if repaint_px == 0:
        # Nothing was selected, so there is nothing to inpaint. Loading 7 GB to paint zero
        # pixels would be a lie about what ran, so the model is not loaded and the notes say
        # exactly that. The output is still the white-balanced image, so the label is honest:
        # `generative_step` reports the skip.
        notes["generative_step"] = "skipped: empty wrinkle mask"
        notes["generative"] = {"repaint_px": 0, "model_loaded": False}
        return PrettifyResult(
            prettified_png=_encode_rgb(balanced),
            meta=StageMeta(
                stage="prettify",
                backend=_SDXL_BACKEND,
                device="cpu",
                seconds=time.perf_counter() - started,
                notes=notes,
            ),
        )

    weights_dir = _require_sdxl_backend()
    device, dtype_name = _sdxl_device_and_dtype()

    load_started = time.perf_counter()
    cache_info_before = _load_sdxl_pipeline.cache_info()
    try:
        pipe = _load_sdxl_pipeline(str(weights_dir), device, dtype_name)
    except PrettifyBackendUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - a loader can fail a dozen ways; name the fix
        raise PrettifyBackendUnavailable(_sdxl_refusal(f"{type(exc).__name__}: {exc}")) from exc
    model_load_s = time.perf_counter() - load_started
    cold = _load_sdxl_pipeline.cache_info().misses > cache_info_before.misses

    import torch

    work_h, work_w = _sdxl_work_size(height, width, resolved_side)
    image_pil = Image.fromarray(balanced).resize(
        (work_w, work_h), Image.LANCZOS
    )
    # The **binary** final mask, not the feathered weight: the model's job is to regenerate
    # the crease and nothing else, and the coverage cap is a promise about the region the
    # model may touch. The pipeline binarises `mask_image` at 0.5 anyway, so handing it a soft
    # mask would only move the contour without making it soft. The soft edge lives in the
    # composite below, where it is exact (`_outward_feather`) and where a weight of 0 provably
    # means "not generated".
    mask_pil = Image.fromarray(np.where(audit.final_mask, 255, 0).astype(np.uint8)).resize(
        (work_w, work_h), Image.BILINEAR
    )

    generate_started = time.perf_counter()
    generator = _make_generator(torch, device, resolved_seed)
    result = pipe(
        prompt=SDXL_PROMPT,
        negative_prompt=SDXL_NEGATIVE_PROMPT,
        image=image_pil,
        mask_image=mask_pil,
        height=work_h,
        width=work_w,
        strength=resolved_strength,
        num_inference_steps=resolved_steps,
        guidance_scale=resolved_guidance,
        generator=generator,
        num_images_per_prompt=1,
    )
    generate_s = time.perf_counter() - generate_started

    generated_image = result.images[0].convert("RGB")
    if generated_image.size != (width, height):
        generated_image = generated_image.resize((width, height), Image.LANCZOS)
    generated = np.asarray(generated_image, dtype=np.float64)

    # The composite, and the reason it is here rather than inside the pipeline: it is the only
    # formulation in which "w == 0 => output == white-balanced input, bit for bit" is true by
    # construction instead of by observation.
    alpha_weight = weight[:, :, None]
    composited = audit.balanced * (1.0 - alpha_weight) + generated * alpha_weight
    prettified_png = _encode_rgb(_quantise(composited))
    seconds = time.perf_counter() - started

    if device == "mps":  # pragma: no cover - depends on the host
        torch.mps.empty_cache()

    notes["generative_step"] = "sdxl-inpainting-0.1"
    notes["generative"] = {
        "model_id": SDXL_MODEL_ID,
        "revision": SDXL_REVISION,
        "variant": SDXL_VARIANT,
        "model_loaded": True,
        "cold": bool(cold),
        "model_load_s": round(model_load_s, 3),
        "generate_s": round(generate_s, 3),
        "num_inference_steps": resolved_steps,
        "strength": resolved_strength,
        "effective_denoise_steps": round(resolved_steps * resolved_strength, 2),
        "guidance_scale": resolved_guidance,
        "prompt": SDXL_PROMPT,
        "negative_prompt": SDXL_NEGATIVE_PROMPT,
        "work_canvas": [int(work_w), int(work_h)],
        "attention_slicing": bool(device == "mps"),
        "repaint_px": repaint_px,
        "repaint_area_px": int((weight > 0.0).sum()),
        "feather_radius_px": int(feather_radius),
        "repaint_coverage_of_interior": round(
            repaint_px / max(1, int(audit.interior.sum())), 4
        ),
        "feather_profile": "outward Chebyshev rings, raised cosine 1 -> 0 over feather_radius_px",
        "composite": "out = balanced*(1-w) + generated*w, w=0 outside the feathered mask",
        "texture_synthesis": True,
    }
    return PrettifyResult(
        prettified_png=prettified_png,
        meta=StageMeta(
            stage="prettify",
            backend=_SDXL_BACKEND,
            device=device,
            seconds=seconds,  # measured wall-clock; None would mean UNMEASURED
            notes=notes,
        ),
    )


# --------------------------------------------------------------------------------- stage


def prettify(
    flat_png: bytes,
    alpha_png: bytes,
    *,
    mode: PrettifyMode = PrettifyMode.PASS_THROUGH,
    protect: Sequence[Box] | None = None,
) -> PrettifyResult:
    """Catalog-grade prettify of one rectified flat-lay.

    `mode=CLASSICAL` runs the non-generative pipeline: white balance -> wrinkle mask ->
    protection subtraction -> feathered shadow attenuation. `mode=PASS_THROUGH` validates the
    inputs and returns the bytes **untouched**, saying so in `meta.notes`. `mode=SDXL_INPAINT`
    runs the same mask pipeline and then SDXL inpainting inside the feathered mask — see
    `prettify_sdxl()` for the knobs (steps, strength, seed, canvas) and for what it costs. It
    raises `PrettifyBackendUnavailable`, with the fix inside the message, when the pinned
    weights are not on disk **or when the optional `sdxl` dependency group is not
    installed** (a base `make install` has neither) — the sentence names whichever gap
    is actually open, because naming the wrong one costs the reader a round trip. It
    never downloads or installs anything itself.

    `protect` is an optional sequence of `(x, y, w, h)` boxes in flat-lay pixel coordinates —
    e.g. a caller that already knows where the care label is. It is added to the automatic
    detector; passing nothing means "detect it yourself", not "no protection".
    """
    if not flat_png:
        raise ValueError("prettify(): flat_png bytes are empty")
    if not alpha_png:
        raise ValueError("prettify(): alpha_png bytes are empty")
    try:
        requested = PrettifyMode(mode)
    except ValueError as exc:
        raise ValueError(
            f"prettify(): mode={mode!r} is not a PrettifyMode; pass one of "
            f"{[m.value for m in PrettifyMode]} rather than letting the stage guess"
        ) from exc

    if requested is PrettifyMode.SDXL_INPAINT:
        # Delegated rather than handled inline: the generative path has its own knobs and its
        # own (much longer) docstring, and `prettify()`'s signature is frozen at four
        # parameters. The mode label reaching the report is still whatever actually ran.
        return prettify_sdxl(flat_png, alpha_png, protect=protect)

    started = time.perf_counter()
    rgb = _decode_rgb(flat_png, "flat_png")
    alpha = _decode_l(alpha_png, "alpha_png")
    if alpha.shape != rgb.shape[:2]:
        raise ValueError(
            f"prettify(): alpha shape {alpha.shape} does not match flat shape {rgb.shape[:2]}"
            "; rectified flat-lay and alpha must be the same canvas (rectify does not return "
            "the warped alpha — resize it to the flat-lay before calling prettify)"
        )

    if requested is PrettifyMode.PASS_THROUGH:
        # Bytes in, bytes out. The decode above is validation, not processing — it is what
        # makes `seconds` a measurement instead of a placeholder, and what makes a garbage
        # input fail here rather than three stages later.
        seconds = time.perf_counter() - started
        return PrettifyResult(
            prettified_png=flat_png,
            meta=StageMeta(
                stage="prettify",
                backend=_PASS_THROUGH_BACKEND,
                device="cpu",
                seconds=seconds,  # measured wall-clock; None would mean UNMEASURED
                notes={
                    "mode": PrettifyMode.PASS_THROUGH.value,
                    "generative_step": "skipped",
                    "pixels_touched": 0,
                    "network_calls": 0,
                    "note": (
                        "pass-through: the flat-lay is returned byte-identical. No white "
                        "balance, no wrinkle mask, no fill — this is NOT a cleaned image"
                    ),
                },
            ),
        )

    if requested is not PrettifyMode.CLASSICAL:  # pragma: no cover - enum is exhaustive today
        raise PrettifyBackendUnavailable(
            f"mode={requested.value!r} has no implementation in this build "
            "(docs/PLAN.md ticket E2-T2)"
        )

    if not (alpha >= 128).any():
        raise ValueError("prettify(): alpha contains no garment pixels (no_garment)")

    audit = _build_wrinkle_mask(rgb, alpha, protect=protect)
    filled, fill_note = _attenuate(
        audit.balanced, audit.final_mask, height=rgb.shape[0], width=rgb.shape[1]
    )
    prettified_png = _encode_rgb(np.rint(filled).astype(np.uint8))
    seconds = time.perf_counter() - started

    notes = dict(audit.notes)
    notes.update(
        {
            "mode": PrettifyMode.CLASSICAL.value,
            "generative_step": "skipped",
            "fill": fill_note,
            "network_calls": 0,
            "honest_scope": _HONEST_SCOPE,
            "ssim_note": (
                "a garment-mask SSIM near 1.0 here means 'almost nothing was changed', not "
                "'the creases were repaired' — the metric measures destruction by the fill"
            ),
            "canvas": [int(rgb.shape[1]), int(rgb.shape[0])],
        }
    )
    return PrettifyResult(
        prettified_png=prettified_png,
        meta=StageMeta(
            stage="prettify",
            backend=_CLASSICAL_BACKEND,
            device="cpu",
            seconds=seconds,  # measured wall-clock; None would mean UNMEASURED
            notes=notes,
        ),
    )
