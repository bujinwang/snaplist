"""Wang et al. SSIM, shared by the two vision metrics that quote a number from it.

`scikit-image` is not a dependency (`docs/DEPS.md`) and adding it for one metric would put a
15 MB wheel behind a number the harness must be able to print on a cold machine. The
implementation is the standard Wang et al. formulation with an 11x11 uniform window and
`C1 = (0.01*L)^2`, `C2 = (0.03*L)^2` on 8-bit data — the same constants everyone quotes, so
the number is comparable with the literature. Window means come from integral images, so it is
exact (no truncated-kernel edge bias) and fast.

This module exists because **two** metrics need the same arithmetic and they must agree about
it. It was extracted out of `evals/metrics/prettify.py` (which re-exports every name below, so
its callers and its test file are unchanged) when `evals/metrics/rectify.py` needed the same
`ssim_map` for a different region definition:

    prettify   region = a boolean garment mask (alpha >= 128)   -> ssim_inside_mask
    rectify    region = the warped alpha, continuous, weighted  -> ssim_weighted

The two region rules live side by side on purpose: ADR-011 §2.2 argues at length that the
rectify number must be alpha-**weighted** rather than thresholded, and the cheapest way for a
reader to check that claim is to see both functions in one file, differing only in how the
weight enters the sum. `ssim_inside_mask` is a special case of `ssim_weighted` with a 0/1
weight; it is kept as its own function because a boolean mask is a different *claim* about the
region, not a different implementation of the same claim.

No code here decides a threshold. `rectify` and `prettify` own their own DoD constants.
"""

from __future__ import annotations

import numpy as np

SSIM_WINDOW = 11
SSIM_K1 = 0.01
SSIM_K2 = 0.03
SSIM_DATA_RANGE = 255.0


def _box_mean(array: np.ndarray, radius: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, edges replicated, by integral image.

    Replicated edges (rather than `valid` cropping) keep the map the same size as the image so
    the mask can select from it directly; with `valid` the reported region would silently
    shrink by 5 px on every side, which is exactly the kind of quiet change of denominator
    this harness exists to prevent.
    """
    padded = np.pad(array.astype(np.float64, copy=False), radius, mode="edge")
    integral = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    height, width = array.shape
    window = 2 * radius + 1
    ys = np.arange(height)
    xs = np.arange(width)
    total = (
        integral[np.ix_(ys + window, xs + window)]
        - integral[np.ix_(ys, xs + window)]
        - integral[np.ix_(ys + window, xs)]
        + integral[np.ix_(ys, xs)]
    )
    return total / float(window * window)


def ssim_map(first: np.ndarray, second: np.ndarray, *, window: int = SSIM_WINDOW) -> np.ndarray:
    """Per-pixel SSIM of two 2-D float images on a 0..255 scale."""
    if first.shape != second.shape:
        raise ValueError(f"ssim: shape mismatch {first.shape} vs {second.shape}")
    radius = window // 2
    c1 = (SSIM_K1 * SSIM_DATA_RANGE) ** 2
    c2 = (SSIM_K2 * SSIM_DATA_RANGE) ** 2
    mu_first = _box_mean(first, radius)
    mu_second = _box_mean(second, radius)
    var_first = np.maximum(_box_mean(first * first, radius) - mu_first * mu_first, 0.0)
    var_second = np.maximum(_box_mean(second * second, radius) - mu_second * mu_second, 0.0)
    covariance = _box_mean(first * second, radius) - mu_first * mu_second
    numerator = (2.0 * mu_first * mu_second + c1) * (2.0 * covariance + c2)
    denominator = (mu_first * mu_first + mu_second * mu_second + c1) * (
        var_first + var_second + c2
    )
    return numerator / denominator


def ssim_inside_mask(
    first: np.ndarray, second: np.ndarray, mask: np.ndarray, *, window: int = SSIM_WINDOW
) -> float:
    """Mean SSIM over `mask`. Returns NaN for an empty mask (caller reports UNMEASURED)."""
    selected = np.asarray(mask).astype(bool)
    if selected.shape != np.shape(first):
        # A mask of the wrong shape is a bug in the caller, and numpy's IndexError for a
        # boolean index mismatch is the kind of message that gets worked around instead of
        # fixed. Fail with the shapes named.
        raise ValueError(
            f"ssim_inside_mask: mask shape {selected.shape} does not match image shape "
            f"{np.shape(first)}"
        )
    if not selected.any():
        return float("nan")
    values = ssim_map(first, second, window=window)[selected]
    return float(values.mean())


def weighted_mean(values: np.ndarray, weight: np.ndarray) -> float:
    """`sum(w * values) / sum(w)`, with the guards a weighted mean needs.

    Split out of `ssim_weighted` so a caller that has already computed the map (the rectify
    metric needs `ssim_map` for three different region rules over the same pair of images)
    does not pay for a second convolution just to average it. Same arithmetic either way, so
    the two entry points cannot drift.
    """
    sample = np.asarray(values, dtype=np.float64)
    weights = np.asarray(weight, dtype=np.float64)
    if weights.shape != sample.shape:
        raise ValueError(
            f"weighted_mean: weight shape {weights.shape} does not match value shape "
            f"{sample.shape}"
        )
    if not np.all(np.isfinite(weights)):
        raise ValueError("weighted_mean: the weight array contains a non-finite value")
    if np.any(weights < 0.0):
        raise ValueError("weighted_mean: the weight array contains a negative value")
    total = float(weights.sum())
    if total <= 0.0:
        return float("nan")
    return float((weights * sample).sum() / total)


def ssim_weighted(
    first: np.ndarray, second: np.ndarray, weight: np.ndarray, *, window: int = SSIM_WINDOW
) -> float:
    """`sum(w * SSIM) / sum(w)` — a continuous weight, with no threshold and no erosion.

    This is ADR-011 §2.2's region rule. Three things it deliberately does **not** do:

    * **no threshold** — a hard `w >= 128/255` cut is a parameter with no derivation, and an
      11x11 SSIM window centred within 5 px of the silhouette mixes garment and backdrop, so a
      binary mask scores the compositing seam instead of the garment;
    * **no erosion** — eroding to dodge that seam can vanish entirely on a thin garment (the
      `logo-04-belt-stamp` fixture is a belt, and the alpha-bbox quad stretches it across a
      1200 px canvas, so its region is a thin diagonal);
    * **no re-derivation of the region from the output** — that would need a backdrop
      classifier and would let the stage under test define its own denominator.

    Returns NaN when `sum(w)` is 0 (an empty region is not a measurement; the caller reports
    UNMEASURED with a reason rather than inventing a value).
    """
    if np.shape(weight) != np.shape(first):
        raise ValueError(
            f"ssim_weighted: weight shape {np.shape(weight)} does not match image shape "
            f"{np.shape(first)}"
        )
    return weighted_mean(ssim_map(first, second, window=window), weight)


def luminance(rgb: np.ndarray) -> np.ndarray:
    """`0.299R + 0.587G + 0.114B` on a float RGB array, the Y of ITU-R BT.601.

    Public name (`luminance`) for new callers; `_luminance` is kept as the historical alias so
    `evals/metrics/prettify.py`'s re-export and its test file import the same symbol they
    always did.
    """
    return 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]


_luminance = luminance
