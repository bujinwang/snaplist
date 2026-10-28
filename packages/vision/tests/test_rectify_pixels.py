"""Pixel-pipeline tests for E2-T1 `rectify()` (packages/vision/rectify.py).

The geometry (`solve_homography`, `invert_homography`, `canonical_size`, …) is already
covered by `test_rectify_geometry.py` at 1e-9. These tests cover what those cannot: the
decode → quad → warp → composite chain.

Fixtures are synthetic by design (E0-T1's no-synthetic rule applies to `evals/golden/`,
not to unit tests). Every test installs the socket trap — `rectify()` is a pure CPU stage
and must stay that way.
"""

from __future__ import annotations

import io
import socket
from collections.abc import Iterator

import numpy as np
import pytest
from PIL import Image

from packages.vision.rectify import (
    BACKDROP_RGB,
    canonical_size,
    rectify,
)
from packages.vision.types import StageMeta

GARMENT_RGB = (168, 42, 90)  # a magenta nothing like the backdrop


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def _trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("rectify() attempted a network call")

    monkeypatch.setattr(socket, "socket", _trap)
    monkeypatch.setattr(socket, "create_connection", _trap)
    yield


def png(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def garment_fixture(
    size: tuple[int, int] = (200, 160),
    box: tuple[int, int, int, int] = (40, 30, 150, 120),
) -> tuple[bytes, bytes]:
    """A photo with a solid magenta garment rect, and its alpha (soft 1px edge omitted)."""
    photo = np.full((*size[::-1], 3), BACKDROP_RGB, dtype=np.uint8)
    alpha = np.zeros(size[::-1], dtype=np.uint8)
    x0, y0, x1, y1 = box
    photo[y0:y1, x0:x1] = GARMENT_RGB
    alpha[y0:y1, x0:x1] = 255
    return png(photo), png(alpha)


# ------------------------------------------------------------------------ canvas geometry


def test_output_canvas_matches_canonical_size() -> None:
    photo, alpha = garment_fixture()
    result = rectify(photo, alpha, "top")

    assert result.meta.notes["canvas"] == list(canonical_size("top"))
    decoded = Image.open(io.BytesIO(result.flat_png))
    assert decoded.size == canonical_size("top")
    assert decoded.mode == "RGB"


def test_long_edge_never_exceeds_1600px_for_any_category() -> None:
    photo, alpha = garment_fixture()
    for category in ("top", "bottom", "dress", "shoe", "accessory", "other"):
        result = rectify(photo, alpha, category)
        width, height = Image.open(io.BytesIO(result.flat_png)).size
        assert max(width, height) <= 1600


# ------------------------------------------------------------------- backdrop & compositing


def test_corners_sample_the_quad_and_centre_is_garment() -> None:
    """The frozen geometry maps the garment quad onto the FULL canvas (no letterboxing,
    per CANONICAL_ASPECT's design note) — so canvas corners sample the quad corners, and
    the backdrop only shows through soft alpha. This pins that design."""
    photo, alpha = garment_fixture()
    flat = np.asarray(Image.open(io.BytesIO(rectify(photo, alpha, "top").flat_png)))

    for y, x in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
        assert tuple(flat[y, x]) == GARMENT_RGB, f"corner ({y},{x}) must sample the quad"

    centre = tuple(flat[flat.shape[0] // 2, flat.shape[1] // 2])
    assert centre == GARMENT_RGB, "the garment centre must survive the warp exactly"


def test_partial_alpha_blends_toward_the_backdrop() -> None:
    """A half-covered source pixel must come out halfway between garment and backdrop.
    The bbox quad fills the canvas, so the half-covered column lands on the canvas edge.
    """
    photo = np.full((32, 32, 3), BACKDROP_RGB, dtype=np.uint8)
    alpha = np.zeros((32, 32), dtype=np.uint8)
    photo[8:24, 8:24] = GARMENT_RGB
    alpha[8:24, 8:24] = 255
    alpha[8:24, 23] = 128  # the last bbox column is half-covered
    photo[8:24, 23] = GARMENT_RGB

    flat = np.asarray(Image.open(io.BytesIO(rectify(png(photo), png(alpha), "top").flat_png)))
    # The half-covered column maps onto the canvas right edge; resampling smears it, so
    # demand *betweenness* — every edge pixel strictly between garment and backdrop, and
    # at least one that is neither endpoint (the blend exists, not just a hard step).
    edge = flat[:, -1].astype(np.int64)
    lo = np.minimum(GARMENT_RGB, BACKDROP_RGB)
    hi = np.maximum(GARMENT_RGB, BACKDROP_RGB)
    assert ((edge > lo) & (edge < hi)).any(), "no intermediate blend value on the edge"
    assert all(
        ((edge[:, ch] >= lo[ch]) & (edge[:, ch] <= hi[ch])).all() for ch in range(3)
    ), "an edge pixel escaped the garment↔backdrop range"


def test_unknown_category_falls_back_instead_of_raising() -> None:
    photo, alpha = garment_fixture()
    result = rectify(photo, alpha, "a-category-the-vlm-invented")
    assert result.meta.notes["canvas"] == list(canonical_size("other"))


# ---------------------------------------------------------------------------- validation


def test_empty_alpha_is_no_garment_not_a_crash() -> None:
    photo, _ = garment_fixture()
    empty = np.zeros((160, 200), dtype=np.uint8)
    with pytest.raises(ValueError, match="no_garment"):
        rectify(photo, png(empty), "top")


def test_alpha_size_mismatch_is_named() -> None:
    photo, alpha = garment_fixture()
    with pytest.raises(ValueError, match="shape"):
        rectify(photo, png(np.zeros((10, 10), dtype=np.uint8)), "top")


@pytest.mark.parametrize("bad", [b"", b"garbage bytes", b"\x89PNG truncated"])
def test_garbage_bytes_raise_valueerror_naming_the_culprit(bad: bytes) -> None:
    photo, alpha = garment_fixture()
    with pytest.raises(ValueError, match="image"):
        rectify(bad, alpha, "top")
    with pytest.raises(ValueError, match="alpha"):
        rectify(photo, bad, "top")


# ------------------------------------------------------------------------------ honesty


def test_meta_is_measured_and_declares_its_shortcuts() -> None:
    photo, alpha = garment_fixture()
    result = rectify(photo, alpha, "top")

    meta = result.meta
    assert isinstance(meta, StageMeta)
    assert meta.stage == "rectify"
    assert meta.backend == "local_bbox_warp_v1"
    assert meta.device == "cpu"
    assert meta.seconds is not None and meta.seconds > 0.0
    assert meta.notes["quad_source"] == "alpha_bbox"  # the v1 shortcut, stated not hidden
    assert meta.notes["contact_shadow"] is False  # the owed work, stated not hidden
    assert meta.notes["network_calls"] == 0


def test_rectify_is_deterministic() -> None:
    photo, alpha = garment_fixture()
    first = rectify(photo, alpha, "top")
    second = rectify(photo, alpha, "top")

    assert first.flat_png == second.flat_png
    assert first.homography == second.homography


def test_homography_maps_source_quad_onto_canvas_corners() -> None:
    """The returned homography must take the alpha bbox (its own quad source) exactly
    onto `target_rect` — checked in the forward direction, which is what `h` means.
    """
    photo, alpha = garment_fixture(box=(40, 30, 150, 120))
    result = rectify(photo, alpha, "top")
    width, height = canonical_size("top")

    alpha_arr = np.asarray(Image.open(io.BytesIO(alpha)).convert("L"))
    ys, xs = np.nonzero(alpha_arr > 0)
    quad = (
        (float(xs.min()), float(ys.min())),
        (float(xs.max()), float(ys.min())),
        (float(xs.max()), float(ys.max())),
        (float(xs.min()), float(ys.max())),
    )
    expected = (
        (0.0, 0.0), (float(width), 0.0), (float(width), float(height)), (0.0, float(height))
    )

    a, b, c, d, e, f, g, hh, i = result.homography
    for (x, y), (ex, ey) in zip(quad, expected, strict=True):
        w = g * x + hh * y + i
        mapped = ((a * x + b * y + c) / w, (d * x + e * y + f) / w)
        assert abs(mapped[0] - ex) < 1e-6, f"quad corner {x},{y} -> {mapped}, wanted {ex}"
        assert abs(mapped[1] - ey) < 1e-6
