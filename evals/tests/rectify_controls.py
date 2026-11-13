"""Synthetic control cases for `evals/metrics/rectify.py` — ADR-011 §4 and §7.

No fixture files, no stage code, no network: every control is built from arithmetic, so the
kill matrix can score outputs **a wrong implementation would have produced** through the same
`score_output()` the real 9 photographs go through. A metric whose only entry point is "run the
stage" can only ever be tested against correct stages, which is how the prettify metric shipped
a comparison of the output with itself while 449 tests were green.

Two things live here and nothing else does:

1. **Builders** — a photograph with a known garment quad, its hard-edged alpha, the documented
   homography for the canvas, and the output a correct stage would produce.
2. **`warp()`** — an independent inverse-map + bilinear/nearest resample + composite, written
   from the formula. It is the numeric *twin* of the stage and of `reference_render()`, and
   being a third implementation is the point: `test_reference_resampler_is_not_the_stage_
   resampler` compares Pillow's C resampler against this one and requires a non-zero difference
   on a sub-pixel shift. If the metric ever delegates its reference to the stage's `_bilinear`,
   that difference collapses to exactly 0 and the test goes red.

`warp()` deliberately duplicates the stage's clamping-of-source-coordinates rule (drawn from the
formula, not imported): a control that overhangs the frame must still produce a defined image.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

BACKDROP_RGB: tuple[int, int, int] = (242, 241, 238)

Quad = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]


# ---------------------------------------------------------------------------------- textures
def checkerboard(
    width: int, height: int, cell: int = 8, low: int = 0, high: int = 255
) -> np.ndarray:
    """A hard-edged periodic field (`cell` px squares), RGB uint8.

    Periodic so a wrap-around bug cannot hide, hard-edged so a half-pixel disagreement between
    two resamplers shows up immediately, and 1:1 with the pixel grid so an identity homography
    samples integer coordinates on both sides. The channels are deliberately **not** equal: the
    red band runs at four times the cell size, so an RGB channel swap is visible in the
    structure and not only in the hue.
    """
    ys, xs = np.mgrid[0:height, 0:width]
    squares = (((xs // cell) + (ys // cell)) % 2).astype(np.float64)
    values = low + squares * (high - low)
    stripes = ((xs // (cell * 4)) % 2).astype(np.float64)
    red = np.clip(values * 0.75 + stripes * (high - low) * 0.25, 0, 255)
    green = values
    blue = np.clip(values * 0.9 + (1.0 - squares) * (high - low) * 0.1, 0, 255)
    return np.dstack([red, green, blue]).round().clip(0, 255).astype(np.uint8)


def noisy_texture(width: int, height: int, seed: int = 20261112) -> np.ndarray:
    """Independent per-pixel RGB noise, seeded.

    Maximum spatial frequency, so a one-pixel misregistration, a nearest-neighbour resample or
    a repaint decorrelates the comparison immediately. Seeded: a flaky control is worse than no
    control.
    """
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------------- geometry
def solve_homography(src: Quad, dst: Quad) -> tuple[float, ...]:
    """The 4-point planar homography `src -> dst`, row-major, normalised so `h33 = 1`.

    An independent least-squares solve (numpy, 8x8) rather than the stage's Gaussian
    elimination: a control that borrows the stage's solver cannot distinguish "the stage solved
    the documented map" from "the control and the stage made the same mistake".
    """
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        rhs.append(v)
    solution, *_ = np.linalg.lstsq(np.asarray(rows, float), np.asarray(rhs, float), rcond=None)
    return tuple(float(value) for value in solution) + (1.0,)


def target_rect(width: int, height: int) -> Quad:
    """(TL, TR, BR, BL) of a canvas, clockwise from the top-left."""
    return (
        (0.0, 0.0),
        (float(width), 0.0),
        (float(width), float(height)),
        (0.0, float(height)),
    )


def apply_homography(matrix: tuple[float, ...], point: tuple[float, float]):
    a, b, c, d, e, f, g, h, i = matrix
    x, y = point
    w = g * x + h * y + i
    return ((a * x + b * y + c) / w, (d * x + e * y + f) / w)


def translate(homography: tuple[float, ...], dx: float, dy: float) -> tuple[float, ...]:
    """`T · H` — the same photo map, followed by a canvas-space translation of `(dx, dy)`."""
    shift = (1.0, 0.0, dx, 0.0, 1.0, dy, 0.0, 0.0, 1.0)
    return _compose(shift, homography)


def scale(homography: tuple[float, ...], factor: float) -> tuple[float, ...]:
    """`S · H` — the same photo map, followed by a canvas-space scale about the top-left."""
    growing = (factor, 0.0, 0.0, 0.0, factor, 0.0, 0.0, 0.0, 1.0)
    return _compose(growing, homography)


def mirror(homography: tuple[float, ...], width: int) -> tuple[float, ...]:
    """`M · H` — the same photo map, flipped horizontally about the canvas' vertical axis.

    A negative determinant: a mirrored garment is a bug, always (ADR-011 §2.3 B1).
    """
    flipping = (-1.0, 0.0, float(width), 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    return _compose(flipping, homography)


def _compose(outer: tuple[float, ...], inner: tuple[float, ...]) -> tuple[float, ...]:
    product = np.asarray(outer, float).reshape(3, 3) @ np.asarray(inner, float).reshape(3, 3)
    if abs(product[2, 2]) < 1e-12:
        return tuple(float(value) for value in product.reshape(9))
    return tuple(float(value) for value in (product / product[2, 2]).reshape(9))


# ------------------------------------------------------------------------------------ resample
def sample_grid(
    source: np.ndarray,
    homography: tuple[float, ...],
    canvas: tuple[int, int],
    *,
    resampler: str = "bilinear",
) -> np.ndarray:
    """Inverse-map a 2-D channel through `homography` onto `canvas`, as float values.

    The resampling half of `warp()`, split out so `matte_of()` can sample an alpha matte
    *without* compositing it — a composite destroys the very weights the metric is about (the
    backdrop is 242, not 0, so reading the composite's first channel returns 0.95 wherever the
    matte is 0). `resampler` is `"bilinear"` (correct), `"nearest"` (D9's wrong resampler) or
    `"swapped"` (D9's fx/fy swap).
    """
    if source.ndim != 2:
        raise ValueError(f"sample_grid: source must be 2-D, got shape {source.shape}")
    width, height = canvas
    inverse = np.linalg.inv(np.asarray(homography, float).reshape(3, 3))
    inverse = inverse / inverse[2, 2]
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    denominator = inverse[2, 0] * xs + inverse[2, 1] * ys + inverse[2, 2]
    sx = (inverse[0, 0] * xs + inverse[0, 1] * ys + inverse[0, 2]) / denominator
    sy = (inverse[1, 0] * xs + inverse[1, 1] * ys + inverse[1, 2]) / denominator
    if resampler == "swapped":
        sx, sy = sy, sx
    source_height, source_width = source.shape

    if resampler == "nearest":
        xi = np.clip(np.rint(sx).astype(np.int64), 0, source_width - 1)
        yi = np.clip(np.rint(sy).astype(np.int64), 0, source_height - 1)
        return source[yi, xi].astype(np.float64)

    x = np.clip(sx, 0.0, float(source_width - 1))
    y = np.clip(sy, 0.0, float(source_height - 1))
    x0 = np.clip(np.floor(x).astype(np.int64), 0, source_width - 1)
    y0 = np.clip(np.floor(y).astype(np.int64), 0, source_height - 1)
    x1 = np.minimum(x0 + 1, source_width - 1)
    y1 = np.minimum(y0 + 1, source_height - 1)
    fx = x - x0
    fy = y - y0
    top = source[y0, x0].astype(np.float64) * (1.0 - fx) + source[y0, x1].astype(np.float64) * fx
    bottom = (
        source[y1, x0].astype(np.float64) * (1.0 - fx) + source[y1, x1].astype(np.float64) * fx
    )
    return top * (1.0 - fy) + bottom * fy


def warp(
    photo: np.ndarray,
    alpha: np.ndarray,
    homography: tuple[float, ...],
    canvas: tuple[int, int],
    *,
    resampler: str = "bilinear",
    alpha_threshold: int | None = None,
) -> np.ndarray:
    """Inverse-map `photo`+`alpha` through `homography` onto `canvas`, composited on backdrop.

    `resampler` is `"bilinear"` (correct), `"nearest"` (D9's wrong resampler) or `"swapped"`
    (D9's fx/fy swap). `alpha_threshold` binarises the sample before compositing, which is D13:
    a stage that composites at 200/255 instead of using the continuous matte.

    Written from the formula and independent of both the stage and the metric, so agreement
    between any two of the three is evidence and not tautology.
    """
    garment = np.stack(
        [
            sample_grid(photo[:, :, index], homography, canvas, resampler=resampler)
            for index in range(3)
        ],
        axis=-1,
    )
    matte = sample_grid(alpha, homography, canvas, resampler=resampler)
    if alpha_threshold is not None:
        matte = np.where(matte >= float(alpha_threshold), 255.0, 0.0)
    matte = np.clip(matte, 0.0, 255.0) / 255.0
    backdrop = np.asarray(BACKDROP_RGB, dtype=np.float64)
    flat = garment * matte[:, :, None] + backdrop * (1.0 - matte[:, :, None])
    return np.rint(flat).clip(0, 255).astype(np.uint8)


def matte_of(
    alpha: np.ndarray, homography: tuple[float, ...], canvas: tuple[int, int]
) -> np.ndarray:
    """The warped alpha in `[0, 1]` — the weight `reference_render()` returns alongside `Ref`."""
    return np.clip(sample_grid(alpha, homography, canvas), 0.0, 255.0) / 255.0


# ------------------------------------------------------------------------------ control cases
@dataclass(frozen=True, slots=True)
class ControlCase:
    """One synthetic case: a photo, its alpha, the documented map, and a correct output."""

    photo: np.ndarray
    alpha: np.ndarray
    category: str
    canvas: tuple[int, int]
    quad: Quad
    homography: tuple[float, ...]
    flat_png: bytes
    flat: np.ndarray

    def with_output(self, flat: np.ndarray) -> bytes:
        """Encode an alternative output, so a defect can be scored through `score_output()`."""
        return encode_rgb(flat)


def encode_rgb(pixels: np.ndarray) -> bytes:
    """8-bit truecolour PNG, the same shape the stage's `_encode_rgb` produces (no ICC chunk)."""
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(pixels.astype(np.uint8))).save(buffer, format="PNG")
    return buffer.getvalue()


def encode_rgba(pixels: np.ndarray, alpha: int = 255) -> bytes:
    """A PNG **with an alpha band** — D7/B2's other failure mode.

    The DoD says "sRGB PNG ≤ 1600 px"; the stage writes 8-bit truecolour with no alpha band, and
    `_encode_rgb` writes no ICC chunk either. An RGBA output of the right size passes the corner
    check and scores full marks on Part A, so this is the only input that makes B2 fail alone.
    """
    height, width = pixels.shape[:2]
    band = np.full((height, width, 1), alpha, dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(np.concatenate([pixels.astype(np.uint8), band], axis=-1)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def encode_mask(mask: np.ndarray) -> bytes:
    """8-bit greyscale PNG for an alpha matte."""
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(mask.astype(np.uint8))).save(buffer, format="PNG")
    return buffer.getvalue()


def build_case(
    *,
    category: str = "top",
    canvas: tuple[int, int] = (960, 1200),
    photo_size: tuple[int, int] = (1100, 1300),
    box: tuple[float, float, float, float] = (101.0, 97.0, 899.0, 1143.0),
    shape: str = "rect",
    texture: str = "checker",
    seed: int = 20261112,
    feather: int = 0,
) -> ControlCase:
    """A photograph with a known garment quad, and the output a correct stage would produce.

    `photo_size` is `(width, height)`, `box` is `(left, top, right, bottom)` of the garment in
    the photograph, inclusive. The default map is a scale rather than an identity, and a
    non-integer one at that (799x1047 px onto 960x1200), which is what makes a difference
    between two resamplers measurable at all.

    `shape` is `"rect"` (the garment fills its bounding box, so the whole canvas is weighted) or
    `"ellipse"` (it does not, so there is an outside-the-mask region for D11 and for
    `test_the_mask_changes_the_denominator`). `feather > 0` ramps the alpha over that many
    pixels inside the silhouette edge, which is what gives D13's weighted-vs-binary gap
    something to measure: the 9 real fixtures' alphas are algorithmic and hard-edged, so on
    them the two region rules coincide and D13 is inert.

    The quad is **derived from the alpha that was actually built**, exactly as the stage derives
    it (`alpha`'s nonzero bounding box, TL/TR/BR/BL, inclusive maxima). A control whose declared
    map did not match its own mask would fail B1 for a reason the test is not about.
    """
    if shape not in ("rect", "ellipse"):
        raise ValueError(f"build_case: unknown shape {shape!r}")
    if texture not in ("checker", "noise"):
        raise ValueError(f"build_case: unknown texture {texture!r}")
    width, height = photo_size
    left, top, right, bottom = (float(value) for value in box)

    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    if shape == "ellipse":
        centre_x, centre_y = (left + right) / 2.0, (top + bottom) / 2.0
        radius_x, radius_y = (right - left) / 2.0, (bottom - top) / 2.0
        inside = ((xs - centre_x) / radius_x) ** 2 + ((ys - centre_y) / radius_y) ** 2 <= 1.0
    else:
        inside = (xs >= left) & (xs <= right) & (ys >= top) & (ys <= bottom)

    if feather > 0:
        distance = np.minimum.reduce([xs - left, right - xs, ys - top, bottom - ys])
        ramp = np.clip(distance / float(feather), 0.0, 1.0)
        alpha = np.where(inside, (ramp * 255.0).round(), 0.0).astype(np.uint8)
    else:
        alpha = np.where(inside, 255, 0).astype(np.uint8)

    texture_pixels = (
        checkerboard(width, height) if texture == "checker" else noisy_texture(width, height, seed)
    )
    photo = np.empty((height, width, 3), dtype=np.uint8)
    photo[:, :] = np.asarray(BACKDROP_RGB, dtype=np.uint8)
    garment = alpha > 0
    photo[garment] = texture_pixels[garment]

    quad = quad_of_alpha(alpha)
    homography = solve_homography(quad, target_rect(canvas[0], canvas[1]))
    flat = warp(photo, alpha, homography, canvas)
    return ControlCase(
        photo=photo,
        alpha=alpha,
        category=category,
        canvas=canvas,
        quad=quad,
        homography=homography,
        flat_png=encode_rgb(flat),
        flat=flat,
    )


def quad_of_alpha(alpha: np.ndarray) -> Quad:
    """(TL, TR, BR, BL) of the nonzero alpha, inclusive maxima — the stage's `alpha_bbox` rule."""
    ys, xs = np.nonzero(alpha > 0)
    if xs.size == 0:
        raise ValueError("quad_of_alpha: the alpha has no garment pixels")
    left, right = float(xs.min()), float(xs.max())
    top, bottom = float(ys.min()), float(ys.max())
    return ((left, top), (right, top), (right, bottom), (left, bottom))


def blank_canvas(canvas: tuple[int, int]) -> np.ndarray:
    """D1: a canvas with no garment on it at all — pure backdrop."""
    width, height = canvas
    return np.broadcast_to(np.asarray(BACKDROP_RGB, np.uint8), (height, width, 3)).copy()


def scatter_repaint(
    flat: np.ndarray,
    matte: np.ndarray,
    share: float,
    *,
    seed: int = 0,
    value: tuple[int, int, int] = (128, 128, 128),
) -> np.ndarray:
    """Paint `share` of the garment **in the canvas frame**, scattered, one pixel at a time.

    This is the construction behind the tolerance table in `evals/metrics/rectify.py`
    (`THRESHOLD_SEMANTICS`) and in the handoff, and it is here — in code, with the seed and the
    mask rule written down — because the first version of that table was produced from a *source
    frame* scatter and could not be reproduced from its own description. The source-frame version
    is nearly useless as an argument: these photographs are downsampled (`1920x1280 -> 1200x960`),
    so a scattered pixel in the source is averaged away by the warp and only ~0.2 % of it survives
    as grey in the canvas. The canvas is the frame that is actually scored.

    Fully specified so the numbers are reproducible: `matte >= 128/255` selects the pixels,
    `numpy.random.default_rng(seed).choice(n, size=round(share*n), replace=False)` picks them,
    they are set to `value`, and the caller encodes lossless PNG (`encode_rgb`).
    """
    if not 0.0 <= share <= 1.0:
        raise ValueError(f"scatter_repaint: share must be in [0, 1], got {share}")
    pixels = np.asarray(flat)
    if pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError(f"scatter_repaint: flat must be HxWx3, got {pixels.shape}")
    selected = np.argwhere(np.asarray(matte) >= (128.0 / 255.0))
    if selected.size == 0:
        raise ValueError("scatter_repaint: the matte selects no pixels")
    rng = np.random.default_rng(seed)
    count = int(round(share * len(selected)))
    picked = rng.choice(len(selected), size=count, replace=False)
    painted = pixels.astype(np.uint8).copy()
    painted[selected[picked, 0], selected[picked, 1]] = np.asarray(value, dtype=np.uint8)
    return painted


def repaint(case: ControlCase, share: float) -> np.ndarray:
    """D10: replace the leftmost `share` of the garment with its own mean colour.

    A flat fill keeps the local mean and destroys the local variance, which is what an
    inpainting model's output looks like to SSIM: the score must fall, and must fall
    monotonically in `share`.
    """
    if not 0.0 <= share <= 1.0:
        raise ValueError(f"repaint: share must be in [0, 1], got {share}")
    matte = matte_of(case.alpha, case.homography, case.canvas) >= 0.5
    columns = np.where(matte.any(axis=0))[0]
    if columns.size == 0:
        raise ValueError("repaint: the control case has no garment")
    cut = int(columns.min() + (columns.max() - columns.min() + 1) * share)
    painted = case.flat.copy()
    region = matte.copy()
    region[:, cut:] = False
    mean_colour = case.flat[matte].astype(np.float64).mean(axis=0)
    painted[region] = np.rint(mean_colour).astype(np.uint8)
    return painted


__all__ = [
    "BACKDROP_RGB",
    "ControlCase",
    "apply_homography",
    "blank_canvas",
    "build_case",
    "checkerboard",
    "encode_mask",
    "encode_rgba",
    "encode_rgb",
    "matte_of",
    "quad_of_alpha",
    "mirror",
    "noisy_texture",
    "repaint",
    "scatter_repaint",
    "sample_grid",
    "scale",
    "solve_homography",
    "target_rect",
    "translate",
    "warp",
]
