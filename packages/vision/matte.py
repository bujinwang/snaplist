"""STAGE 1b — alpha matting around the segmentation mask. Owner: CV role (E1, ticket E1-T3).

`matte()` refines a hard segmentation mask into a soft alpha channel so straps, knit
edges and fabric frizz survive compositing onto the neutral backdrop. Cheap, deterministic,
**zero network calls**: this is the stage that must never be a generative model.

    matte(image: bytes, mask_png: bytes) -> MatteResult   # alpha_png + meta

Algorithm (classical feather, v1)
---------------------------------
1. Normalize the mask to 8-bit single-channel.
2. Feather it with a 1.0σ Gaussian — roughly one pixel of soft edge on each side.
3. Keep the interior exactly opaque: wherever the mask is 255, alpha is 255 (a blur
   would eat into a 1px strap and leave it see-through).
4. Hard-zero anything that rounds to 0: the feather's tail is 3px wide in theory, but
   beyond 2px it rounds away — and the `spread` guard clamps the band anyway. The DoD's
   "no halo wider than 2px" is a property of the output, and `test_matte.py` proves it
   pixel-by-pixel rather than trusting the maths.

What this version deliberately does not do: colour decontamination. The `image` bytes are
decoded (so garbage input fails loudly here, not three stages later) but their pixels are
not read yet — v1 refinement is geometry-only. When colour spill shows up in the prettify
composites, this is the file that grows the decontamination pass, and `meta.notes` is where
the switch gets recorded.

The signature is frozen by `test_public_signatures_are_stable`; changing it is a contract
change (`docs/CONTRACTS.md`), not a refactor.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from packages.vision.types import StageMeta

FEATHER_SIGMA_PX = 1.0
HALO_BAND_PX = 2
_BACKEND = "local_feather_v1"


@dataclass(slots=True)
class MatteResult:
    alpha_png: bytes
    meta: StageMeta


def _decode_l(png_bytes: bytes, what: str) -> Image.Image:
    """Decode PNG bytes to 8-bit single-channel, or raise ValueError with the culprit named."""
    try:
        with Image.open(io.BytesIO(png_bytes)) as opened:
            frame = opened.load()  # force the decode: a truncated file fails here
            del frame
            if opened.mode in ("RGBA", "LA", "PA"):
                return opened.getchannel("A").copy()
            return opened.convert("L").copy()
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - PIL raises a zoo of formats for bad bytes
        raise ValueError(f"matte(): could not decode {what} bytes as an image ({exc})") from exc


def matte(image: bytes, mask_png: bytes) -> MatteResult:
    """Refine a hard mask into a soft alpha channel. Deterministic, offline, pure."""
    if not image:
        raise ValueError("matte(): image bytes are empty")
    if not mask_png:
        raise ValueError("matte(): mask_png bytes are empty")
    _decode_l(image, "image")  # validated, not yet used: geometry-only refinement in v1

    started = time.perf_counter()

    mask = np.asarray(_decode_l(mask_png, "mask"), dtype=np.uint8)
    soft = np.asarray(
        Image.fromarray(mask).filter(ImageFilter.GaussianBlur(radius=FEATHER_SIGMA_PX)),
        dtype=np.float64,
    )
    # The band guard: exact Chebyshev dilation of the mask by HALO_BAND_PX (MaxFilter
    # with a (2r+1)² kernel). A Gaussian guard would leak: PIL's blur is a box
    # approximation whose tail leaves alpha=1 residue at 3px, which is exactly the halo
    # the DoD forbids. Dilation is geometry, not numerics — it cannot leak.
    band = (
        np.asarray(
            Image.fromarray(mask).filter(ImageFilter.MaxFilter(2 * HALO_BAND_PX + 1)),
            dtype=np.uint8,
        )
        > 0
    )

    interior = mask == 255
    alpha = np.rint(soft).astype(np.int64)
    alpha = np.maximum(alpha, np.where(interior, 255, 0))  # interior stays exactly opaque
    alpha = np.where(band, alpha, 0)  # no residue beyond the band
    alpha = np.clip(alpha, 0, 255).astype(np.uint8)

    alpha_png = _encode(alpha)
    seconds = time.perf_counter() - started

    meta = StageMeta(
        stage="matte",
        backend=_BACKEND,
        device="cpu",
        seconds=seconds,  # measured wall-clock; the harness treats None as UNMEASURED
        notes={
            "feather_sigma_px": FEATHER_SIGMA_PX,
            "halo_band_px": HALO_BAND_PX,
            "color_decontamination": False,  # v1 is geometry-only; see module docstring
            "network_calls": 0,
            "mask_px": int((mask > 0).sum()),
        },
    )
    return MatteResult(alpha_png=alpha_png, meta=meta)


def _encode(alpha: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(alpha).save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()
