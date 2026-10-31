"""Developer CLI for the vision pipeline. Owner: CV role (E1..E3).

Wires the implemented stages into the product's core asset pack and writes three files into
--out:

    python -m packages.vision.cli --in PHOTO --out DIR [--category top]

    cutout.png       garment composited onto transparency (segment -> matte alpha)
    prettified.png   rectified flat-lay on the neutral backdrop (segment -> matte -> rectify
                     -> prettify(PASS_THROUGH))
    attributes.json  honest "not implemented" marker — the VLM attribute extractor (E3) has
                     not landed, so no fake JSON is emitted

Stages that are not implemented yet (prettify.SDXL_INPAINT, attrs) fail loudly with a
reason rather than producing a silent pass-through that looks like a result.

The acceptance command from prompts/02-vision.md must keep working verbatim once the stages
land. `category` defaults to "top" so `--in evals/fixtures/tee.jpg --out /tmp/out` runs
without it; a real garment needs its own category (or the E3 classifier once it lands).
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from packages.vision import StageNotImplemented
from packages.vision.matte import matte
from packages.vision.prettify import prettify
from packages.vision.rectify import rectify
from packages.vision.segment import SegmentResult, segment
from packages.vision.types import PrettifyMode, SegmentBackend

_DEFAULT_CATEGORY = "top"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m packages.vision.cli")
    p.add_argument("--in", dest="src", required=True, help="input photo")
    p.add_argument("--out", dest="dst", required=True, help="output directory")
    p.add_argument(
        "--category",
        default=_DEFAULT_CATEGORY,
        help="canonical flat-lay category for rectify (E3 classifier replaces this)",
    )
    p.add_argument(
        "--backend",
        default=None,
        help="override segmentation backend (default: runtime.probe())",
    )
    return p


def _coverage(mask_png: bytes) -> float:
    with Image.open(io.BytesIO(mask_png)) as opened:
        arr = np.asarray(opened.convert("L"))
    return round(float((arr >= 128).mean()), 4)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = Path(args.src)
    dst = Path(args.dst)
    if not src.is_file():
        print(f"NO INPUT: {src} does not exist", file=sys.stderr)
        return 2
    dst.mkdir(parents=True, exist_ok=True)

    from packages.vision.runtime import probe

    runtime = probe()
    backend = SegmentBackend(args.backend) if args.backend else runtime.backend
    photo = src.read_bytes()

    started = time.perf_counter()
    try:
        seg: SegmentResult = segment(photo, backend)
    except StageNotImplemented as exc:
        print(f"SEGMENT UNVAILABLE: {exc}", file=sys.stderr)
        return 2

    mt = matte(photo, seg.mask_png)
    rect = rectify(photo, mt.alpha_png, args.category)
    pt = prettify(rect.flat_png, mt.alpha_png, mode=PrettifyMode.PASS_THROUGH)
    elapsed = time.perf_counter() - started

    # cutout = original photo composited onto the matte alpha
    with Image.open(io.BytesIO(photo)) as rgb:
        base = rgb.convert("RGB")
    with Image.open(io.BytesIO(mt.alpha_png)) as alpha:
        base.putalpha(alpha.convert("L"))
    base.save(dst / "cutout.png")

    # prettified = rectified flat-lay, pass-through prettify
    (dst / "prettified.png").write_bytes(pt.prettified_png)

    # attributes: not implemented — emit an honest marker, never a fabricated record
    (dst / "attributes.json").write_text(
        json.dumps(
            {
                "status": "unimplemented",
                "stage": "attrs (E3)",
                "note": "VLM attribute extractor not landed; see docs/PLAN.md. "
                "No attributes are guessed.",
            },
            indent=2,
        )
    )

    cov = _coverage(seg.mask_png)
    print(
        f"OK  backend={seg.meta.backend} device={seg.meta.device} "
        f"category={args.category} mask_coverage={cov} "
        f"prettify={pt.meta.notes.get('mode')} total={elapsed:.2f}s\n"
        f"  cutout     -> {dst / 'cutout.png'}\n"
        f"  prettified -> {dst / 'prettified.png'}\n"
        f"  attributes -> {dst / 'attributes.json'} (unimplemented marker)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
