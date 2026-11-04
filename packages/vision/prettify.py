"""STAGE 3 — catalog-grade prettify. Owner: CV role (E2, ticket E2-T2).

    prettify(flat_png: bytes, alpha_png: bytes, *, mode: PrettifyMode,
             protect: Sequence[tuple[int, int, int, int]] | None = None) -> PrettifyResult

What is implemented here, and under which name
---------------------------------------------
E2-T2 is a two-round ticket. **Round 1 (this code) ships no model and downloads nothing.**
What it ships is the whole shape of the stage, executed for real:

1. **white balance** — gains estimated from the backdrop ring, so the flat-lay's background
   lands on `#F2F1EE` instead of on whatever the phone's auto-white-balance chose;
2. **wrinkle / crease mask** — high-pass luminance, local contrast, directional coherence,
   morphology, with a hard coverage cap and a hard boundary exclusion;
3. **logo / text protection** — an automatic thin-ink detector *plus* caller-supplied boxes,
   subtracted from the wrinkle mask in the last step;
4. **fill (non-generative)** — low-frequency luminance guidance with feathered edges. It is
   called **shadow/crease attenuation** and that name is the promise: it reduces the contrast
   of a shadow band, it does not remove a crease, and nothing here invents texture.

The generative step (`mode=SDXL_INPAINT`) is **round 2** and raises
`PrettifyBackendUnavailable` with the whole fix inside the message. It never downloads at run
time — same rule as `segment.py`, same reason: a pipeline stage that fetches 10 GB on first
call is a network call inside a zero-network contract.

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

What this does *not* do (round 2, and the eval notes say so out loud)
--------------------------------------------------------------------
* No generative inpaint, so no crease *removal*: a hard fold stays visible, dimmer.
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

import io
import math
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
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

# The round-2 target. Named, sized and licensed here so the refusal can quote it; **not**
# pinned by sha256, because the file has never been on this machine and a made-up digest is
# exactly the kind of number this repo forbids.
SDXL_MODEL_ID = "diffusers/stable-diffusion-xl-1.0-inpainting-0.1"
SDXL_DIRNAME = "sdxl-inpaint-0.1"
SDXL_APPROX_BYTES = 10_000_000_000
SDXL_LICENCE = "CreativeML Open RAIL++-M"
WEIGHTS_DIR_ENV = "SNAPLIST_MODEL_DIR"

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
    """Where the round-2 SDXL weights are looked for. Never creates it, never downloads."""
    override = os.environ.get(WEIGHTS_DIR_ENV, "").strip()
    base = Path(override).expanduser() if override else Path.home() / ".cache/snaplist/models"
    return base / SDXL_DIRNAME


def fetch_hint() -> str:
    """The sentence a missing-weights refusal carries, so the fix is inside the error."""
    size_gb = SDXL_APPROX_BYTES / 1e9
    return (
        f"run `make fetch-models` (fetches {SDXL_MODEL_ID}, ~{size_gb:.0f} GB, licence "
        f"{SDXL_LICENCE}, into {sdxl_weights_dir()})"
    )


def _sdxl_refusal() -> str:
    """The full refusal text: what is missing, how big, where it goes, and what to run."""
    return (
        "mode=SDXL_INPAINT is not implemented in this build (docs/PLAN.md ticket E2-T2, "
        "round 2). It needs the ~10 GB SDXL inpainting weights "
        f"({SDXL_MODEL_ID}, licence {SDXL_LICENCE}) unpacked into "
        f"{sdxl_weights_dir()}, plus torch and diffusers — none of which this environment "
        f"has. Fix: {fetch_hint()} and install the round-2 extras. This stage never "
        "downloads them itself: the pipeline contract is zero network calls, and a stage "
        "that fetches 10 GB on first call turns a missing file into a ten-minute stall "
        "inside a job"
    )


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
    is round 2 and raises `PrettifyBackendUnavailable` with the fix inside the message.

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
        # Refused *before* any pixel work: the honest answer is "this build cannot", not
        # "here is a classical result labelled sdxl".
        raise PrettifyBackendUnavailable(_sdxl_refusal())

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
