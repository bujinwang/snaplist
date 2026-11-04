"""Tests for `evals/metrics/prettify.py` — the measuring code, not the stage.

Why this file exists: QA mutated the metric to compare the output **against itself** and the
report printed `1.0000` with all 449 tests green. A metric with no tests is a metric that can
silently stop measuring, and this is the number the epic's DoD is quoted from.

What is pinned here:

* `ssim_inside_mask` of an image against itself is exactly 1.0, and noise strictly lowers it
  (`test_identity_is_one_and_noise_lowers_it`) — the direction of the metric;
* the value matches the closed form `(2·µa·µb + C1)/(µa² + µb² + C1)` on constant images to
  1e-9 (`test_matches_the_closed_form_for_constant_images`) — the arithmetic;
* a shape mismatch raises instead of broadcasting into a wrong number
  (`test_shape_mismatch_raises`);
* it compares **input vs output**, symmetrically: perturbing the output lowers the score,
  swapping the two arguments does not change it
  (`test_it_compares_the_two_images_symmetrically`);
* the mask really is the denominator: the same pair scored inside a small box differs from the
  whole garment (`test_the_mask_changes_the_denominator`);
* `summarise()` keeps an exact `0.0` instead of dropping the case
  (`test_summarise_keeps_a_zero_measurement`) — the bug QA found, where two 0.0 rows and one
  9.0 row reported 9.0 instead of 4.5.

**And one test that goes through `run()` rather than the helpers**
(`test_run_reports_input_against_output_on_the_real_fixtures`). Without it, mutating `run()`
so that SSIM compares `before` with a copy of itself left the report printing a perfect
`1.0000` with the whole suite green — the same class of defect QA found in the stage, one
layer up. Every helper can be right while the code that *assembles* the number is wrong.
"""

from __future__ import annotations

import io
import json
import shutil

import numpy as np
import pytest
from PIL import Image

from evals.metrics.prettify import (
    DEFAULT_FIXTURE_DIR,
    SSIM_DATA_RANGE,
    SSIM_K1,
    _luminance,
    run,
    ssim_inside_mask,
    ssim_map,
    summarise,
)
from packages.vision.prettify import prettify
from packages.vision.types import PrettifyMode


def constant(value: float, size: tuple[int, int] = (12, 12)) -> np.ndarray:
    return np.full(size, float(value))


def test_identity_is_one_and_noise_lowers_it() -> None:
    base = np.full((24, 24), 128.0)
    assert ssim_inside_mask(base, base.copy(), np.ones_like(base, dtype=bool)) == pytest.approx(
        1.0, abs=1e-12
    )

    rng = np.random.default_rng(7)  # seeded: a flaky metric test is worse than none
    mask = np.ones_like(base, dtype=bool)
    previous = 1.0
    for sigma in (2.0, 6.0, 18.0, 40.0):
        noisy = np.clip(base + rng.normal(0.0, sigma, base.shape), 0.0, 255.0)
        value = ssim_inside_mask(base, noisy, mask)
        assert value < previous, (sigma, value, previous)
        assert 0.0 <= value <= 1.0, (sigma, value)
        previous = value


def test_matches_the_closed_form_for_constant_images() -> None:
    """Two flat images have zero variance, so SSIM collapses to the luminance term exactly."""
    c1 = (SSIM_K1 * SSIM_DATA_RANGE) ** 2
    for left, right in ((100.0, 120.0), (200.0, 40.0), (17.0, 240.0)):
        expected = (2.0 * left * right + c1) / (left * left + right * right + c1)
        got = ssim_inside_mask(
            constant(left), constant(right), np.ones((12, 12), dtype=bool)
        )
        assert got == pytest.approx(expected, abs=1e-9), (left, right, got, expected)


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        ssim_map(np.zeros((10, 10)), np.zeros((10, 11)))
    with pytest.raises(ValueError):
        ssim_inside_mask(np.zeros((10, 10)), np.zeros((11, 10)), np.ones((10, 10), dtype=bool))


def test_empty_mask_is_not_a_number() -> None:
    """No region => NaN, so the caller reports UNMEASURED instead of inventing a value."""
    empty = np.zeros((8, 8), dtype=bool)
    value = ssim_inside_mask(np.zeros((8, 8)), np.zeros((8, 8)), empty)
    assert np.isnan(value)


def test_it_compares_the_two_images_symmetrically() -> None:
    """Input vs output, not output vs output: a one-pixel change must move the number."""
    rng = np.random.default_rng(11)
    before = rng.uniform(0.0, 255.0, (32, 32))
    after = before.copy()
    mask = np.ones((32, 32), dtype=bool)

    identical = ssim_inside_mask(before, after, mask)
    assert identical == pytest.approx(1.0, abs=1e-12)

    after[16, 16] = 255.0 - after[16, 16]  # one pixel, large jump
    changed = ssim_inside_mask(before, after, mask)
    assert changed < identical, (changed, identical)

    # Symmetry: SSIM(a, b) == SSIM(b, a). A metric that compared an image with itself (the QA
    # mutation) would be symmetric too, so this alone is not the check — the ``changed <
    # identical`` assertion above is.
    assert ssim_inside_mask(before, after, mask) == pytest.approx(
        ssim_inside_mask(after, before, mask), abs=1e-12
    )


def test_the_mask_changes_the_denominator() -> None:
    """The same pair scored over different regions must give different numbers."""
    rng = np.random.default_rng(3)
    before = np.full((40, 40), 120.0)
    after = before.copy()
    after[:, :8] = 200.0  # only the left eighth is modified

    everywhere = np.ones((40, 40), dtype=bool)
    left = np.zeros((40, 40), dtype=bool)
    left[:, :8] = True
    right = np.zeros((40, 40), dtype=bool)
    right[:, 32:] = True

    value_all = ssim_inside_mask(before, after, everywhere)
    value_left = ssim_inside_mask(before, after, left)
    value_right = ssim_inside_mask(before, after, right)

    assert value_left < value_all < value_right, (value_left, value_all, value_right)
    assert value_right == pytest.approx(1.0, abs=1e-12)
    # A mask of the wrong dtype/shape must not be accepted silently.
    with pytest.raises(ValueError):
        ssim_inside_mask(before, after, rng.uniform(0.0, 1.0, (39, 40)) > 0.5)


def test_summarise_keeps_a_zero_measurement() -> None:
    """`0.0` is a measurement, not a missing value: two 0.0s and one 9.0 mean 3.0, not 9.0.

    This is the bug QA found — `if row[...]` dropped the falsy rows, so the reported mean had a
    different denominator from the reported n.
    """
    rows = [
        {
            "ssim": 0.9,
            "mean_abs_delta_in_mask": 0.0,
            "protect_overlap_px": 0,
            "auto_overlap_px": 0,
            "auto_ink_overlap_px": 0,
            "crease_hit": None,
            "mask_coverage": 0.1,
            "cap_bound": False,
            "seconds": 0.1,
            "protect_share": 0.1,
        },
        {
            "ssim": 0.8,
            "mean_abs_delta_in_mask": 0.0,
            "protect_overlap_px": 0,
            "auto_overlap_px": 5,
            "auto_ink_overlap_px": 0,
            "crease_hit": None,
            "mask_coverage": 0.1,
            "cap_bound": False,
            "seconds": 0.1,
            "protect_share": 0.1,
        },
        {
            "ssim": 0.7,
            "mean_abs_delta_in_mask": 9.0,
            "protect_overlap_px": 0,
            "auto_overlap_px": 0,
            "auto_ink_overlap_px": 0,
            "crease_hit": None,
            "mask_coverage": 0.1,
            "cap_bound": False,
            "seconds": 0.1,
            "protect_share": 0.1,
        },
    ]
    stats = summarise(rows)
    assert stats["n_delta"] == 3, "a 0.0 mean |dL| is a case, not a gap"
    assert stats["mean_abs_delta_in_mask"] == pytest.approx(3.0), stats["mean_abs_delta_in_mask"]
    assert stats["protect_overlap_total"] == 0
    assert stats["protect_clean"] is True
    assert stats["auto_overlap_total"] == 5
    assert stats["auto_ink_overlap_total"] == 0
    assert stats["mean"] == pytest.approx(0.8, abs=1e-6)


def test_run_reports_input_against_output_on_the_real_fixtures() -> None:
    """The number in the report is `input vs output`, recomputed from the files on disk.

    This is the test QA asked for, and the one that kills the mutation they demonstrated: with
    `after = before.copy()` inside `run()` every row becomes 1.0 while every helper test above
    still passes. Two independent checks:

    1. **no row may be a perfect 1.0** — the stage changes pixels on every fixture, so a 1.0
       means the comparison collapsed to "an image against itself";
    2. **the reported row must equal a fresh recomputation** from the fixture bytes, so the row
       is pinned to the pipeline rather than to a substituted pair. The row is rounded to four
       decimals for reporting, so the comparison is against the same rounding at 1e-9.
    """
    rows, meta = run(DEFAULT_FIXTURE_DIR)
    assert meta["n"] == len(rows) > 0

    for row in rows:
        assert row["ssim"] is not None, row["id"]
        assert row["ssim"] < 1.0, (
            f"{row['id']}: SSIM is exactly 1.0 — the metric is no longer comparing the output "
            "with the input"
        )

    entry = json.loads((DEFAULT_FIXTURE_DIR / "manifest.json").read_text())["cases"][0]
    flat_bytes = (DEFAULT_FIXTURE_DIR / entry["flat"]).read_bytes()
    alpha_bytes = (DEFAULT_FIXTURE_DIR / entry["alpha"]).read_bytes()
    protect_box = entry.get("protect_box")
    result = prettify(
        flat_bytes,
        alpha_bytes,
        mode=PrettifyMode.CLASSICAL,
        protect=[tuple(protect_box)] if protect_box else None,
    )

    def luminance_of(rgb_bytes: bytes) -> np.ndarray:
        with Image.open(io.BytesIO(rgb_bytes)) as opened:
            return _luminance(np.asarray(opened.convert("RGB"), dtype=np.float64))

    with Image.open(DEFAULT_FIXTURE_DIR / entry["alpha"]) as opened:
        garment = np.asarray(opened.convert("L")) >= 128

    expected = ssim_inside_mask(
        luminance_of(flat_bytes), luminance_of(result.prettified_png), garment
    )
    assert rows[0]["id"] == entry["id"]
    assert rows[0]["ssim"] == pytest.approx(round(expected, 4), abs=1e-9)


def test_a_broken_rectify_path_costs_the_timing_and_not_the_ssim_row(tmp_path) -> None:
    """A supplementary number failing must not take the headline number down with it.

    `_time_rectify`'s docstring promised "a failure here is reported as a missing number, not
    as a crash", and QA found the code raising `FileNotFoundError` from `run()`, which turned
    the whole `prettify SSIM (inside mask)` row into `UNMEASURED`. The promise is now a test.
    """
    shutil.copytree(DEFAULT_FIXTURE_DIR, tmp_path / "fx")
    manifest_path = tmp_path / "fx" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["source"]["rectify_inputs"]["photo"] = "evals/golden-provisional/nope.jpg"
    manifest_path.write_text(json.dumps(manifest))

    rows, meta = run(tmp_path / "fx")

    assert meta["n"] == len(rows) > 0
    assert all(row["ssim"] is not None for row in rows), "the SSIM row died with the timing"
    assert meta["rectify_notes"], "the missing timing was swallowed instead of reported"
    assert "rectify timing unavailable" in meta["rectify_notes"][0]
    # The other eight cases still produced a timing, so the block is incomplete, not empty.
    assert meta["rectify_seconds_by_backend"]
