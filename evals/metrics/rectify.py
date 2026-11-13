"""E2-T1 — what `rectify SSIM (inside mask)` measures. Defined by **ADR-011 §2.2**.

    python -m evals.metrics.rectify                  # per-case table to stdout
    python -m evals.metrics.rectify --no-report      # table only, write no file

Consumed by `evals/run.py::_measure_rectify()`, which puts one number in the
`--suite vision` table and the caveats in that row's note. The full per-case evidence is
written to `evals/reports/rectify.md` (gitignored, like `prettify.md`).

## Why this module is not "the obvious code"

`rectify()` is a geometric transform, so "SSIM ≥ 0.85 inside the mask **versus the source**" has
no literal reading: the output and the source are in different coordinate systems. Every
reading that typechecks is either meaningless (compare across a homography) or a **tautology**
(compare the stage against the same homography it declared — ≈ 1.0 for *every* internally
consistent implementation, including a wrong one). ADR-011 §1.2 calls the tautology reading
"the default thing an implementer will build", and this module exists mostly to forbid it.

So the DoD is decomposed into **two independent artefacts**, and neither may be derived from
the other:

* **Part A — the number on the row (§2.2).** The output against a reference re-rendered by
  **Pillow's C resampler** under the homography the stage *declared*, compared in the canvas
  frame with the warped alpha as a continuous weight:

      R    = P.transform(canvas, PERSPECTIVE, coeffs, BILINEAR)      # RGB
      a    = A.transform(canvas, PERSPECTIVE, coeffs, BILINEAR) / 255
      Ref  = R·a + backdrop·(1−a)
      SSIM = ssim_map(L(O), L(Ref))          # 11×11 uniform, C1/C2 as prettify
      value= Σ(a·SSIM) / Σ(a)                # weighted, not thresholded, no erosion

  `Ref` is rendered by **Pillow, not by numpy code and not by importing anything from
  `packages.vision.rectify`**. That is the load-bearing decision: it is what makes Part A a
  *differential* measurement instead of a self-comparison. `reference_render()` therefore
  imports nothing from the stage, and a test asserts that on its source text.

* **Part B — two exact conformance checks that give Part A teeth (§2.3).** SSIM cannot see an
  error that lives *in* the declared homography (it is in both images), so:

      B1  H · quad_ref[i] == target_rect[i] within 0.5 px for i = TL, TR, BR, BL; det(H) > 0
      B2  (w, h) == canonical_size(category) within 1 px; max(w, h) <= 1600;
          PNG is 8-bit truecolour, decoded mode "RGB", no alpha band

  `quad_ref` is the alpha's bounding box, cross-checked two ways (§2.3); `target_rect` and the
  aspect table are **transcribed literals in this file**, never imported from the stage, and a
  drift test fails loudly if the stage's table moves. Note what B2 does *not* prove:
  `_encode_rgb` writes no ICC chunk, so the assertion is "8-bit RGB, no alpha", not "carries an
  sRGB profile" — the word sRGB in the DoD implies a colour-management claim nobody tested.

`passes_dod = (Part A ≥ 0.85) and B1 and B2`.

## Read this before quoting the number

> `1.0` = every garment pixel in the output is that garment pixel of the photograph, transported
> by the map the stage declared. Below `0.85` means more than 15 % of the garment's structure
> was **replaced or mis-transported**. This is a *transport budget*, not a correction score: it
> cannot tell a good quad from a bad quad, and it is blind to any error the declared homography
> itself carries.

The budget runs the other way too, and `THRESHOLD_SEMANTICS` says it in the artifact: **`>= 0.85`
does not mean nothing was repainted**, and there is no percentage behind it — in the canvas frame
`logo-02` survives 5 % scattered repainting and fails at 7 %, while `logo-01` fails at 2 %. And
`RESAMPLER_FLOOR` records what the ceiling is: ~0.98, because the reference is Pillow's
antialiasing resampler while the stage point-samples a 2-tap bilinear. What Part A rewards is
agreement with Pillow's kernel, **not** smoothness; blurring the output only lowers the score.

`ssim_binary` (mean over `alpha >= 128`) is reported next to the weighted value so the number
stays comparable with prettify's binary-mask convention and so a large gap between the two is
visible rather than hidden. The gate uses the weighted value; the binary value is evidence.

## Status

ADR-011 is `Proposed — pending Bert's review`. Its restatement of E2-T1's DoD sentence is **not
adjudicated**; `docs/PLAN.md` still says "versus the source". The row prints a number because a
number is what the harness is for, and this module's note says — in the artifact, not only in a
commit message — that the criterion it is scored against is a proposal.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from evals.metrics.ssim import luminance, ssim_map, weighted_mean

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = ROOT / "evals" / "fixtures" / "prettify"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "rectify.md"

# ---------------------------------------------------------------------------------------------
# Spec literals, TRANSCRIBED — never imported from `packages.vision.rectify`
# ---------------------------------------------------------------------------------------------
# ADR-011 §2.3: `target_rect` and the aspect table are transcribed into the metric from the
# spec, not imported from the stage. If this table were imported, a change to the stage would
# silently redefine both sides of the comparison and the B1 check could never fail. The values
# are the flat-lay design targets recorded for E2-T1: a tee, a jean and a shoe must each fill
# the frame without letterboxing, hence one aspect per category rather than one global canvas.
CANONICAL_ASPECT: dict[str, float] = {
    "top": 4 / 5,
    "bottom": 4 / 5,
    "dress": 3 / 4,
    "outerwear": 4 / 5,
    "shoe": 4 / 3,
    "accessory": 1.0,
    "other": 4 / 5,
}
# Long edge of the canonical canvas (BRIEF.md "canonical flat-lay aspect per category"; PLAN
# E2-T1 pins it at 1200).
CANONICAL_LONG_EDGE_PX = 1200
# BRIEF.md: "Output PNG <= 1600px".
MAX_LONG_EDGE_PX = 1600
# BRIEF.md / PLAN E2-T1: neutral flat-lay backdrop.
BACKDROP_RGB: tuple[int, int, int] = (242, 241, 238)
# PLAN E2-T1's DoD line. Unchanged, and not this module's to move.
DOD_MIN_SSIM = 0.85
# ADR-011 §2.3: the solve is exact at the four correspondences, so 0.5 px is enormous slack for
# float noise and microscopic for a wrong corner order / transposed axis / inset quad.
CORNER_TOLERANCE_PX = 0.5
# ADR-011 §2.3 B2: integer rounding of `round(long_edge * aspect)` is the only slack owed.
CANVAS_TOLERANCE_PX = 1
# ADR-011 §2.2 guard: a canvas that is 99 % backdrop is not a measurement of anything.
MIN_WEIGHT_FRACTION = 0.01
# ADR-011 §2.2: the binary value is prettify's convention (alpha >= 128), reported as evidence.
BINARY_ALPHA_THRESHOLD = 128
# A garment pixel is "closed" for the |dL| evidence column; the SSIM weight stays continuous.
CLOSED_ALPHA_THRESHOLD = 0.5

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# PNG colour types (spec 11.2.2): 0 grey, 2 truecolour, 3 palette, 4 grey+alpha, 6 RGBA.
PNG_TRUECOLOUR = 2

Quad = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]

# ---------------------------------------------------------------------------------------------
# The caveats that must travel with the number (§2.5), exported so they cannot drift
# ---------------------------------------------------------------------------------------------
DEFINITION_SOURCE = (
    "ADR-011 §2.2 (alpha-weighted SSIM against an independently re-rendered reference)"
)

CAPABILITY_LIMITS = (
    "what it cannot prove (ADR-011 §2.5): (1) quad quality — H is derived from the alpha "
    "bounding box, so a wrong mask means a wrong bbox, and a warp that crops or stretches the "
    "garment passes all three checks; (2) absolute correctness — there is no ground-truth quad "
    "on disk for any of the 9 photos, so this verifies that the alpha_bbox convention was "
    "followed, not that the convention is right"
)

PROPOSAL_STATUS = (
    "ADR-011 is `Proposed — pending Bert's review`: its restatement of E2-T1's DoD is NOT "
    "adjudicated, so this row is evidence toward a proposed criterion, not an approved gate"
)

CANVAS_CAVEAT = (
    "B2 asserts 8-bit RGB with no alpha band and not 'carries an sRGB profile' — the encoder "
    "writes no ICC chunk, so no colour-management claim is tested"
)

# The resampler floor, measured. ADR-011 §7 predicted "max|Δ| = 1 level, achievable maximum
# ≈0.999"; that is wrong, and the reason is not "Pillow's bilinear is fixed-point 8-bit" — it is
# that Pillow's `transform` is the *antialiasing* resampler. Measured on `logo-02` (bottom-03,
# 960x1200) inside the warped mask; the 1-level figure holds only where the map is 1:1.
RESAMPLER_FLOOR = (
    "the achievable ceiling is ~0.98, not 1.0: Pillow's `transform` is the *antialiasing* "
    "resampler (the same machinery as `resize`), so the reference is smoother than the stage's "
    "2-tap bilinear — logo-02, inside the warped matte >= 0.5, 8-bit levels: per-channel "
    "max|Δ| 33, luminance max|Δ| 32, luminance mean|Δ| 2.47, 99.9th pct 16 (ADR-011 §7's "
    "'±1 level' holds only at unit scale). What Part A rewards is **agreement with Pillow's "
    "kernel, not smoothness** — a stage rendering through `Image.transform` itself scores 1.0000 "
    "where the current one scores 0.9694, and blurring only lowers the score (0.9694 -> 0.9172 "
    "-> 0.6727 -> 0.5250 at Gaussian radius 0.6 / 1.2 / 2.0)"
)

# What `>= 0.85` actually buys. Measured, and the reason the row must not be read as "nothing
# was repainted": a 10 % scattered repaint in the source still clears the floor.
THRESHOLD_SEMANTICS = (
    "`>= 0.85` is a transport budget, not proof that nothing was repainted — and the tolerance "
    "is **not a percentage**: it depends on where the damage sits and on the garment's local "
    "structure. Measured in the canvas frame (the frame that is scored), scattered "
    "(128,128,128) over the warped matte, seed 0, lossless PNG: logo-02 survives 5 % "
    "(0.8796) and fails at 7 % (0.8492), while logo-01 fails at 2 % (0.6488) and logo-05 at "
    "2 % (0.8060). An earlier version of this note said '~10 % still passes'; that came from "
    "scattering in the SOURCE frame, where the 1920x1280 -> 1200x960 downsample averages the "
    "speckle away, and it does not hold in the canvas"
)


# ---------------------------------------------------------------------------------------------
# Geometry — an independent implementation, so the metric does not borrow the stage's arithmetic
# ---------------------------------------------------------------------------------------------
def canonical_size(category: str) -> tuple[int, int]:
    """The flat-lay canvas `(width, height)` for `category`, from the transcribed table.

    Unknown categories fall back to `other`, matching the stage's documented behaviour: a
    category the VLM invented must not make the metric crash, and 4:5 is the neutral choice.
    """
    aspect = CANONICAL_ASPECT.get(category, CANONICAL_ASPECT["other"])
    if aspect <= 0.0:
        raise ValueError(f"canonical_size: non-positive aspect {aspect} for {category!r}")
    if aspect >= 1.0:
        return CANONICAL_LONG_EDGE_PX, max(int(round(CANONICAL_LONG_EDGE_PX / aspect)), 1)
    return max(int(round(CANONICAL_LONG_EDGE_PX * aspect)), 1), CANONICAL_LONG_EDGE_PX


def target_rect(width: int, height: int) -> Quad:
    """The four corners of the target canvas, clockwise from the top-left (TL, TR, BR, BL)."""
    if width <= 0 or height <= 0:
        raise ValueError("target_rect: the rectangle must have positive extent")
    return (
        (0.0, 0.0),
        (float(width), 0.0),
        (float(width), float(height)),
        (0.0, float(height)),
    )


def determinant(matrix: tuple[float, ...] | list[float]) -> float:
    """Determinant of a row-major 3×3. A mirror (negative) is a bug, always (§2.3)."""
    a, b, c, d, e, f, g, h, i = _as_matrix(matrix)
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def apply_homography(matrix: tuple[float, ...] | list[float], point: tuple[float, float]):
    """Map one point through a row-major 3×3 homography. Raises when it maps to infinity."""
    a, b, c, d, e, f, g, h, i = _as_matrix(matrix)
    x, y = float(point[0]), float(point[1])
    w = g * x + h * y + i
    if abs(w) < 1e-12:
        raise ValueError(f"apply_homography: {point} maps to infinity")
    return ((a * x + b * y + c) / w, (d * x + e * y + f) / w)


def invert_homography(matrix: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    """The inverse via the adjugate, normalised so `h33 = 1`.

    Normalising matters: Pillow's PERSPECTIVE coefficients are read as an inverse map whose
    bottom-right element is 1 by definition, so handing it an un-normalised adjugate would
    scale the whole map. Raises when the matrix is not invertible (a degenerate quad).
    """
    a, b, c, d, e, f, g, h, i = _as_matrix(matrix)
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if not math.isfinite(det) or abs(det) < 1e-12:
        raise ValueError("invert_homography: the matrix is not invertible (determinant ~ 0)")
    adjugate = (
        e * i - f * h,
        c * h - b * i,
        b * f - c * e,
        f * g - d * i,
        a * i - c * g,
        c * d - a * f,
        d * h - e * g,
        b * g - a * h,
        a * e - b * d,
    )
    normalised = [value / det for value in adjugate]
    if abs(normalised[8]) < 1e-12:
        raise ValueError("invert_homography: the inverse has h33 = 0; cannot express as PIL data")
    return tuple(value / normalised[8] for value in normalised)


def perspective_coefficients(matrix: tuple[float, ...] | list[float]) -> tuple[
    float, float, float, float, float, float, float, float
]:
    """Pillow's 8 PERSPECTIVE coefficients for the canvas -> photo map.

    Pillow reads them as

        source_x = (a·x + b·y + c) / (g·x + h·y + 1)
        source_y = (d·x + e·y + f) / (g·x + h·y + 1)

    for output pixel `(x, y)`, i.e. an **inverse** map with `h33 = 1` — which is exactly the
    inverse of `RectifyResult.homography` (photo -> canvas) once normalised. The pixel-centre
    convention is integer indices on both sides, verified by
    `test_identity_homography_on_a_checkerboard_matches_within_one_level`: a half-pixel
    disagreement here would show up as a uniform SSIM loss and would be "fixed" by loosening
    the threshold, which is the failure mode ADR-011 exists to prevent.
    """
    inverse = invert_homography(matrix)
    return (
        inverse[0],
        inverse[1],
        inverse[2],
        inverse[3],
        inverse[4],
        inverse[5],
        inverse[6],
        inverse[7],
    )


def _as_matrix(matrix: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    if len(matrix) != 9:
        raise ValueError(f"a homography has exactly 9 values, got {len(matrix)}")
    return tuple(float(value) for value in matrix)


def quad_reference(alpha: np.ndarray) -> tuple[Quad, str | None]:
    """The garment quad implied by the alpha matte: (TL, TR, BR, BL) of the nonzero pixels.

    ADR-011 §2.3 pins this to **two** computations that must agree: Pillow's
    `Image.getbbox()` (whose right/bottom are exclusive, so they are decremented to the
    inclusive silhouette extremes) and `np.nonzero(alpha > 0)` min/max. A mismatch is a
    fixture anomaly: it is returned in the second slot and printed, never smoothed away,
    because the two would then be defining different rectangles and the 0.5 px check would be
    measuring a disagreement about the reference rather than about the stage.

    Raises when the alpha has no garment pixels at all — that is not an anomaly to report, it
    is a fixture that cannot be measured.
    """
    array = np.asarray(alpha)
    if array.ndim != 2:
        raise ValueError(f"quad_reference: alpha must be 2-D, got shape {array.shape}")
    ys, xs = np.nonzero(array > 0)
    if xs.size == 0:
        raise ValueError("quad_reference: the alpha matte has no garment pixels (no_garment)")
    left, right = int(xs.min()), int(xs.max())
    top, bottom = int(ys.min()), int(ys.max())

    with Image.fromarray(np.ascontiguousarray(array.astype(np.uint8))) as image:
        bbox = image.getbbox()
    expected = (left, top, right + 1, bottom + 1)
    anomaly = (
        None
        if bbox is not None and tuple(bbox) == expected
        else f"getbbox()={bbox} disagrees with the nonzero-pixel bbox {expected}"
    )
    quad: Quad = (
        (float(left), float(top)),
        (float(right), float(top)),
        (float(right), float(bottom)),
        (float(left), float(bottom)),
    )
    return quad, anomaly


# ---------------------------------------------------------------------------------------------
# Part A — the reference render (Pillow's C resampler; NOT numpy, NOT the stage's code)
# ---------------------------------------------------------------------------------------------
def reference_render(
    photo: np.ndarray,
    alpha: np.ndarray,
    homography: tuple[float, ...] | list[float],
    canvas: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """`(Ref, a)` on the canvas grid: the photograph composited through `homography`.

    `photo` is RGB uint8 `Hp×Wp`, `alpha` is L uint8 the same size, `canvas` is the output
    `(width, height)`, `homography` maps photo -> canvas. Returns `Ref` as float RGB `h×w×3`
    and `a` as float `h×w` in `[0, 1]`.

    **This function must not call the stage.** It builds the reference with `Image.transform`,
    the C implementation in Pillow, so the two images being compared come out of two different
    resamplers. If this used the stage's `_bilinear`, the comparison would be the stage against
    itself and the number would be ≈ 1.0 for a wrong homography too — ADR-011 §1.2(b), the
    exact mutation QA once used to fake the prettify metric. `test_reference_resampler_is_not_
    the_stage_resampler` measures the difference and requires at least one differing pixel, and
    a second test asserts this function's source imports nothing from `packages.vision`.

    `fillcolor` never matters on a real case: the alpha-bbox quad maps the whole canvas onto
    the bbox, so every canvas pixel samples inside the photograph. It is set anyway so that a
    degenerate control (a quad that overhangs the frame) has a defined — rather than a
    silently black — background.
    """
    height, width = canvas[1], canvas[0]
    if height <= 0 or width <= 0:
        raise ValueError(f"reference_render: bad canvas {canvas}")
    photo_array = np.ascontiguousarray(np.asarray(photo, dtype=np.uint8))
    alpha_array = np.ascontiguousarray(np.asarray(alpha, dtype=np.uint8))
    if photo_array.ndim != 3 or photo_array.shape[2] != 3:
        raise ValueError(f"reference_render: photo must be HxWx3 RGB, got {photo_array.shape}")
    if alpha_array.ndim != 2 or alpha_array.shape != photo_array.shape[:2]:
        raise ValueError(
            f"reference_render: alpha shape {alpha_array.shape} does not match the photo's "
            f"{photo_array.shape[:2]}"
        )
    coefficients = perspective_coefficients(homography)

    with Image.fromarray(photo_array) as photo_image:
        warped_number = photo_image.transform(
            (width, height),
            Image.PERSPECTIVE,
            coefficients,
            Image.BILINEAR,
            fillcolor=BACKDROP_RGB,
        )
        warped_rgb = np.asarray(warped_number, dtype=np.float64).copy()
    with Image.fromarray(alpha_array) as alpha_image:
        warped_alpha_number = alpha_image.transform(
            (width, height), Image.PERSPECTIVE, coefficients, Image.BILINEAR, fillcolor=0
        )
        warped_alpha = np.asarray(warped_alpha_number, dtype=np.float64).copy() / 255.0

    backdrop = np.asarray(BACKDROP_RGB, dtype=np.float64)
    alpha_band = warped_alpha[:, :, None]
    reference = warped_rgb * alpha_band + backdrop * (1.0 - alpha_band)
    return reference, warped_alpha


# ---------------------------------------------------------------------------------------------
# The score of one case
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CaseScore:
    """Every field of ADR-011 §2 for one case, plus the evidence the report prints.

    `measured` is False only when the case has no region to score (§2.2's guard); everything
    SSIM-shaped is then NaN rather than 0.0, because `0.0` is a *measurement* meaning "the
    garment was destroyed" and a missing number must not look like a catastrophic one.
    """

    case_id: str
    category: str
    # Part A (§2.2)
    measured: bool
    unmeasured_reason: str | None
    ssim_weighted: float
    ssim_binary: float
    ssim_whole_canvas: float  # evidence only — R2 rejects it as *the* number
    weighted_binary_gap: float
    weight_sum: float
    weight_fraction: float
    mean_abs_delta_in_mask: float
    # Part B1 (§2.3)
    corners_ok: bool
    corner_residual_px: float
    corner_residuals: tuple[float, float, float, float]
    determinant: float
    determinant_ok: bool
    quad_anomaly: str | None
    # Part B2 (§2.3)
    canvas_size: tuple[int, int]
    canvas_expected_size: tuple[int, int]
    canvas_ok: bool
    canvas_reason: str
    # Verdict
    passes_dod: bool
    seconds: float


def score_output(
    photo: np.ndarray,
    alpha: np.ndarray,
    category: str,
    flat_png: bytes,
    homography: tuple[float, ...] | list[float],
    *,
    case_id: str = "control",
    seconds: float = 0.0,
) -> CaseScore:
    """Score one output PNG against the reference re-rendered from `photo` + `alpha`.

    Takes the bytes and the declared matrix as **arguments** rather than calling `rectify()`:
    that is what lets the negative controls in `evals/tests/rectify_controls.py` score outputs
    a wrong implementation would have produced (a blank canvas, an identity homography, a
    nearest-neighbour resample) through the *same* scoring path the real fixtures use. A metric
    whose only entry point is "run the stage" can only be tested with correct stages.
    """
    decoded, canvas_reason_png, canvas_size = _decode_output(flat_png)
    expected_size = canonical_size(category)

    # ---- Part A: both operands on the output grid; the output is never resampled.
    reference, warped_alpha = reference_render(photo, alpha, homography, canvas_size)
    output_luma = luminance(decoded.astype(np.float64))
    reference_luma = luminance(reference)
    weights = warped_alpha
    weight_sum = float(weights.sum())
    weight_fraction = weight_sum / float(canvas_size[0] * canvas_size[1])

    if weight_sum < MIN_WEIGHT_FRACTION * canvas_size[0] * canvas_size[1]:
        reason = (
            f"the warped alpha covers {weight_fraction:.4f} of the canvas, below the "
            f"{MIN_WEIGHT_FRACTION:g} floor: a canvas that is ~100 % backdrop is not a "
            "measurement of anything (ADR-011 §2.2 guard)"
        )
        missing = float("nan")
        return CaseScore(
            case_id=case_id,
            category=category,
            measured=False,
            unmeasured_reason=reason,
            ssim_weighted=missing,
            ssim_binary=missing,
            ssim_whole_canvas=missing,
            weighted_binary_gap=missing,
            weight_sum=weight_sum,
            weight_fraction=weight_fraction,
            mean_abs_delta_in_mask=missing,
            corners_ok=False,
            corner_residual_px=float("inf"),
            corner_residuals=(float("inf"),) * 4,
            determinant=float("nan"),
            determinant_ok=False,
            quad_anomaly=None,
            canvas_size=canvas_size,
            canvas_expected_size=expected_size,
            canvas_ok=False,
            canvas_reason=canvas_reason_png,
            passes_dod=False,
            seconds=seconds,
        )

    map_values = ssim_map(output_luma, reference_luma)
    weighted = weighted_mean(map_values, weights)
    closed = warped_alpha >= (BINARY_ALPHA_THRESHOLD / 255.0)
    binary = float(map_values[closed].mean()) if bool(closed.any()) else float("nan")
    whole = float(map_values.mean())
    gap = abs(weighted - binary) if np.isfinite(binary) else float("nan")
    delta = np.abs(output_luma - reference_luma)
    mean_delta = (
        float(delta[warped_alpha >= CLOSED_ALPHA_THRESHOLD].mean())
        if bool((warped_alpha >= CLOSED_ALPHA_THRESHOLD).any())
        else float("nan")
    )

    # ---- Part B1: corner conformance, which pins all 8 degrees of freedom of H.
    quad, anomaly = quad_reference(alpha)
    target = target_rect(expected_size[0], expected_size[1])
    residuals: list[float] = []
    for corner, destination in zip(quad, target, strict=True):
        try:
            mapped = apply_homography(homography, corner)
        except ValueError:
            residuals.append(float("inf"))
            continue
        residuals.append(
            math.hypot(mapped[0] - destination[0], mapped[1] - destination[1])
        )
    corner_residuals = (residuals[0], residuals[1], residuals[2], residuals[3])
    worst_corner = max(corner_residuals)
    corners_ok = worst_corner <= CORNER_TOLERANCE_PX

    det = determinant(homography)
    determinant_ok = math.isfinite(det) and det > 0.0

    # ---- Part B2: canvas conformance.
    canvas_problems: list[str] = []
    if canvas_reason_png:
        canvas_problems.append(canvas_reason_png)
    if (
        abs(canvas_size[0] - expected_size[0]) > CANVAS_TOLERANCE_PX
        or abs(canvas_size[1] - expected_size[1]) > CANVAS_TOLERANCE_PX
    ):
        canvas_problems.append(
            f"canvas {canvas_size[0]}x{canvas_size[1]} != canonical "
            f"{expected_size[0]}x{expected_size[1]} for category {category!r} "
            f"(aspect {CANONICAL_ASPECT.get(category, CANONICAL_ASPECT['other']):.4f} at long "
            f"edge {CANONICAL_LONG_EDGE_PX}, tolerance {CANVAS_TOLERANCE_PX} px per side)"
        )
    long_edge = max(canvas_size)
    if long_edge > MAX_LONG_EDGE_PX:
        canvas_problems.append(
            f"long edge {long_edge} px exceeds the {MAX_LONG_EDGE_PX} px limit"
        )
    canvas_ok = not canvas_problems
    passes_dod = bool(
        weighted >= DOD_MIN_SSIM and corners_ok and determinant_ok and canvas_ok
    )

    return CaseScore(
        case_id=case_id,
        category=category,
        measured=True,
        unmeasured_reason=None,
        ssim_weighted=weighted,
        ssim_binary=binary,
        ssim_whole_canvas=whole,
        weighted_binary_gap=gap,
        weight_sum=weight_sum,
        weight_fraction=weight_fraction,
        mean_abs_delta_in_mask=mean_delta,
        corners_ok=corners_ok,
        corner_residual_px=worst_corner,
        corner_residuals=corner_residuals,
        determinant=det,
        determinant_ok=determinant_ok,
        quad_anomaly=anomaly,
        canvas_size=canvas_size,
        canvas_expected_size=expected_size,
        canvas_ok=canvas_ok,
        canvas_reason="; ".join(canvas_problems),
        passes_dod=passes_dod,
        seconds=seconds,
    )


def _decode_output(flat_png: bytes) -> tuple[np.ndarray, str, tuple[int, int]]:
    """Decode the stage's PNG. Returns `(rgb_uint8, problem_or_empty, (w, h))`.

    The header is parsed from the bytes as well as trusted to PIL, because "8-bit RGB, no alpha
    band" is a claim about the *file*: Pillow happily decodes a 16-bit or RGBA PNG into whatever
    mode it likes, and converting first would answer a question about the conversion instead of
    about the encoder.
    """
    if not flat_png:
        raise ValueError("_decode_output: the output PNG is empty")
    if flat_png[:8] != PNG_MAGIC:
        raise ValueError("_decode_output: the output is not a PNG (magic bytes do not match)")
    if flat_png[12:16] != b"IHDR":
        raise ValueError("_decode_output: the PNG does not start with an IHDR chunk")
    width = int.from_bytes(flat_png[16:20], "big")
    height = int.from_bytes(flat_png[20:24], "big")
    bit_depth = int(flat_png[24])
    colour_type = int(flat_png[25])

    problems: list[str] = []
    if colour_type != PNG_TRUECOLOUR:
        problems.append(
            f"PNG colour type {colour_type} is not truecolour (2) — an alpha band or a palette "
            "is present"
        )
    if bit_depth != 8:
        problems.append(f"PNG bit depth {bit_depth} is not 8")
    if width <= 0 or height <= 0:
        raise ValueError(f"_decode_output: the PNG declares an empty canvas {width}x{height}")

    with Image.open(io.BytesIO(flat_png)) as opened:
        opened.load()
        if opened.mode != "RGB":
            problems.append(f"decoded mode {opened.mode!r} is not RGB")
        decoded = np.array(opened.convert("RGB"), dtype=np.uint8)
    return decoded, "; ".join(problems), (width, height)


# ---------------------------------------------------------------------------------------------
# run / summarise / render
# ---------------------------------------------------------------------------------------------
def run(fixture_dir: Path = DEFAULT_FIXTURE_DIR) -> tuple[list[CaseScore], dict]:
    """Measure every fixture case that records `source.rectify_inputs`.

    **Raises** on a broken fixture or a case with no `rectify_inputs` rather than skipping it: a
    metric that quietly drops a case has a different denominator from the one it reports (the
    same rule `evals/metrics/prettify.py::run()` states). The denominator here is the 9
    photographs the E2-T2 fixtures were built from — no new binaries, per ADR-011 §6.
    """
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    cases = manifest["cases"] if isinstance(manifest, dict) else manifest

    # Imported here, not at module scope: `reference_render` must stay structurally independent
    # of the stage (see its docstring), and keeping the only call into the stage inside `run()`
    # makes that checkable by reading one function.
    from packages.vision.rectify import rectify

    scores: list[CaseScore] = []
    inputs_seen: list[dict] = []
    for case in cases:
        inputs = case.get("source", {}).get("rectify_inputs")
        if not inputs:
            raise ValueError(
                f"rectify metric: case {case.get('id')!r} records no source.rectify_inputs, so "
                "it cannot be scored — dropping it would change the denominator this metric "
                "reports"
            )
        for key in ("photo", "alpha", "category"):
            if key not in inputs:
                raise ValueError(f"rectify metric: case {case['id']!r} lacks rectify_inputs.{key}")

        photo_path = ROOT / inputs["photo"]
        alpha_path = ROOT / inputs["alpha"]
        started = time.perf_counter()
        photo_bytes = photo_path.read_bytes()
        alpha_bytes = alpha_path.read_bytes()
        photo = _decode_photo(photo_bytes, photo_path)
        alpha = _decode_alpha(alpha_bytes, alpha_path)

        result = rectify(photo_bytes, alpha_bytes, inputs["category"])
        elapsed = time.perf_counter() - started
        score = score_output(
            photo,
            alpha,
            inputs["category"],
            result.flat_png,
            result.homography,
            case_id=str(case["id"]),
            seconds=elapsed,
        )
        scores.append(score)
        inputs_seen.append(
            {
                "case": case["id"],
                "photo": inputs["photo"],
                "alpha": inputs["alpha"],
                "category": inputs["category"],
                "quad_source": result.meta.notes.get("quad_source"),
                "contact_shadow": result.meta.notes.get("contact_shadow"),
            }
        )

    meta = {
        "n": len(scores),
        "fixture_dir": str(fixture_dir),
        "categories": sorted({score.category for score in scores}),
        "canvases": sorted({score.canvas_size for score in scores}),
        "quad_sources": sorted({str(entry["quad_source"]) for entry in inputs_seen}),
        "contact_shadow": sorted({str(entry["contact_shadow"]) for entry in inputs_seen}),
        "inputs": inputs_seen,
        "honesty": manifest.get("honesty", "") if isinstance(manifest, dict) else "",
    }
    return scores, meta


def _decode_photo(payload: bytes, path: Path) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            return np.array(opened.convert("RGB"), dtype=np.uint8)
    except Exception as exc:  # noqa: BLE001 - any failure is "this fixture cannot be read"
        raise ValueError(f"rectify metric: could not decode photo {path} ({exc})") from exc


def _decode_alpha(payload: bytes, path: Path) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            opened.load()
            channel = (
                opened.getchannel("A")
                if opened.mode in ("RGBA", "LA", "PA")
                else opened.convert("L")
            )
            return np.array(channel, dtype=np.uint8)
    except Exception as exc:  # noqa: BLE001 - any failure is "this fixture cannot be read"
        raise ValueError(f"rectify metric: could not decode alpha {path} ({exc})") from exc


def summarise(scores: list[CaseScore]) -> dict:
    """Aggregates over the measured cases, with the failure counts broken out per check.

    A `0.0` stays in the mean: the filter is `measured and isfinite(...)`, never truthiness —
    that was the bug QA found in prettify, where two 0.0 rows and one 9.0 row reported 9.0.
    """
    measured = [s for s in scores if s.measured and np.isfinite(s.ssim_weighted)]
    values = np.asarray([s.ssim_weighted for s in measured], dtype=np.float64)
    binaries = np.asarray(
        [s.ssim_binary for s in measured if np.isfinite(s.ssim_binary)], dtype=np.float64
    )
    gaps = np.asarray(
        [s.weighted_binary_gap for s in measured if np.isfinite(s.weighted_binary_gap)],
        dtype=np.float64,
    )
    fractions = np.asarray([s.weight_fraction for s in measured], dtype=np.float64)
    deltas = np.asarray(
        [s.mean_abs_delta_in_mask for s in measured if np.isfinite(s.mean_abs_delta_in_mask)],
        dtype=np.float64,
    )
    seconds = [s.seconds for s in scores]
    return {
        "n": len(scores),
        "n_measured": len(measured),
        "n_passes_dod": sum(1 for s in scores if s.passes_dod),
        "n_corner_failures": sum(1 for s in measured if not s.corners_ok),
        "n_determinant_failures": sum(1 for s in measured if not s.determinant_ok),
        "n_canvas_failures": sum(1 for s in measured if not s.canvas_ok),
        "n_ssim_failures": sum(1 for s in measured if s.ssim_weighted < DOD_MIN_SSIM),
        "n_unmeasured": sum(1 for s in scores if not s.measured),
        "mean": round(float(values.mean()), 4) if values.size else None,
        "median": round(float(np.median(values)), 4) if values.size else None,
        "worst": round(float(values.min()), 4) if values.size else None,
        "best": round(float(values.max()), 4) if values.size else None,
        "mean_binary": round(float(binaries.mean()), 4) if binaries.size else None,
        "max_gap": round(float(gaps.max()), 4) if gaps.size else None,
        "mean_gap": round(float(gaps.mean()), 4) if gaps.size else None,
        "min_weight_fraction": round(float(fractions.min()), 4) if fractions.size else None,
        "max_corner_residual_px": (
            round(max(s.corner_residual_px for s in measured), 6) if measured else None
        ),
        "mean_abs_delta_in_mask": round(float(deltas.mean()), 3) if deltas.size else None,
        "seconds_total": round(sum(seconds), 2),
        "seconds_mean": round(sum(seconds) / len(seconds), 3) if seconds else None,
    }


def render(
    scores: list[CaseScore], meta: dict, stats: dict, threshold: float = DOD_MIN_SSIM
) -> str:
    """Plain text report: per-case table, summary, and the caveats that must travel with it."""
    lines = [
        "# rectify measurement — E2-T1, defined by ADR-011",
        "",
        f"fixtures: n={meta['n']} · categories: {', '.join(meta['categories'])} · "
        f"canvases: {', '.join(f'{w}x{h}' for w, h in meta['canvases'])} · "
        f"quad source(s): {', '.join(meta['quad_sources']) or 'n/a'} · "
        f"contact shadow: {', '.join(meta['contact_shadow']) or 'n/a'}",
        f"wall clock: {stats['seconds_total']:.2f} s total / "
        f"{stats['seconds_mean']:.3f} s mean (rectify + reference render + SSIM)",
        "",
        "**Part A** compares the stage output against a reference re-rendered by *Pillow's C "
        "resampler* under the homography the stage declared, in the canvas frame, with the "
        "warped alpha as a **continuous weight** (no threshold, no erosion). `1.0` = every "
        "garment pixel is that garment pixel of the photograph, transported by the declared "
        "map; below 0.85 means more than 15 % of the garment's structure was replaced or "
        "mis-transported. It is a transport budget, not a correction score.",
        "",
        "**Part B1** pins all 8 degrees of freedom of H: the alpha bbox quad must land on the "
        "canonical rectangle within 0.5 px, corner order included, with det(H) > 0. "
        "**Part B2** pins the canvas: the transcribed aspect table at long edge 1200 within "
        "1 px, long edge <= 1600, 8-bit truecolour PNG, no alpha band.",
        "",
        "`ssim whole canvas` is evidence only. ADR-011 R2 rejects it as the number: the "
        "backdrop is most of the canvas, so it dilutes the score and makes it a function of "
        "garment size. `binary` is prettify's `alpha >= 128` convention, also evidence.",
        "",
        "| case | category | canvas | weighted SSIM | binary SSIM | whole canvas | gap "
        "| weight cov | worst corner px | det | B1 | B2 | DoD | s |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for score in scores:
        weighted = "UNMEASURED" if not score.measured else f"{score.ssim_weighted:.4f}"
        binary = _number(score.ssim_binary)
        whole = _number(score.ssim_whole_canvas)
        gap = _number(score.weighted_binary_gap)
        corner = (
            "n/a"
            if not math.isfinite(score.corner_residual_px)
            else f"{score.corner_residual_px:.4f}"
        )
        det = "n/a" if not math.isfinite(score.determinant) else f"{score.determinant:.3f}"
        lines.append(
            f"| {score.case_id} | {score.category} | "
            f"{score.canvas_size[0]}x{score.canvas_size[1]} | {weighted} | {binary} | {whole} | "
            f"{gap} | {score.weight_fraction:.3f} | {corner} | {det} | "
            f"{_mark(score.corners_ok)} | {_mark(score.canvas_ok)} | "
            f"{_mark(score.passes_dod)} | {score.seconds:.3f} |"
        )
    for score in scores:
        if score.unmeasured_reason:
            lines.append(f"\n- `{score.case_id}` UNMEASURED — {score.unmeasured_reason}")
        if score.quad_anomaly:
            lines.append(f"\n- `{score.case_id}` fixture anomaly — {score.quad_anomaly}")
        if score.canvas_reason:
            lines.append(f"\n- `{score.case_id}` canvas — {score.canvas_reason}")
    lines += [
        "",
        "| summary | value |",
        "|---|---|",
        f"| mean weighted SSIM (n={stats['n_measured']} of {stats['n']}) | {stats['mean']} |",
        f"| median / worst / best | {stats['median']} / {stats['worst']} / {stats['best']} |",
        f"| mean binary SSIM (evidence, not the gate) | {stats['mean_binary']} |",
        f"| weighted−binary gap, mean / worst (D13's soft signal) | "
        f"{stats['mean_gap']} / {stats['max_gap']} |",
        f"| smallest alpha weight fraction | {stats['min_weight_fraction']} |",
        f"| worst B1 corner residual — tolerance {CORNER_TOLERANCE_PX} px | "
        f"{stats['max_corner_residual_px']} |",
        f"| mean abs luminance delta where alpha >= 0.5, 8-bit levels | "
        f"{stats['mean_abs_delta_in_mask']} |",
        f"| cases passing `passes_dod` (A >= {threshold:.2f} and B1 and B2) | "
        f"{stats['n_passes_dod']} / {stats['n']} |",
        f"| failures: SSIM / corners / determinant / canvas | "
        f"{stats['n_ssim_failures']} / {stats['n_corner_failures']} / "
        f"{stats['n_determinant_failures']} / {stats['n_canvas_failures']} |",
        f"| UNMEASURED cases | {stats['n_unmeasured']} |",
        "",
        "## Definition and status",
        "",
        f"**Defined by:** {DEFINITION_SOURCE}.",
        "",
        f"**Status:** {PROPOSAL_STATUS}.",
        "",
        f"**{CAPABILITY_LIMITS}**.",
        "",
        "**The ceiling is not 1.0, and the distance from it is not a defect.** Pillow's "
        "`Image.transform(PERSPECTIVE, BILINEAR)` is the *antialiasing* resampler — the same "
        "machinery as `Image.resize` — while the stage point-samples a 2-tap bilinear. ADR-011 "
        "§7 predicted a ±1 level floor and an achievable maximum of ≈0.999; **the measurement "
        "refutes both for any non-unit scale**. On `logo-02-breeches-waistband` inside the "
        "warped matte (>= 0.5), in 8-bit levels: per-channel `max|Δ| = 33`, luminance "
        "`max|Δ| = 32`, luminance `mean|Δ| = 2.47`, 99.9th percentile `= 16`. "
        "The ±1 figure holds only where the map is 1:1 (0 on an identity checkerboard, 1 level "
        "on a sub-pixel translation), which is why the identity control passes and the corpus "
        "sits at ~0.98.",
        "",
        "**What that rewards is *agreement with Pillow's kernel*, not smoothness.** Rendering "
        "the same photograph through `Image.transform` itself scores `1.0000` on `logo-02`, "
        "where the current 2-tap bilinear stage scores `0.9694` — the 0.03 is a filter "
        "difference, not a defect. Smoothness is not rewarded: blurring the stage output only "
        "lowers the score (`logo-02` `0.9694 -> 0.9172 -> 0.6727 -> 0.5250` at Gaussian radius "
        "0.6 / 1.2 / 2.0; `logo-01` `0.9942 -> 0.9882 -> 0.9351 -> 0.8639`). The corollary is "
        "the one to watch when a corner detector lands: a stage must **not** be written to "
        "imitate this reference's filter — if kernel agreement ever matters enough to fix, the "
        "honest fix is to render both operands through the same kernel and say the number "
        "moved, not to change the stage.",
        "",
        "**`>= 0.85` is a transport budget, not proof that nothing was repainted — and the "
        "tolerance is not a percentage.** It depends on where the damage sits and on the "
        "garment's local structure. Measured in the **canvas frame** (the frame that is scored), "
        "scattered `(128,128,128)`, seed 0, lossless PNG:",
        "",
        "| case | baseline | 2 % | 5 % | 7 % | 10 % |",
        "|---|---|---|---|---|---|",
        "| `logo-02-breeches-waistband` | 0.9694 | 0.9299 passes | 0.8796 passes | 0.8492 "
        "**fails** | 0.8086 **fails** |",
        "| `logo-01-tee-chest` | 0.9942 | 0.6488 **fails** | — | — | — |",
        "| `logo-05-jacket-emblem` | 0.9962 | 0.8060 **fails** | — | — | — |",
        "",
        "So `passes_dod` means \"at most ~15 % of the garment's *structure* was replaced or "
        "mis-transported, and the declared map and the canvas conform\" — never \"nothing was "
        "repainted\", and never \"10 % is allowed\". Five per cent survives on one garment and "
        "two per cent kills another. An earlier version of this note claimed '~10 % still "
        "passes'; that number came from scattering in the **source** frame, where the "
        "`1920x1280 -> 1200x960` downsample averages the speckle away and only ~0.22 % of the "
        "canvas ends up grey. It does not hold. E2-T1's stage has no generative step at all, so "
        "none of this is a hole the metric opened — it is the arithmetic of the threshold.",
        "",
        "**`weighted` and `binary` coincide on this fixture set** (gap 0.0000): the 9 alphas are "
        "algorithmic and hard-edged, so every pixel is 0 or 255 and the continuous weight "
        "selects the same pixels as the `alpha >= 128` closure. ADR-011 §2.2's argument for "
        "alpha weighting is therefore **not exercised by these fixtures**, and D13's "
        "weighted-vs-binary signal is inert here; it only becomes live on a soft matte, which "
        "`evals/tests/test_rectify_metric.py::test_D13_...` builds synthetically.",
        "",
        "**Also not proven:** the rim (alpha weighting suppresses but does not remove the "
        "silhouette seam, and the 2 px composite edge is pinned by unit test, not here); "
        "content pasted *outside* the reference mask (weight 0 there, apart from an 11x11 "
        "window's worth of leak at the silhouette); the contact shadow, which nothing here "
        f"would notice either way; and wall clock, which is evidence only — this row gates "
        f"nothing on seconds. {CANVAS_CAVEAT}.",
        "",
        f"DoD threshold for this row: weighted SSIM >= {threshold:.2f}, plus the two exact "
        "conformance checks. The threshold was not moved to make this row green.",
        "",
    ]
    return "\n".join(lines)


def _mark(value: bool) -> str:
    return "ok" if value else "**FAIL**"


def _number(value: float) -> str:
    """Four decimals, or `n/a` for a NaN — a missing number is never printed as 0.0000."""
    return "n/a" if not np.isfinite(value) else f"{value:.4f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.metrics.rectify")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_REPORT, help="report path (default evals/reports)"
    )
    parser.add_argument("--no-report", action="store_true", help="print only, write no file")
    parser.add_argument("--threshold", type=float, default=DOD_MIN_SSIM, help="DoD line to quote")
    args = parser.parse_args(argv)

    if not (args.fixture_dir / "manifest.json").is_file():
        print(f"no manifest.json in {args.fixture_dir}", file=sys.stderr)
        return 2
    try:
        scores, meta = run(args.fixture_dir)
    except Exception as exc:  # noqa: BLE001 - name the reason instead of printing a fake table
        print(f"RECTIFY METRIC UNAVAILABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    stats = summarise(scores)
    report = render(scores, meta, stats, args.threshold)
    print(report)
    if not args.no_report:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        try:
            shown = args.out.relative_to(ROOT)
        except ValueError:
            shown = args.out
        print(f"wrote {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
