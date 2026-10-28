"""Geometry tests for `packages/vision/rectify.py` (E2-T1, pure-math half).

The bar in the ticket is "known quad → known matrix, round-trip error < 1e-6". These tests are
written against **analytically constructed** matrices, not against the implementation's own output:
a homography is built by hand, applied to a quad to produce a target quad, then recovered by
`solve_homography` and compared. If the solver were wrong, the recovered matrix would not match.

No image is decoded here — that is the part of E2-T1 that is still missing, and pretending otherwise
would hide it.
"""

from __future__ import annotations

import math

import pytest

from packages.vision import StageNotImplemented
from packages.vision.rectify import (
    CANONICAL_ASPECT,
    DEFAULT_LONG_EDGE_PX,
    IDENTITY_HOMOGRAPHY,
    DegenerateQuadError,
    apply_homography,
    canonical_size,
    clamp_to_frame,
    determinant,
    homography_for_quad,
    invert_homography,
    quad_area,
    solve_homography,
    target_rect,
    warp_quad,
)

UNIT_SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
TOLERANCE = 1e-9


def close(left: tuple[float, ...], right: tuple[float, ...], tol: float = TOLERANCE) -> None:
    assert len(left) == len(right)
    for index, (a, b) in enumerate(zip(left, right, strict=True)):
        assert abs(a - b) <= tol, f"value {index}: {a} != {b} (tol {tol})"


def apply(matrix, points):
    return [apply_homography(matrix, point) for point in points]


# ---------------------------------------------------------------------------------------
# Identity and similarity transforms
# ---------------------------------------------------------------------------------------
def test_identity_quad_recovers_the_identity_matrix() -> None:
    matrix = solve_homography(UNIT_SQUARE, UNIT_SQUARE)
    close(matrix, IDENTITY_HOMOGRAPHY)
    assert determinant(matrix) == pytest.approx(1.0, abs=TOLERANCE)


def test_pure_translation_and_scale() -> None:
    """src (0..1)² → dst (10..110, 20..220): scale 100, offsets (10, 20)."""
    dst = ((10.0, 20.0), (110.0, 20.0), (110.0, 220.0), (10.0, 220.0))
    matrix = solve_homography(UNIT_SQUARE, dst)
    close(matrix, (100.0, 0.0, 10.0, 0.0, 200.0, 20.0, 0.0, 0.0, 1.0))
    mapped = apply(matrix, UNIT_SQUARE)
    for got, want in zip(mapped, dst, strict=True):
        close(got, want)


# ---------------------------------------------------------------------------------------
# A known perspective matrix, recovered from its own output
# ---------------------------------------------------------------------------------------
PERSPECTIVE = (1.6, 0.35, -40.0, -0.22, 1.45, 25.0, 0.0009, -0.0011, 1.0)


def test_a_known_perspective_matrix_is_recovered_exactly() -> None:
    # Use a larger, off-origin square so the perspective terms actually dominate.
    src = ((120.0, 80.0), (640.0, 100.0), (610.0, 720.0), (90.0, 690.0))
    dst = apply(PERSPECTIVE, src)

    recovered = solve_homography(src, dst)
    close(recovered, PERSPECTIVE, tol=1e-6)

    # and the correspondences round-trip
    for got, want in zip(apply(recovered, src), dst, strict=True):
        close(got, want, tol=1e-6)


def test_round_trip_error_stays_below_the_ticket_threshold_on_hostile_quads() -> None:
    """Five skewed quads including a strongly foreshortened one. Error must stay < 1e-6 px."""
    quads = [
        ((0.0, 0.0), (800.0, 40.0), (760.0, 1180.0), (30.0, 1200.0)),
        ((400.0, 10.0), (820.0, 300.0), (700.0, 1100.0), (60.0, 900.0)),
        ((10.0, 900.0), (300.0, 1200.0), (900.0, 1150.0), (700.0, 700.0)),
        ((1.0, 1.0), (1599.0, 2.0), (1598.0, 1599.0), (2.0, 1598.0)),
        ((500.0, 400.0), (560.0, 410.0), (555.0, 470.0), (505.0, 462.0)),
    ]
    targets = [
        ((0.0, 0.0), (960.0, 0.0), (960.0, 1200.0), (0.0, 1200.0)),
        ((0.0, 0.0), (800.0, 0.0), (800.0, 1200.0), (0.0, 1200.0)),
        ((0.0, 0.0), (1200.0, 0.0), (1200.0, 900.0), (0.0, 900.0)),
        ((0.0, 0.0), (1200.0, 0.0), (1200.0, 1500.0), (0.0, 1500.0)),
        ((0.0, 0.0), (120.0, 0.0), (120.0, 140.0), (0.0, 140.0)),
    ]
    for quad, target in zip(quads, targets, strict=True):
        matrix = solve_homography(quad, target)
        inverse = invert_homography(matrix)
        for original in quad:
            mapped = apply_homography(matrix, original)
            back = apply_homography(inverse, mapped)
            assert math.dist(back, original) < 1e-6, (quad, original, back)


def test_inverse_round_trips_a_perspective_matrix() -> None:
    inverse = invert_homography(PERSPECTIVE)
    point = (321.0, 654.0)
    back = apply_homography(inverse, apply_homography(PERSPECTIVE, point))
    assert math.dist(back, point) < 1e-9


# ---------------------------------------------------------------------------------------
# Refusals: a degenerate quad must not produce a matrix full of NaNs
# ---------------------------------------------------------------------------------------
def test_collinear_points_are_refused() -> None:
    with pytest.raises(DegenerateQuadError):
        solve_homography(UNIT_SQUARE, ((0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)))


def test_duplicated_points_are_refused() -> None:
    with pytest.raises(DegenerateQuadError):
        solve_homography(((0.0, 0.0), (0.0, 0.0), (1.0, 1.0), (2.0, 0.0)), UNIT_SQUARE)


def test_wrong_point_count_is_refused() -> None:
    with pytest.raises(DegenerateQuadError):
        solve_homography(UNIT_SQUARE[:3], UNIT_SQUARE)
    with pytest.raises(DegenerateQuadError):
        solve_homography(UNIT_SQUARE, UNIT_SQUARE + ((2.0, 2.0),))


def test_non_finite_coordinates_are_refused() -> None:
    with pytest.raises(DegenerateQuadError):
        solve_homography(((0.0, 0.0), (math.nan, 0.0), (1.0, 1.0), (0.0, 1.0)), UNIT_SQUARE)


def test_a_point_mapping_to_infinity_is_refused_not_returned_as_inf() -> None:
    # w = 0 on the line g·x + h·y + i = 0; with g = 1, h = 0, i = 0 that line is x = 0.
    matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0)
    with pytest.raises(DegenerateQuadError):
        apply_homography(matrix, (0.0, 5.0))


def test_a_singular_matrix_cannot_be_inverted() -> None:
    singular = (1.0, 2.0, 3.0, 2.0, 4.0, 6.0, 0.0, 0.0, 1.0)
    with pytest.raises(DegenerateQuadError):
        invert_homography(singular)


# ---------------------------------------------------------------------------------------
# The canonical canvas
# ---------------------------------------------------------------------------------------
def test_canonical_size_pins_the_long_edge_and_the_aspect() -> None:
    width, height = canonical_size("top", 1000)
    assert height == 1000
    assert width == 800
    assert width / height == pytest.approx(CANONICAL_ASPECT["top"], abs=1e-3)

    width, height = canonical_size("shoe", 1200)
    assert width == 1200
    assert height == 900


def test_unknown_category_falls_back_to_other_instead_of_raising() -> None:
    assert canonical_size("lampshade", 100) == canonical_size("other", 100)


def test_default_long_edge_respects_the_brief_ceiling() -> None:
    for category in CANONICAL_ASPECT:
        width, height = canonical_size(category)
        assert max(width, height) == DEFAULT_LONG_EDGE_PX <= 1600


def test_a_quad_lands_exactly_on_the_target_rectangle() -> None:
    quad = ((120.0, 80.0), (640.0, 100.0), (610.0, 720.0), (90.0, 690.0))
    matrix, width, height = homography_for_quad(quad, "top", 800)
    assert (width, height) == (640, 800)

    # 1. the solve itself is exact: corners land on the target rectangle
    expected = target_rect(width, height)
    raw = apply(matrix, quad)
    for got, want in zip(raw, expected, strict=True):
        assert math.dist(got, want) < 1e-6, (got, want)
    assert determinant(matrix) != pytest.approx(0.0)

    # 2. the warp helper additionally clamps onto the pixel grid, so the far corner is width-1
    mapped, _, _, _ = warp_quad(quad, "top", 800)
    for got, want in zip(mapped, expected, strict=True):
        assert math.dist(got, clamp_to_frame(want, width, height)) < 1e-6, (got, want)


def test_an_interior_point_of_the_garment_stays_inside_the_canvas() -> None:
    quad = ((120.0, 80.0), (640.0, 100.0), (610.0, 720.0), (90.0, 690.0))
    matrix, width, height = homography_for_quad(quad, "top", 800)
    centre = (
        sum(point[0] for point in quad) / 4,
        sum(point[1] for point in quad) / 4,
    )
    mapped = clamp_to_frame(apply_homography(matrix, centre), width, height)
    assert 0.0 <= mapped[0] <= width - 1
    assert 0.0 <= mapped[1] <= height - 1


def test_clamping_keeps_overhanging_points_on_the_pixel_grid() -> None:
    assert clamp_to_frame((-12.0, 500.0), 640, 800) == (0.0, 500.0)
    assert clamp_to_frame((900.0, -1.0), 640, 800) == (639.0, 0.0)
    assert clamp_to_frame((10.0, 20.0), 640, 800) == (10.0, 20.0)
    with pytest.raises(ValueError):
        clamp_to_frame((1.0, 1.0), 0, 10)


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------
def test_quad_area_is_order_independent() -> None:
    assert quad_area(UNIT_SQUARE) == pytest.approx(1.0)
    assert quad_area(tuple(reversed(UNIT_SQUARE))) == pytest.approx(1.0)
    assert quad_area(((0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0))) == pytest.approx(50.0)


def test_target_rect_is_clockwise_from_the_top_left() -> None:
    assert target_rect(4, 3) == ((0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0))
    with pytest.raises(ValueError):
        target_rect(0, 3)


def test_the_pixel_pipeline_is_still_explicitly_unimplemented() -> None:
    """The geometry is real; `rectify()` must still refuse rather than return the input bytes."""
    from packages.vision.rectify import rectify

    with pytest.raises(StageNotImplemented) as excinfo:
        rectify(b"", b"", "top")
    assert "E2-T1" in str(excinfo.value)
    assert "homography" in str(excinfo.value)
