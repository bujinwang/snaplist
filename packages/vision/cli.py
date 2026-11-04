"""Developer CLI for the vision pipeline. Owner: CV role (E1..E3).

Wires the implemented stages into the product's core asset pack and writes three files into
--out:

    python -m packages.vision.cli --in PHOTO --out DIR [--category top]

    cutout.png       garment composited onto transparency (segment -> matte alpha)
    prettified.png   rectified flat-lay on the neutral backdrop (segment -> matte -> rectify
                     -> prettify(PASS_THROUGH))
    attributes.json  the E3 VLM extractor's validated ItemAttributes, or an explicit
                     `UNMEASURED` marker naming the missing config when no VLM is set

The CLI calls `get_extractor()` with NO argument on purpose: E3-T1 requires the provider be
chosen by configuration (`SNAPLIST_ATTRS_PROVIDER`), not by a code change here or in apps/api.
When the real provider is configured but returns bad JSON twice, the failure is
`low_confidence` (CONTRACTS §4), not `internal`.

Stages that are not implemented yet (prettify.SDXL_INPAINT) fail loudly with a reason rather
than producing a silent pass-through that looks like a result.

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
from packages.vision.attrs import (
    AttributeExtractionFailed,
    AttributesUnavailable,
    get_extractor,
)
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

    # attributes: config-driven extractor. No VLM configured => explicit UNMEASURED marker,
    # never a fabricated record and never a silent stub.
    try:
        extractor = get_extractor()
        attrs = extractor.extract(photo)
        attributes_doc: dict = {
            "status": "measured",
            "provider": extractor.name,
            "attributes": json.loads(attrs.model_dump_json()),
        }
        meta = getattr(extractor, "last_meta", None)
        if meta is not None:
            attributes_doc["round_trip_seconds"] = meta.seconds
            attributes_doc["cost_usd"] = meta.cost_usd  # None == UNMEASURED, not 0.0
    except AttributesUnavailable as exc:
        attributes_doc = {
            "status": "UNMEASURED",
            "stage": "attrs (E3)",
            "reason": str(exc),
            "note": "docs/RUNBOOK.md §6: the VLM provider decision is Bert's and is still "
            "open. No attributes are guessed; accuracy is UNMEASURED.",
        }
    except AttributeExtractionFailed as exc:
        attributes_doc = {
            "status": "failed",
            "stage": "attrs (E3)",
            "reason_code": exc.reason_code,
            "attempts": exc.attempts,
            "detail": str(exc),
        }

    (dst / "attributes.json").write_text(json.dumps(attributes_doc, indent=2))

    cov = _coverage(seg.mask_png)
    print(
        f"OK  backend={seg.meta.backend} device={seg.meta.device} "
        f"category={args.category} mask_coverage={cov} "
        f"prettify={pt.meta.notes.get('mode')} total={elapsed:.2f}s\n"
        f"  cutout     -> {dst / 'cutout.png'}\n"
        f"  prettified -> {dst / 'prettified.png'}\n"
        f"  attributes -> {dst / 'attributes.json'} ({attributes_doc['status']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
