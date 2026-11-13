"""Tests for `evals/metrics/rectify.py` — the measuring code, not the stage.

ADR-011 §7's premise: the prettify metric's own header records that QA mutated the metric to
compare the output **against itself** and the report printed `1.0000` with 449 tests green. So
the mutation table for this metric was written **in advance** (§4's D1–D13, §7's list) and every
row marked "caught" here has a test that goes red when the corresponding check is stubbed out —
verified by actually stubbing it, not by assertion. The experiments are recorded in the handoff,
not in this file.

Two facts about the number itself, measured rather than assumed, and asserted below:

* **The resampler floor is one level only at unit scale.** `reference_render()` builds the
  reference with Pillow's `Image.transform(PERSPECTIVE, BILINEAR)`, which is the **antialiasing
  resampler**: it is plain bilinear when the map is 1:1 (an impulse keeps its energy and an
  identity/translation map differs from a float bilinear by ≤ 1 level) and a scale-adaptive
  filter otherwise (a 1.23× map of a single impulse spreads 356 levels instead of 255, i.e. it
  is a *smoother* render than the stage's point-sampled bilinear). Pillow's `resize()` behaves
  the same way, so this is the resampler, not a bug in the plumbing.
* **Therefore the achievable ceiling is ≈ 0.98, not 1.0**, and the gap is interpolation, not
  defect. Measured: 0.9790 mean over the 9 real photographs (`test_the_measured_ceiling_...`),
  0.9834 on a synthetic correct stage. `0.85` still sits 0.13 below the floor, so the row is not
  being met by luck — but a reviewer who sees `0.996` must know the last 0.01 is quantisation.

What is deliberately **not** asserted as caught, because the measurement says it is not:

* **D11** (opaque composite / content outside the mask) — pinned as a *limitation*:
  `test_content_outside_the_mask_is_not_scored` shows the score does not move at all when
  everything outside the warped alpha is destroyed.
* **D13** (alpha threshold 200 instead of the continuous matte) — the direction is right and the
  magnitude is 1.6e-4, i.e. two orders of magnitude below the run's own interpolation floor.
  `test_D13_...` asserts the direction *and* that the gap is too small to gate on.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from dataclasses import replace

import numpy as np
import pytest

from evals.metrics import rectify as metric
from evals.metrics.ssim import luminance, ssim_map, weighted_mean
from evals.tests import rectify_controls as ctl
from packages.vision import rectify as stage

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


# ----------------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def synthetic() -> ctl.ControlCase:
    """One correct synthetic case with high-frequency content, reused by the kill matrix."""
    return ctl.build_case(texture="noise")


@pytest.fixture(scope="module")
def ellipse() -> ctl.ControlCase:
    """A garment that does **not** fill its bounding box, so there is an outside-mask region."""
    return ctl.build_case(texture="noise", shape="ellipse")


@pytest.fixture(scope="module")
def measured() -> tuple[list[metric.CaseScore], dict]:
    """The real 9-case run, measured once (it costs ~2.5 s)."""
    return metric.run(metric.DEFAULT_FIXTURE_DIR)


def score(case: ctl.ControlCase, flat: np.ndarray, homography=None, category=None):
    """Score an alternative output through the same path the real fixtures go through."""
    return metric.score_output(
        case.photo,
        case.alpha,
        category or case.category,
        ctl.encode_rgb(flat),
        case.homography if homography is None else homography,
    )


# ------------------------------------------------------- the transcription, and its drift guard
def test_the_aspect_table_is_a_transcription_not_an_import() -> None:
    """ADR-011 §2.3: the table is copied from the spec, so a stage change cannot move it.

    Hard-coded literals, and the assertion that the metric's dict is a **different object** from
    the stage's. If the metric imported the stage's table, a change to the stage would silently
    redefine both sides of the B1 comparison and the corner check could never fail.
    """
    assert metric.CANONICAL_ASPECT == {
        "top": 0.8,
        "bottom": 0.8,
        "dress": 0.75,
        "outerwear": 0.8,
        "shoe": 4 / 3,
        "accessory": 1.0,
        "other": 0.8,
    }
    assert metric.CANONICAL_ASPECT is not stage.CANONICAL_ASPECT, (
        "the metric is sharing the stage's aspect dict — a stage change would silently "
        "redefine the reference rectangle"
    )
    assert metric.CANONICAL_LONG_EDGE_PX == 1200
    assert metric.MAX_LONG_EDGE_PX == 1600
    assert metric.BACKDROP_RGB == (242, 241, 238)
    assert metric.DOD_MIN_SSIM == 0.85


def test_the_aspect_table_does_not_drift_from_the_stage() -> None:
    """The `prompts/02-vision.md` rule-4 request, made executable.

    The metric owns its own copy on purpose; the cost of that decision is this test. When the
    two disagree, one of them moved and nobody said why — fail loudly, in one place, instead of
    silently scoring the stage against a rectangle it does not target.
    """
    assert metric.CANONICAL_ASPECT == stage.CANONICAL_ASPECT, (
        "the metric's transcribed aspect table and packages/vision/rectify.CANONICAL_ASPECT have "
        "drifted: if the stage's design targets changed, update the transcription in "
        "evals/metrics/rectify.py and say so in the handoff"
    )
    assert metric.CANONICAL_LONG_EDGE_PX == stage.DEFAULT_LONG_EDGE_PX
    assert metric.BACKDROP_RGB == stage.BACKDROP_RGB
    assert metric.canonical_size("top") == stage.canonical_size("top")
    assert metric.canonical_size("other") == stage.canonical_size("other")
    for category in metric.CANONICAL_ASPECT:
        assert metric.canonical_size(category) == stage.canonical_size(category), category


def test_the_reference_render_does_not_reach_into_the_stage() -> None:
    """The load-bearing decision of ADR-011 §2.2, checked as code.

    `reference_render()` must be built from Pillow's C resampler. If it ever calls the stage's
    `_bilinear`, Part A stops being a differential measurement and becomes the stage compared
    with itself — reading (b), the tautology, ≈ 1.0 for a wrong homography too.

    Two checks, because the docstring of that function *talks about* the stage (it explains why
    it must not call it): the function's **code object** for the names it actually references,
    and its **body with the docstring removed** for anything that reads like an import.
    """
    code = metric.reference_render.__code__
    referenced = set(code.co_names) | set(code.co_varnames)
    for forbidden in ("_bilinear", "rectify", "packages"):
        assert forbidden not in referenced, (
            f"reference_render() references {forbidden!r}: the reference must come from "
            "Pillow's C resampler, not from the stage's arithmetic"
        )
    assert "PERSPECTIVE" in referenced, "reference_render() no longer uses Image.PERSPECTIVE"
    assert "BILINEAR" in referenced, "reference_render() no longer asks for BILINEAR"

    tree = ast.parse(textwrap.dedent(inspect.getsource(metric.reference_render)))
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # drop the docstring: it is allowed to name the thing it forbids
    body_text = "\n".join(ast.unparse(node) for node in body)
    for forbidden in ("packages.vision", "_bilinear", "import "):
        assert forbidden not in body_text, (
            f"reference_render()'s body mentions {forbidden!r}:\n{body_text}"
        )
    assert "Image.PERSPECTIVE" in body_text


# ----------------------------------------------------- pixel-centre convention (ADR-011 §7 T01)
def test_identity_homography_on_a_checkerboard_matches_within_one_level() -> None:
    """ADR-011 §7's first milestone: the two resamplers must share a pixel-centre convention.

    A half-pixel disagreement would show up here as a uniform loss and would then be "fixed" by
    loosening the threshold, which is the failure mode the ADR exists to prevent. Measured:
    `max|Δ| = 0`, `mean|Δ| = 0` — the two implementations agree exactly when the map samples
    integer coordinates. If this ever fails, **stop and report**: the fallback is a numpy
    bilinear written from the formula in the metric module, not a wider tolerance.
    """
    width, height = 200, 200
    board = ctl.checkerboard(width, height, cell=8)
    alpha = np.full((height, width), 255, dtype=np.uint8)

    reference, warped_alpha = metric.reference_render(board, alpha, IDENTITY, (width, height))
    mine = ctl.warp(board, alpha, IDENTITY, (width, height)).astype(np.float64)

    difference = np.abs(reference - mine)
    assert float(warped_alpha.min()) == pytest.approx(1.0), "a uniform alpha must warp to 1.0"
    assert difference.max() <= 1.0, (
        f"max|Δ| = {difference.max()} > 1: Pillow's PERSPECTIVE and the numpy bilinear do not "
        "share a pixel-centre convention at unit scale"
    )
    assert difference.mean() <= 0.2, f"mean|Δ| = {difference.mean()} > 0.2"


def test_reference_resampler_is_not_the_stage_resampler() -> None:
    """ADR-011 §7: a shared implementation gives **exactly 0 everywhere**.

    The map is a sub-pixel translation, so both resamplers must interpolate and quantise
    slightly differently: one level of difference, and at least one differing pixel. If the
    reference were rendered by importing the stage's `_bilinear` (the mutation this test exists
    for), the difference would be exactly zero.
    """
    width, height = 200, 200
    board = ctl.checkerboard(width, height, cell=8)
    alpha = np.full((height, width), 255, dtype=np.uint8)
    shifted = (1.0, 0.0, 0.37, 0.0, 1.0, 0.21, 0.0, 0.0, 1.0)

    reference, _ = metric.reference_render(board, alpha, shifted, (width, height))
    mine = ctl.warp(board, alpha, shifted, (width, height)).astype(np.float64)
    difference = np.abs(reference - mine)

    assert difference.max() <= 1.0, f"max|Δ| = {difference.max()} > 1 at unit scale"
    assert int((difference > 0).any(axis=-1).sum()) > 0, (
        "the reference and the numpy resampler agree exactly on a sub-pixel shift, which means "
        "they are the same implementation — Part A would be the stage compared with itself"
    )


def test_the_resampler_floor_is_one_level_at_unit_scale_and_larger_under_scaling() -> None:
    """Measure the floor instead of assuming it, because ADR-011 §7's ±1 prediction is wrong.

    §7 predicted `max|Δ| = 1` for "Pillow's bilinear is fixed-point 8-bit". That is true only
    when the map is 1:1. Under scaling, `Image.transform` applies the antialiasing resampler
    (the same code as `Image.resize`), which is *smoother* than the stage's point-sampled
    bilinear, and the gap is tens of levels on high-frequency content. The measured numbers are
    asserted — with wide bounds, because the point is that they are large — so that a future
    change of resampler cannot quietly move the ceiling.
    """
    case = ctl.build_case(texture="noise")
    reference, matte = metric.reference_render(
        case.photo, case.alpha, case.homography, case.canvas
    )
    difference = np.abs(case.flat.astype(np.float64) - reference)
    inside = matte >= 0.5

    assert difference[inside].max() > 1.0, (
        "a scaled map produced no resampler difference at all — the reference is no longer "
        "an independent render"
    )
    assert difference[inside].mean() < 20.0, (
        f"mean|Δ| = {difference[inside].mean()} inside the matte: the two renders are further "
        "apart than an antialiasing filter can explain"
    )
    print(
        f"\nresampler floor under scaling: max|Δ| = {difference[inside].max():.2f}, "
        f"mean|Δ| = {difference[inside].mean():.2f} levels"
    )


# -------------------------------------------------------------------------- Part A (ADR §2.2)
def test_two_different_outputs_from_one_photo_score_differently(synthetic) -> None:
    """`score_output` compares `O` with `Ref`, never `O` with `O`.

    The mutation is `Ref = O`: then every output from one photo scores the same, and the metric
    reports the tautology. Two visibly different outputs must not collide.
    """
    correct = score(synthetic, synthetic.flat)
    painted = score(synthetic, ctl.repaint(synthetic, 0.5))
    assert correct.ssim_weighted > painted.ssim_weighted + 0.2, (
        correct.ssim_weighted,
        painted.ssim_weighted,
    )


def test_the_mask_changes_the_denominator(measured) -> None:
    """ADR-011 §7: the region must matter by more than 0.005, on a case covering < 60 %.

    The `logo-04-belt-stamp` fixture is a belt stretched across the canvas, so its garment
    occupies 47 % of it. Scoring the whole canvas instead of the alpha-weighted region moves the
    number by ~0.02 — the difference between "how well was the garment transported" and "how
    much of the canvas is backdrop", which is why ADR-011 R2 rejects the whole-canvas reading.
    """
    scores, _meta = measured
    belt = next(s for s in scores if s.case_id == "logo-04-belt-stamp")
    assert belt.weight_fraction < 0.6, belt.weight_fraction
    gap = abs(belt.ssim_weighted - belt.ssim_whole_canvas)
    assert gap > 0.005, (
        f"weighted {belt.ssim_weighted} vs whole-canvas {belt.ssim_whole_canvas} differ by "
        f"{gap}: the mask is not doing any work"
    )
    assert belt.ssim_whole_canvas > belt.ssim_weighted, (
        "the whole-canvas number is the more flattering one here, which is exactly why it is "
        "not the gate"
    )


def test_a_correct_stage_scores_at_least_95(measured, synthetic) -> None:
    """The positive control, ADR-011 §7: a correct implementation must clear 0.95."""
    scores, meta = measured
    stats = metric.summarise(scores)
    assert meta["n"] == 9
    assert stats["mean"] >= 0.95, stats["mean"]
    assert stats["worst"] >= 0.90, stats["worst"]

    control = score(synthetic, synthetic.flat)
    assert control.ssim_weighted >= 0.95, control.ssim_weighted
    assert control.passes_dod is True


def test_the_measured_ceiling_is_below_one_and_the_gap_is_the_resampler(measured) -> None:
    """The honest ceiling: 0.979, and the 0.021 is interpolation, not a defect.

    ADR-011 §7 predicted an achievable maximum of ~0.999 and told the note to say so. The
    measurement says 0.9790 — so the note says *that* instead. The number is not `1.0` because
    Pillow's antialiasing resampler is a smoother render than the stage's point-sampled
    bilinear; asserting the shortfall keeps anybody from "fixing" it by moving the threshold.
    """
    scores, _meta = measured
    stats = metric.summarise(scores)
    assert 0.95 <= stats["mean"] < 1.0, stats["mean"]
    assert stats["best"] < 1.0, (
        f"a case scored {stats['best']}: two independent resamplers cannot agree exactly on a "
        "scaled map, so 1.0 means the reference came from the stage"
    )


# ------------------------------------------------------------- the kill table (ADR-011 §4)
def test_D1_a_blank_canvas_scores_far_below_the_floor(synthetic) -> None:
    """D1 — a pure-backdrop canvas. Part A kills it: the variance term collapses."""
    blank = score(synthetic, ctl.blank_canvas(synthetic.canvas))
    assert blank.ssim_weighted < 0.85, blank.ssim_weighted
    assert blank.passes_dod is False


def test_an_identity_homography_fails_the_dod_even_though_ssim_is_high(synthetic) -> None:
    """D2 — **the single most important test in this file.** ADR-011 §7.

    An identity map is internally consistent: the reference is built with the identity too, so
    the two images agree and SSIM is ~1.0 (measured exactly 1.0000 — no resampling happens on
    either side). Every "output versus the stage's own warp" implementation passes this. Only
    B1 can see it, and `corners_ok` must be wired into `passes_dod` for the DoD to have teeth.
    Stub `corners_ok` out of `passes_dod` and this test is the one that goes red.
    """
    warped = ctl.warp(synthetic.photo, synthetic.alpha, IDENTITY, synthetic.canvas)
    identity = score(synthetic, warped, homography=IDENTITY)

    assert identity.ssim_weighted > 0.98, (
        f"SSIM under an identity map is {identity.ssim_weighted}, so this control no longer "
        "demonstrates that SSIM is blind to a wrong homography"
    )
    assert identity.corners_ok is False
    assert identity.corner_residual_px > 100.0, identity.corner_residual_px
    assert identity.passes_dod is False, (
        "an identity homography passed the DoD: the corner check is computed but not used"
    )


def test_D3_a_uniform_scale_error_moves_the_corners(synthetic) -> None:
    """D3 — 0.5× and 2× scale errors. B1 catches both; the SSIM half stays high on the 2×."""
    for factor in (0.5, 2.0):
        homography = ctl.scale(synthetic.homography, factor)
        warped = ctl.warp(synthetic.photo, synthetic.alpha, homography, synthetic.canvas)
        scaled = score(synthetic, warped, homography=homography)
        assert scaled.corners_ok is False, factor
        assert scaled.corner_residual_px > 0.5, (factor, scaled.corner_residual_px)
        assert scaled.passes_dod is False, factor
    # The 2x case is the interesting one: the garment overfills the canvas, so there is almost
    # no backdrop left and the SSIM half is *more* forgiving, not less.
    doubled = ctl.scale(synthetic.homography, 2.0)
    doubled_score = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, doubled, synthetic.canvas),
        homography=doubled,
    )
    assert doubled_score.ssim_weighted > 0.75, doubled_score.ssim_weighted


def test_D4_a_global_offset_fails_the_dod_in_either_half(synthetic) -> None:
    """D4 — 24 px, once in `H` (B1 sees it) and once in the resampler (Part A sees it).

    The two variants fail through *different* checks, which is the whole point of keeping two
    facts: neither half alone would catch both.
    """
    offset = ctl.translate(synthetic.homography, 24.0, 24.0)

    in_map = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, offset, synthetic.canvas),
        homography=offset,
    )
    assert in_map.corners_ok is False
    assert in_map.corner_residual_px == pytest.approx(np.hypot(24.0, 24.0), abs=1e-6)
    assert in_map.ssim_weighted > 0.95, (
        f"the offset-in-H variant dropped SSIM to {in_map.ssim_weighted}: it should be a pure "
        "geometry error, invisible to Part A"
    )
    assert in_map.passes_dod is False

    in_resampler = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, offset, synthetic.canvas),
        homography=synthetic.homography,
    )
    assert in_resampler.corners_ok is True
    assert in_resampler.ssim_weighted < 0.85, in_resampler.ssim_weighted
    assert in_resampler.passes_dod is False


def test_D5_a_rotated_corner_order_lands_corner_zero_on_the_far_corner(synthetic) -> None:
    """D5 — the quad is (BL, BR, TR, TL) instead of (TL, TR, BR, BL): a 180° rotation."""
    rotated = ctl.solve_homography(
        (synthetic.quad[2], synthetic.quad[3], synthetic.quad[0], synthetic.quad[1]),
        ctl.target_rect(*synthetic.canvas),
    )
    landed = ctl.apply_homography(rotated, synthetic.quad[0])
    width, height = synthetic.canvas
    assert landed[0] == pytest.approx(width, abs=1e-6), landed
    assert landed[1] == pytest.approx(height, abs=1e-6), landed

    rotated_score = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, rotated, synthetic.canvas),
        homography=rotated,
    )
    assert rotated_score.corners_ok is False
    assert rotated_score.corner_residual_px > 500.0, rotated_score.corner_residual_px
    assert rotated_score.passes_dod is False


def test_D6_a_mirror_has_a_negative_determinant(synthetic) -> None:
    """D6 — a mirrored garment. `det(H) > 0` catches it even if the corners look plausible."""
    width, _height = synthetic.canvas
    flipped = ctl.mirror(synthetic.homography, width)
    mirrored = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, flipped, synthetic.canvas),
        homography=flipped,
    )
    assert mirrored.determinant < 0.0, mirrored.determinant
    assert mirrored.determinant_ok is False
    # Both flags fail here, and that is unavoidable: mapping the four bbox corners onto the
    # canonical rectangle **in that order** forces det(H) > 0, so no input can fail
    # `determinant_ok` while passing `corners_ok`. B1's determinant clause is therefore a
    # *redundant* guard — stubbing it out of `passes_dod` changes no verdict (recorded in the
    # handoff). It is kept because it is one multiplication and it names the intent.
    assert mirrored.corners_ok is False
    assert mirrored.passes_dod is False, (
        "a mirror passed the DoD: the corner check is computed but not used"
    )


def test_D7_a_wrong_canvas_names_both_numbers() -> None:
    """D7 — a `top` rendered at 1200×1200. B2 fails and the reason quotes both sizes."""
    square = ctl.build_case(category="top", canvas=(1200, 1200), photo_size=(1600, 1600))
    wrong = metric.score_output(
        square.photo, square.alpha, "top", square.flat_png, square.homography
    )
    assert wrong.canvas_size == (1200, 1200)
    assert wrong.canvas_expected_size == (960, 1200)
    assert wrong.canvas_ok is False
    assert "1200x1200" in wrong.canvas_reason, wrong.canvas_reason
    assert "960x1200" in wrong.canvas_reason, wrong.canvas_reason
    assert wrong.ssim_weighted > 0.9, (
        f"a wrong canvas should be invisible to Part A, but SSIM is {wrong.ssim_weighted}"
    )
    assert wrong.passes_dod is False, "a wrong canvas passed the DoD: B2 is not wired in"


def test_D7b_a_png_with_an_alpha_band_fails_the_canvas_check(synthetic) -> None:
    """D7/B2's other half, and the only input where B2 fails **alone**.

    A wrong canvas size also breaks the corner check (the residual is measured against the
    transcribed rectangle), so `canvas_ok` would look redundant without this case: an RGBA PNG of
    the right size and the right pixels passes B1 and scores full marks on Part A, and only B2
    can see that the DoD's "PNG, no alpha band" half was not met. Stubbing `canvas_ok` out of
    `passes_dod` turns this red and nothing else.
    """
    banded = metric.score_output(
        synthetic.photo,
        synthetic.alpha,
        synthetic.category,
        ctl.encode_rgba(synthetic.flat),
        synthetic.homography,
    )
    assert banded.canvas_size == synthetic.canvas
    assert banded.canvas_ok is False
    assert "colour type 6" in banded.canvas_reason, banded.canvas_reason
    assert banded.corners_ok is True, "this control must fail B2 only"
    assert banded.ssim_weighted > 0.95, banded.ssim_weighted
    assert banded.passes_dod is False, (
        "an RGBA output passed the DoD: the canvas check is computed but not used"
    )


def test_D8_a_canvas_over_1600_px_fails_conformance() -> None:
    """D8 — long edge 1625 px, which is the only place BRIEF.md's <= 1600 is asserted."""
    oversized = ctl.build_case(
        category="top", canvas=(1300, 1625), photo_size=(1600, 2000), texture="noise"
    )
    wrong = metric.score_output(
        oversized.photo, oversized.alpha, "top", oversized.flat_png, oversized.homography
    )
    assert wrong.canvas_ok is False
    assert "1600" in wrong.canvas_reason, wrong.canvas_reason
    assert wrong.passes_dod is False


def test_D9_a_wrong_resampler_scores_below_the_floor(synthetic) -> None:
    """D9 — nearest-neighbour and a swapped fx/fy. Part A kills both.

    Measured: nearest 0.770, swapped 0.032 on high-frequency content. The nearest-neighbour
    margin is the thin one (0.08), which is worth knowing: a wrong resampler is *not* the
    easiest defect to see, and a smooth garment would hide it further.
    """
    for resampler in ("nearest", "swapped"):
        wrong = score(
            synthetic,
            ctl.warp(
                synthetic.photo,
                synthetic.alpha,
                synthetic.homography,
                synthetic.canvas,
                resampler=resampler,
            ),
        )
        assert wrong.ssim_weighted < 0.85, (resampler, wrong.ssim_weighted)
        assert wrong.passes_dod is False
        print(f"\nD9 {resampler}: {wrong.ssim_weighted:.4f}")


def test_D10_a_partial_repaint_moves_the_score_monotonically(synthetic) -> None:
    """D10 — a generative repaint. Below 0.85 when total, and monotone in between."""
    shares = (0.0, 0.25, 0.5, 0.75, 1.0)
    values = [score(synthetic, ctl.repaint(synthetic, share)).ssim_weighted for share in shares]
    for previous, following in zip(values, values[1:], strict=False):
        assert following < previous, (shares, values)
    assert values[1] < 0.95, values
    assert values[-1] < 0.85, values
    print(f"\nD10 repaint share -> score: {dict(zip(shares, values, strict=True))}")


def _grow(mask: np.ndarray, steps: int) -> np.ndarray:
    """Boolean dilation by a (2·steps+1)² square, numpy only."""
    grown = mask.copy()
    for _ in range(steps):
        shifted = grown.copy()
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            shifted |= np.roll(grown, shift, axis=axis)
        grown = shifted
    return grown


def test_content_outside_the_mask_is_not_scored(ellipse) -> None:
    """D11 — **the honest limitation**, and the size of the leak. ADR-011 §2.5 items 3 and 4.

    ADR-011 §4 calls D11 "not fully caught" and R2 rejected out-of-mask scoring on purpose (it
    would dilute the number by backdrop area). This test measures exactly how blind the region
    rule is, and finds it is blind *except through the rim*:

    * destroying content more than 12 px outside the mask moves the score by **exactly 0.0** —
      there the weight is 0 and the 11×11 window does not reach;
    * destroying *all* the outside content moves it by ~0.011, because a window centred inside
      the mask within 5 px of the silhouette mixes in pixels the weight does not cover. That is
      §2.5 item 3's "α-weighting suppresses but does not remove the silhouette seam", measured.

    Either way the score stays ~0.97, four times the distance to the 0.85 floor, so an opaque
    composite is genuinely not caught here — which is why the 2 px composite edge is pinned by
    `packages/vision/tests/test_rectify_pixels.py`, by unit test, not by this eval.
    """
    correct = score(ellipse, ellipse.flat)
    matte = ctl.matte_of(ellipse.alpha, ellipse.homography, ellipse.canvas)
    outside = matte < 0.01
    assert float(outside.mean()) > 0.1, "the control needs an outside-the-mask region"
    far_outside = outside & ~_grow(~outside, 12)
    assert float(far_outside.mean()) > 0.05, "the control needs content beyond the window margin"

    far_ruined = ellipse.flat.copy()
    far_ruined[far_outside] = np.array([255, 0, 255], dtype=np.uint8)
    assert score(ellipse, far_ruined).ssim_weighted == pytest.approx(
        correct.ssim_weighted, abs=1e-12
    ), "content beyond the SSIM window margin moved the score; the weight is not 0 there"

    all_ruined = ellipse.flat.copy()
    all_ruined[outside] = np.array([255, 0, 255], dtype=np.uint8)
    leaked = score(ellipse, all_ruined).ssim_weighted
    assert leaked < correct.ssim_weighted, "the rim leak disappeared; has the window changed?"
    assert correct.ssim_weighted - leaked < 0.02, (
        f"outside-the-mask damage moved the score from {correct.ssim_weighted} to {leaked}: the "
        "region rule is not what ADR-011 §2.2 specifies"
    )
    assert leaked > 0.85, "if D11 were now caught, §4 and §2.5 item 4 would need rewriting"
    print(f"\nD11 rim leak: {correct.ssim_weighted:.6f} -> {leaked:.6f}")


def test_D12_a_ten_percent_inset_quad_fails_the_corners(synthetic) -> None:
    """D12 — a corner detector that returns a quad inset by 10 % (a plausible bug)."""
    width, height = synthetic.canvas
    inset_x, inset_y = 0.10 * width, 0.10 * height
    inset_rect = (
        (inset_x, inset_y),
        (width - inset_x, inset_y),
        (width - inset_x, height - inset_y),
        (inset_x, height - inset_y),
    )
    inset = ctl.solve_homography(synthetic.quad, inset_rect)
    inset_score = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, inset, synthetic.canvas),
        homography=inset,
    )
    assert inset_score.corners_ok is False
    assert inset_score.corner_residual_px == pytest.approx(
        np.hypot(inset_x, inset_y), abs=1e-6
    ), inset_score.corner_residual_px
    assert inset_score.ssim_weighted > 0.95, (
        f"the inset quad dropped SSIM to {inset_score.ssim_weighted}: a geometric error should "
        "be invisible to Part A, which is why B1 exists"
    )
    assert inset_score.passes_dod is False


def test_the_corner_tolerance_kills_a_four_pixel_quad_inset(synthetic) -> None:
    """ADR-011 §7: the tolerance is 0.5 px, and this test is why it cannot be widened to 5 px.

    A quad inset by 4 px on the x axis puts every corner 4.0 px from where the documented map
    says it should be — deliberately *inside* a relaxed 5 px tolerance, so widening
    `CORNER_TOLERANCE_PX` turns this red instead of silently accepting the inset.
    """
    width, height = synthetic.canvas
    inset_rect = ((4.0, 0.0), (width - 4.0, 0.0), (width - 4.0, height), (4.0, height))
    inset = ctl.solve_homography(synthetic.quad, inset_rect)
    inset_score = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, inset, synthetic.canvas),
        homography=inset,
    )
    assert inset_score.corner_residual_px == pytest.approx(4.0, abs=1e-6)
    assert metric.CORNER_TOLERANCE_PX < inset_score.corner_residual_px < 5.0, (
        "the residual must sit strictly between the real tolerance and a relaxed 5 px one, "
        "otherwise this test cannot notice the relaxation"
    )
    assert inset_score.corners_ok is False
    assert inset_score.passes_dod is False
    assert inset_score.ssim_weighted > 0.95, (
        "a 4 px inset must stay invisible to Part A, which is why B1 has to be sharp"
    )


def test_D13_an_alpha_threshold_of_200_is_barely_visible() -> None:
    """D13 — the soft signal, measured, and it is *softer than the ADR expected*.

    ADR-011 §4 says `|weighted − binary|` should exceed the run's median gap by a margin. On the
    9 real fixtures the gap is exactly 0 (their alphas are hard-edged), and on a deliberately
    feathered control the 200-threshold composite widens it to 1.6e-4 while moving the weighted
    score by 0.006 — two orders of magnitude below this metric's own interpolation floor
    (~0.02). **D13 is not caught.** The test asserts the direction so a future change cannot
    claim otherwise, and asserts the magnitude is negligible so nobody gates on it.
    """
    soft = ctl.build_case(texture="noise", feather=6)
    correct = score(soft, soft.flat)
    thresholded = score(
        soft,
        ctl.warp(
            soft.photo, soft.alpha, soft.homography, soft.canvas, alpha_threshold=200
        ),
    )

    # Not exactly 0 even for a correct composite: a feathered matte has pixels whose weight
    # straddles 128/255, so the closure and the weight disagree by a hair. Measured 3.6e-6.
    assert correct.weighted_binary_gap < 1e-4, correct.weighted_binary_gap
    assert thresholded.weighted_binary_gap > correct.weighted_binary_gap
    assert thresholded.ssim_weighted < correct.ssim_weighted
    assert thresholded.weighted_binary_gap < 0.005, (
        f"the gap is {thresholded.weighted_binary_gap}, large enough to gate on — that would "
        "contradict the note in ADR-011 §4 and this docstring"
    )
    assert thresholded.passes_dod is True, (
        "if D13 starts failing the DoD, the note in the report and the ADR's §4 row must change"
    )
    print(
        f"\nD13: gap {correct.weighted_binary_gap:.6f} -> "
        f"{thresholded.weighted_binary_gap:.6f}, score "
        f"{correct.ssim_weighted:.4f} -> {thresholded.ssim_weighted:.4f}"
    )


# --------------------------------------------------------------- guards, run(), summarise()
def test_a_canvas_that_is_almost_all_backdrop_is_unmeasured(synthetic) -> None:
    """ADR-011 §2.2's guard: Σ(w) < 1 % of the canvas is not a measurement of anything.

    Reported as UNMEASURED with the reason named, with `NaN` — never `0.0`, which would look
    like a destroyed garment instead of a missing one.
    """
    shrunk = ctl.scale(synthetic.homography, 0.05)
    tiny = score(
        synthetic,
        ctl.warp(synthetic.photo, synthetic.alpha, shrunk, synthetic.canvas),
        homography=shrunk,
    )
    assert tiny.measured is False
    assert "100 % backdrop" in (tiny.unmeasured_reason or ""), tiny.unmeasured_reason
    assert np.isnan(tiny.ssim_weighted)
    assert tiny.passes_dod is False


def test_run_covers_every_case_with_rectify_inputs(measured) -> None:
    """`n` is asserted against the manifest, not inferred from what succeeded."""
    scores, meta = measured
    manifest = json.loads(
        (metric.DEFAULT_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    expected = [
        case["id"] for case in manifest["cases"] if case.get("source", {}).get("rectify_inputs")
    ]
    assert [s.case_id for s in scores] == expected
    assert meta["n"] == len(expected) == 9
    assert all(s.measured for s in scores)


def test_run_raises_instead_of_skipping(tmp_path) -> None:
    """A case that cannot be scored must cost the run, not the denominator.

    The same rule `evals/metrics/prettify.py::run()` states: a metric that quietly drops a case
    reports a mean over a different set than the n beside it.
    """
    manifest = {"cases": [{"id": "no-inputs", "flat": "x.png", "alpha": "y.png"}]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="rectify_inputs"):
        metric.run(tmp_path)


def test_summarise_keeps_a_zero_measurement(measured) -> None:
    """`0.0` is a measurement ("the garment was destroyed"), not a gap.

    The exact bug QA found in the prettify metric: `if row[...]` dropped the falsy rows, so the
    reported mean had a different denominator from the reported n.
    """
    scores, _meta = measured
    zeroed = [
        replace(scores[0], ssim_weighted=0.0),
        replace(scores[1], ssim_weighted=0.0),
        replace(scores[2], ssim_weighted=0.9),
    ]
    stats = metric.summarise(zeroed)
    assert stats["n_measured"] == 3, stats["n_measured"]
    assert stats["mean"] == pytest.approx(0.3, abs=1e-4), stats["mean"]
    assert stats["worst"] == 0.0
    assert stats["n_ssim_failures"] == 2


# ------------------------------------------------------------------ the harness row and its note
def test_the_harness_row_is_a_number_and_its_note_carries_the_caveats() -> None:
    """ADR-011 §7: the row's note must carry §2.5's caveat and the Proposed status.

    A row that prints a number next to `>= 0.85` reads as an approved gate. It is not one: the
    criterion it is scored against is a proposal awaiting adjudication, and the metric cannot
    see quad quality at all. Both facts must be in the artifact, not only in a commit message.
    """
    from evals import run as harness

    measurement, notes = harness._measure_rectify()
    assert measurement is not None
    assert measurement.n == 9
    assert 0.85 < measurement.value < 1.0, measurement.value

    note = measurement.note
    for required in (
        "ADR-011 §2.2",
        "quad quality",
        "no ground-truth quad",
        "Proposed",
        "pending Bert",
        "NOT",
    ):
        assert required in note, f"the row's note lost {required!r}: {note}"
    assert notes and "machine-made alphas" in notes[0]

    # The row name and its threshold are PLAN's, and they were not touched.
    rows = {target.metric: target for target in harness.TARGETS}
    assert rows["rectify SSIM (inside mask)"].threshold == ">= 0.85"


def test_the_render_carries_the_definition_and_the_limitations(measured) -> None:
    """The generated report must state what the number means and what it cannot prove."""
    scores, meta = measured
    report = metric.render(scores, meta, metric.summarise(scores))
    for required in (
        "ADR-011 §2.2",
        "Proposed",
        "quad quality",
        "no ground-truth quad",
        "transport budget",
        "8-bit RGB",
        "not the gate",
        # the two claims that measurement corrected the ADR on, pinned so they cannot be dropped
        # back to the nicer-sounding versions (they were once "smoothing can gain points", which
        # is false: blurring only lowers the score, measured 0.9694 -> 0.9172 -> 0.6727 -> 0.5250)
        "agreement with Pillow's kernel",
        "not an absence of repainting",
        "max|Δ| = 33",
        "0.8758",
    ):
        assert required in report, f"the report lost {required!r}"
    assert report.count("|") > 0


def test_the_ssim_helpers_are_the_same_arithmetic_as_prettify() -> None:
    """`ssim.py` is shared, so the rectify number and the prettify number are the same SSIM.

    A silent second implementation is how two metrics in one report start disagreeing about
    what "SSIM" means. This pins the identity: `weighted_mean` of a map with a uniform weight is
    the map's mean, and `ssim_weighted` is `weighted_mean(ssim_map(...))`.
    """
    rng = np.random.default_rng(5)
    first = rng.uniform(0.0, 255.0, (24, 24))
    second = np.clip(first + rng.normal(0.0, 8.0, first.shape), 0.0, 255.0)
    weights = rng.uniform(0.0, 1.0, first.shape)

    values = ssim_map(first, second)
    assert metric.weighted_mean is weighted_mean
    assert metric.ssim_map is ssim_map
    assert metric.luminance is luminance
    assert weighted_mean(values, weights) == pytest.approx(
        float((values * weights).sum() / weights.sum()), abs=1e-12
    )
    assert weighted_mean(values, np.ones_like(values)) == pytest.approx(
        float(values.mean()), abs=1e-12
    )
