"""Canvas-sized alpha and a transparent cutout — the two asset derivations the CLI and the job
pipeline both need.

Why these live here and not in `cli.py`
---------------------------------------
They were written inside the CLI (`_alpha_on_flat_canvas`, and the cutout block inline in `main`)
because the CLI was the only consumer. The pipeline is the second consumer, and a stage that
re-implements "resize the alpha onto the flat-lay canvas" will eventually disagree with the CLI
about which interpolation, which mode, and whether a same-size alpha is passed through untouched.
Two implementations of one derivation is the same drift this project has spent several tickets
removing from prose; the fix is to have one.

`cli.py` imports these now, so its output stayed byte-identical (asserted by comparing hashes of
`cutout.png` / `prettified.png` before and after the extraction).

Neither function writes a file and neither touches the network: bytes in, bytes out.
"""

from __future__ import annotations

import io

from PIL import Image


def alpha_for_canvas(alpha_png: bytes, flat_png: bytes) -> bytes:
    """Resample `alpha_png` onto `flat_png`'s canvas, or return it unchanged when sizes agree.

    `rectify()` warps the alpha internally but returns only the flat-lay, so the caller often holds
    an alpha in *photo* coordinates. `prettify()` refuses a shape mismatch on purpose — a silently
    rescaled mask is a silently rescaled wrinkle mask — so the resize happens here, visibly, with
    `BILINEAR` and no smoothing beyond it.

    Passing an already-correct alpha through **byte-for-byte** is part of the contract, not an
    optimisation: re-encoding an identical image would change the bytes for no reason.
    """
    with Image.open(io.BytesIO(flat_png)) as opened:
        flat_size = opened.size
    with Image.open(io.BytesIO(alpha_png)) as opened:
        channel = opened.convert("L")
        if channel.size == flat_size:
            return alpha_png
        resized = channel.resize(flat_size, Image.BILINEAR)
    buffer = io.BytesIO()
    resized.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def cutout_png(photo: bytes, alpha_png: bytes) -> bytes:
    """The original photograph composited onto the matte: RGB with `alpha`'s L channel as alpha.

    The delta between the two images is the point — this asset exists so a seller can see what was
    removed. The alpha is used as-is (converted to L, not resized): a mismatched alpha is a bug in
    the caller, and rescaling it here would hide that.
    """
    with Image.open(io.BytesIO(photo)) as rgb:
        base = rgb.convert("RGB")
    with Image.open(io.BytesIO(alpha_png)) as alpha:
        base.putalpha(alpha.convert("L"))
    buffer = io.BytesIO()
    # Same encoder call the CLI used when it saved straight to `cutout.png`; the bytes are identical
    # because format-and-defaults are all that path ever supplied.
    base.save(buffer, format="PNG")
    return buffer.getvalue()
