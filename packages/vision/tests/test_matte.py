"""Tests for E1-T3 `matte()` (packages/vision/matte.py).

The DoD lines that matter here, and how each is proven:

* "no halo wider than 2px"  -> a straight-edge fixture is checked pixel-column by
  pixel-column: alpha must be exactly 0 from 3px outside the mask outward, may be
  soft at 1-2px, and the interior must stay exactly 255.
* "zero network calls"      -> sockets are replaced with traps before the call.
* honest metadata           -> seconds is a measured positive float (never a 0.0
  placeholder), backend/device/notes populated.

The synthetic images are test fixtures, not golden-set material: E0-T1 forbids
synthetic entries in `evals/golden/`, and none of these files touch that directory.
"""

from __future__ import annotations

import io
import socket
from collections.abc import Iterator

import numpy as np
import pytest
from PIL import Image

from packages.vision.matte import matte
from packages.vision.types import StageMeta

HALO_BAND_PX = 2


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """matte() is the one stage that must never touch the network. Prove it every test."""

    def _trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("matte() attempted a network call")

    monkeypatch.setattr(socket, "socket", _trap)
    monkeypatch.setattr(socket, "create_connection", _trap)
    yield


def png(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def rgb_png(color: tuple[int, int, int], size: tuple[int, int]) -> bytes:
    return png(np.full((*size[::-1], 3), color, dtype=np.uint8))


def straight_edge_mask(width: int = 40, height: int = 40, edge_x: int = 20) -> np.ndarray:
    """Left half opaque, right half empty — a clean vertical boundary at x = edge_x."""
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[:, :edge_x] = 255
    return mask


def decode_alpha(alpha_png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(alpha_png)).convert("L"))


# ------------------------------------------------------------------------------ DoD: halo


def test_interior_stays_exactly_opaque() -> None:
    mask = straight_edge_mask()
    alpha = decode_alpha(matte(rgb_png((200, 100, 100), (40, 40)), png(mask)).alpha_png)
    interior = alpha[:, : 20 - (HALO_BAND_PX + 1)]
    assert (interior == 255).all(), "a blur must not make the garment body see-through"


def test_no_halo_beyond_two_pixels() -> None:
    edge_x = 20
    mask = straight_edge_mask(edge_x=edge_x)
    alpha = decode_alpha(matte(rgb_png((200, 100, 100), (40, 40)), png(mask)).alpha_png)

    assert (alpha[:, edge_x + HALO_BAND_PX + 1 :] == 0).all(), (
        "alpha found more than 2px outside the mask — that is the halo the DoD forbids"
    )


def test_soft_edge_exists_inside_the_band() -> None:
    """A hard step to 0 would shred knit edges; the band must carry real intermediate
    values — but never overshoot into opaque."
    """
    edge_x = 20
    mask = straight_edge_mask(edge_x=edge_x)
    alpha = decode_alpha(matte(rgb_png((200, 100, 100), (40, 40)), png(mask)).alpha_png)

    band_values = alpha[:, edge_x : edge_x + HALO_BAND_PX]
    assert ((band_values > 0) & (band_values < 255)).any()
    assert (band_values <= 255).all()


def test_full_mask_stays_fully_opaque_and_empty_mask_stays_empty() -> None:
    solid = matte(rgb_png((10, 10, 10), (16, 16)), png(np.full((16, 16), 255, dtype=np.uint8)))
    assert (decode_alpha(solid.alpha_png) == 255).all()

    empty = matte(rgb_png((10, 10, 10), (16, 16)), png(np.zeros((16, 16), dtype=np.uint8)))
    assert (decode_alpha(empty.alpha_png) == 0).all()


def dilate(mask_bool: np.ndarray, radius_px: int) -> np.ndarray:
    """Chebyshev dilation (8-neighbourhood) by radius_px, numpy-only."""
    grown = mask_bool.copy()
    for _ in range(radius_px):
        padded = np.pad(grown, 1, constant_values=False)
        grown = (
            padded[:-2, :-2] | padded[:-2, 1:-1] | padded[:-2, 2:]
            | padded[1:-1, :-2] | padded[1:-1, 1:-1] | padded[1:-1, 2:]
            | padded[2:, :-2] | padded[2:, 1:-1] | padded[2:, 2:]
        )
    return grown


def assert_halo_within_bound(alpha: np.ndarray, mask: np.ndarray) -> None:
    """The DoD, stated as pixels: any non-opaque-source pixel with alpha > 0 must lie
    within HALO_BAND_PX of a mask pixel. Independent of the blur implementation.
    """
    lit = alpha > 0
    allowed = dilate(mask > 0, HALO_BAND_PX)
    stray = lit & ~allowed
    rows, cols = np.nonzero(stray)
    assert not rows.size, f"alpha found {rows.size}px beyond the {HALO_BAND_PX}px band"


def test_thin_strap_survives() -> None:
    """A 3px strap must not be blurred away: its centre column stays opaque, and the
    halo bound still holds on both sides.
    """
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[:, 14:17] = 255  # 3px-wide vertical strap
    alpha = decode_alpha(matte(rgb_png((0, 0, 0), (30, 30)), png(mask)).alpha_png)

    assert (alpha[:, 15] == 255).all(), "the strap centre must survive fully opaque"
    assert_halo_within_bound(alpha, mask)


# ------------------------------------------------------------------ input validation


@pytest.mark.parametrize("bad", [b"", b"not a png at all", b"\x89PNG\r\n\x1a\ntruncated"])
def test_garbage_image_bytes_raise_valueerror(bad: bytes) -> None:
    good_mask = png(straight_edge_mask(width=8, height=8, edge_x=4))
    with pytest.raises(ValueError, match="image"):
        matte(bad, good_mask)


@pytest.mark.parametrize("bad", [b"", b"definitely not a png"])
def test_garbage_mask_bytes_raise_valueerror(bad: bytes) -> None:
    with pytest.raises(ValueError, match="mask"):
        matte(rgb_png((0, 0, 0), (8, 8)), bad)


def test_rgba_mask_uses_its_alpha_channel() -> None:
    rgba = np.zeros((16, 16, 4), dtype=np.uint8)
    rgba[:, :8, 3] = 255  # alpha channel is the mask, RGB is noise
    rgba[:, :, :3] = (7, 7, 7)
    result = matte(rgb_png((0, 0, 0), (16, 16)), png(rgba))

    alpha = decode_alpha(result.alpha_png)
    assert (alpha[:, :4] == 255).all()
    assert (alpha[:, 12:] == 0).all()


# --------------------------------------------------------------------- honest metadata


def test_alpha_output_is_same_size_l_mode_png() -> None:
    mask = straight_edge_mask(width=24, height=32)
    result = matte(rgb_png((1, 2, 3), (24, 32)), png(mask))

    decoded = Image.open(io.BytesIO(result.alpha_png))
    assert decoded.size == (24, 32)
    assert decoded.mode == "L"


def test_meta_is_measured_and_explains_itself() -> None:
    result = matte(rgb_png((1, 2, 3), (16, 16)), png(straight_edge_mask(width=16, height=16)))

    meta = result.meta
    assert isinstance(meta, StageMeta)
    assert meta.stage == "matte"
    assert meta.backend == "local_feather_v1"
    assert meta.device == "cpu"
    assert meta.seconds is not None and meta.seconds > 0.0, (
        "UNMEASURED would be None; 0.0 would be a lie — this must be a real measurement"
    )
    assert meta.notes["halo_band_px"] == HALO_BAND_PX
    assert meta.notes["color_decontamination"] is False
    assert meta.notes["network_calls"] == 0


def test_matte_is_deterministic() -> None:
    image, mask = rgb_png((9, 9, 9), (20, 20)), png(straight_edge_mask(width=20, height=20))

    first = matte(image, mask)
    second = matte(image, mask)

    assert first.alpha_png == second.alpha_png
