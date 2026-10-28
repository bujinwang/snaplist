"""STAGE 2 — rectify + flatten. Owner: CV role (E2, ticket E2-T1).

## What is implemented here, and what is not

**Implemented (pure arithmetic, no model, no network, no image):**

* `solve_homography(src, dst)` — the 4-point planar homography from `docs/PLAN.md` E2-T1, solved as
  an 8×8 linear system (DLT with `h33 = 1`) by Gaussian elimination with partial pivoting.
* `apply_homography` / `invert_homography` — forward and inverse mapping in homogeneous
  coordinates, with an explicit refusal when a point maps to infinity.
* `canonical_size` / `target_rect` / `homography_for_quad` — the target flat-lay rectangle per
  category, and the composed matrix that takes a photo quad onto it.
* `clamp_to_frame` — keeps a mapped point inside the output image, so a quad that overhangs the
  source cannot produce out-of-range pixel coordinates for the warp.

**Not implemented:** decoding the JPEG, reading the alpha PNG, resampling pixels, compositing onto
`#F2F1EE` and the contact shadow. `rectify()` still raises `StageNotImplemented`, because those
steps need a codec (Pillow/OpenCV) and real fixtures, and nothing in this environment can verify
them. The maths above is what the eventual wrapper calls; it is deliberately dependency-free so it
can be verified today.

## Why no numpy

`numpy` is not installed in this repo's virtualenv, and adding it would change `pyproject.toml` and
`docs/DEPS.md`, neither of which this role owns. An 8×8 solve in pure Python is ~40 lines, has no
licence, no wheel, no ABI and no import cost, and is unit-tested to 1e-9 against analytically known
matrices. If a later ticket brings numpy in for the pixel work, `_solve_linear` can be swapped for
`numpy.linalg.solve` without touching a caller — the function boundaries are already the right ones.

## Constraints that stay true when the pixel stages land

* **no generative repainting in this module** — garment pixels must survive the warp, SSIM ≥ 0.85
  inside the mask versus the source (prompts/02-vision.md)
* output is sRGB PNG, ≤ 1600 px on the long edge, backdrop `#F2F1EE`

    rectify(image: bytes, alpha_png: bytes, category: str) -> RectifyResult
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from packages.vision import StageNotImplemented
from packages.vision.types import StageMeta

# A quad smaller than this (in pixels²) is a mis-tap, not a garment.
MIN_QUAD_AREA = 1.0
# Determinant below which we declare the system singular instead of returning -0.0 noise.
SINGULAR_EPSILON = 1e-12
# Homogeneous w below which a point is treated as mapped to infinity.
INFINITY_EPSILON = 1e-12
# Long edge of the canonical flat-lay, per BRIEF.md ("<=1600px").
DEFAULT_LONG_EDGE_PX = 1200

Point = tuple[float, float]
Homography = tuple[float, float, float, float, float, float, float, float, float]
Quad = tuple[Point, Point, Point, Point]

IDENTITY_HOMOGRAPHY: Homography = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

# Flat-lay aspect (width / height) per category. These are **design targets, not measurements**:
# they are chosen so a tee, a jean and a shoe each fill the frame without letterboxing. E2-T1's
# eval compares real output against these, and the ratio is recorded here so the report can cite
# the number it was scored against.
CANONICAL_ASPECT: dict[str, float] = {
    "top": 4 / 5,
    "bottom": 4 / 5,
    "dress": 3 / 4,
    "outerwear": 4 / 5,
    "shoe": 4 / 3,
    "accessory": 1.0,
    "other": 4 / 5,
}


class DegenerateQuadError(ValueError):
    """The four points cannot define a homography (collinear, duplicated, or non-finite)."""


@dataclass(slots=True)
class RectifyResult:
    flat_png: bytes  # canonical flat-lay, sRGB, <=1600px
    homography: tuple[float, ...]  # 9 values, row-major — kept for debugging/eval overlays
    meta: StageMeta


# ---------------------------------------------------------------------------------------
# Small linear-algebra helpers (pure python, float64)
# ---------------------------------------------------------------------------------------
def _solve_linear(matrix: Sequence[Sequence[float]], rhs: Sequence[float]) -> list[float]:
    """Solve `A x = b` for a square system by Gaussian elimination with partial pivoting.

    Raises `DegenerateQuadError` when the matrix is singular (the four points are collinear or two
    of them coincide). Partial pivoting matters even at n=8: a quad whose x-values are all small
    produces a badly scaled column, and an unpivoted elimination loses the solution to rounding.
    """
    size = len(rhs)
    if size == 0 or len(matrix) != size:
        raise ValueError("matrix must be square and match rhs")
    augmented = [list(row) + [float(rhs[index])] for index, row in enumerate(matrix)]

    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot_row][column]) < SINGULAR_EPSILON:
            raise DegenerateQuadError(
                f"homography system is singular at column {column} "
                "(collinear or duplicated points)"
            )
        if pivot_row != column:
            augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]

        pivot = augmented[column][column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / pivot
            if factor == 0.0:
                continue
            for index in range(column, size + 1):
                augmented[row][index] -= factor * augmented[column][index]

    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        total = augmented[row][size]
        for column in range(row + 1, size):
            total -= augmented[row][column] * solution[column]
        solution[row] = total / augmented[row][row]
    return solution


def quad_area(points: Sequence[Point]) -> float:
    """Shoelace area. Absolute, so corner order (CW or CCW) does not matter."""
    total = 0.0
    for index, (x, y) in enumerate(points):
        next_x, next_y = points[(index + 1) % len(points)]
        total += x * next_y - next_x * y
    return abs(total) / 2.0


def _validate_quad(points: Sequence[Point], label: str) -> Quad:
    if len(points) != 4:
        raise DegenerateQuadError(f"{label} needs exactly 4 points, got {len(points)}")
    cleaned: list[Point] = []
    for x, y in points:
        if not (math.isfinite(x) and math.isfinite(y)):
            raise DegenerateQuadError(f"{label} contains a non-finite coordinate")
        cleaned.append((float(x), float(y)))
    if quad_area(cleaned) < MIN_QUAD_AREA:
        raise DegenerateQuadError(f"{label} has near-zero area; the corners are collinear")
    return (cleaned[0], cleaned[1], cleaned[2], cleaned[3])


# ---------------------------------------------------------------------------------------
# Homography
# ---------------------------------------------------------------------------------------
def solve_homography(src: Sequence[Point], dst: Sequence[Point]) -> Homography:
    """The 3×3 matrix mapping `src` onto `dst`, row-major, normalised so `h33 = 1`.

    Each correspondence `(x, y) -> (u, v)` contributes two rows:

        x·h11 + y·h12 + h13 − u·x·h31 − u·y·h32 = u
        x·h21 + y·h22 + h23 − v·x·h31 − v·y·h32 = v

    With four correspondences that is the 8×8 system `_solve_linear` solves. Fixing `h33 = 1` is
    valid for every real quad (the alternative is `h33 = 0`, a homography that sends the whole
    plane onto a line — not a rectangle we can warp to).
    """
    source = _validate_quad(src, "src")
    target = _validate_quad(dst, "dst")

    matrix: list[list[float]] = []
    rhs: list[float] = []
    for (x, y), (u, v) in zip(source, target, strict=True):
        matrix.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        rhs.append(u)
        matrix.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        rhs.append(v)

    solved = _solve_linear(matrix, rhs)
    return (
        solved[0],
        solved[1],
        solved[2],
        solved[3],
        solved[4],
        solved[5],
        solved[6],
        solved[7],
        1.0,
    )


def apply_homography(matrix: Sequence[float], point: Point) -> Point:
    """Map one point. Refuses a point that maps to infinity instead of returning `inf`."""
    if len(matrix) != 9:
        raise ValueError("a homography has exactly 9 values")
    x, y = point
    a, b, c, d, e, f, g, h, i = (float(value) for value in matrix)
    w = g * x + h * y + i
    if abs(w) < INFINITY_EPSILON:
        raise DegenerateQuadError(f"point {point} maps to infinity under this homography")
    return ((a * x + b * y + c) / w, (d * x + e * y + f) / w)


def determinant(matrix: Sequence[float]) -> float:
    """Determinant of a row-major 3×3."""
    a, b, c, d, e, f, g, h, i = (float(value) for value in matrix)
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def invert_homography(matrix: Sequence[float]) -> Homography:
    """The inverse via the adjugate. Raises when the matrix is not invertible."""
    a, b, c, d, e, f, g, h, i = (float(value) for value in matrix)
    det = determinant(matrix)
    if abs(det) < SINGULAR_EPSILON:
        raise DegenerateQuadError("homography is not invertible (determinant ~ 0)")
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
    return (
        adjugate[0] / det,
        adjugate[1] / det,
        adjugate[2] / det,
        adjugate[3] / det,
        adjugate[4] / det,
        adjugate[5] / det,
        adjugate[6] / det,
        adjugate[7] / det,
        adjugate[8] / det,
    )


# ---------------------------------------------------------------------------------------
# Target rectangle
# ---------------------------------------------------------------------------------------
def canonical_size(category: str, long_edge_px: int = DEFAULT_LONG_EDGE_PX) -> tuple[int, int]:
    """The flat-lay canvas for `category`, long edge pinned to `long_edge_px`.

    Unknown categories fall back to `other` rather than raising: a category the VLM invented must
    not crash the pipeline, and `other`'s 4:5 is the neutral choice.
    """
    if long_edge_px <= 0:
        raise ValueError("long_edge_px must be positive")
    aspect = CANONICAL_ASPECT.get(category, CANONICAL_ASPECT["other"])
    if aspect >= 1.0:
        return long_edge_px, max(int(round(long_edge_px / aspect)), 1)
    return max(int(round(long_edge_px * aspect)), 1), long_edge_px


def target_rect(width: int, height: int) -> Quad:
    """The four corners of the target canvas, clockwise from the top-left, as floats."""
    if width <= 0 or height <= 0:
        raise ValueError("target rectangle must have positive extent")
    return (
        (0.0, 0.0),
        (float(width), 0.0),
        (float(width), float(height)),
        (0.0, float(height)),
    )


def homography_for_quad(
    quad: Sequence[Point],
    category: str,
    long_edge_px: int = DEFAULT_LONG_EDGE_PX,
) -> tuple[Homography, int, int]:
    """Compose the garment quad onto the canonical canvas for `category`.

    Corner order is the caller's contract: `quad` must be top-left, top-right, bottom-right,
    bottom-left **of the garment**, matching `target_rect`'s order. A rotated quad is fine; a
    shuffled one produces a mirrored or twisted warp, which is why every eval overlay draws the
    numbered corners.
    """
    width, height = canonical_size(category, long_edge_px)
    matrix = solve_homography(_validate_quad(quad, "quad"), target_rect(width, height))
    return matrix, width, height


def clamp_to_frame(point: Point, width: int, height: int) -> Point:
    """Clamp to the pixel grid `[0, width-1] × [0, height-1]`.

    A quad that overhangs the photo (common: a hanger or a hand in frame) can map a corner to a
    negative coordinate. Bilinear sampling at -0.4 returns garbage or raises depending on the
    backend; clamping keeps the warp defined and is what the SSIM comparison will score.
    """
    if width <= 0 or height <= 0:
        raise ValueError("frame must have positive extent")
    max_x = float(max(width - 1, 0))
    max_y = float(max(height - 1, 0))
    return (min(max(point[0], 0.0), max_x), min(max(point[1], 0.0), max_y))


def warp_quad(
    quad: Sequence[Point],
    category: str,
    long_edge_px: int = DEFAULT_LONG_EDGE_PX,
) -> tuple[list[Point], Homography, int, int]:
    """Map a quad's own corners through the composed homography and clamp them into the canvas.

    Returns `(mapped_corners, homography, width, height)`. For a well-formed quad the mapped corners
    are exactly `target_rect` (up to rounding), so this doubles as the cheapest self-check in a
    test: any drift means the solve is wrong.
    """
    matrix, width, height = homography_for_quad(quad, category, long_edge_px)
    mapped = [clamp_to_frame(apply_homography(matrix, corner), width, height) for corner in quad]
    return mapped, matrix, width, height


def rectify(image: bytes, alpha_png: bytes, category: str) -> RectifyResult:
    """Not implemented: the geometry above is ready, the pixel pipeline is not.

    What is missing is everything that touches an image: JPEG decode, alpha-PNG read, bilinear
    resample of the garment under `homography_for_quad`, composite onto `#F2F1EE`, contact shadow.
    Those need a codec (Pillow/OpenCV) and real fixtures, and E2-T3's SSIM comparison is what would
    prove them — returning the input bytes and calling it "rectified" would make the eval report a
    number for work that never ran.
    """
    raise StageNotImplemented(
        "packages.vision.rectify.rectify — pixel pipeline missing (E2-T1). The homography maths in "
        "this module IS implemented and tested: solve_homography, homography_for_quad, "
        "clamp_to_frame."
    )
