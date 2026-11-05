"""Developer CLI for the vision pipeline. Owner: CV role (E1..E3).

Wires the implemented stages into the product's core asset pack and writes three files into
--out:

    python -m packages.vision.cli --in PHOTO --out DIR [--category top] [--prettify classical]

    cutout.png       garment composited onto transparency (segment -> matte alpha)
    prettified.png   rectified flat-lay on the neutral backdrop (segment -> matte -> rectify
                     -> prettify(--prettify, default CLASSICAL))
    attributes.json  the E3 VLM extractor's validated ItemAttributes, or an explicit
                     `UNMEASURED` marker naming the missing config when no VLM is set

The CLI calls `get_extractor()` with NO argument on purpose: E3-T1 requires the provider be
chosen by configuration (`SNAPLIST_ATTRS_PROVIDER`), not by a code change here or in apps/api.
When the real provider is configured but returns bad JSON twice, the failure is
`low_confidence` (CONTRACTS §4), not `internal`.

`--prettify` defaults to `classical` because that is the mode that runs on any machine without
a 7 GB download (E2-T2 round 1: white balance + wrinkle mask + feathered shadow attenuation).
`--prettify sdxl_inpaint` runs the round-2 generative step and needs `make fetch-sdxl`; when
the weights are absent it fails loudly with the fix inside the error rather than producing a
classical result under a generative label. `--prettify pass_through` returns the flat-lay
untouched. The name printed in the summary is the mode that actually ran, and for
`sdxl_inpaint` the summary also prints the seed, steps, canvas and device — the four numbers
without which a generative latency or a visual regression cannot be reproduced.

`--sdxl-steps` / `--sdxl-seed` / `--sdxl-side` exist so the cost curve in
`docs/HANDOFF-vision.md` can be re-measured without editing constants in the module. They are
refused under any other `--prettify` value instead of being silently ignored: a flag that does
nothing is a flag that lies about what ran.

Per-stage wall clock is printed for every stage and keyed by its backend, because PLAN E2-T2's
acceptance asks for seconds per backend rather than one total that hides which stage costs.

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
from packages.vision.prettify import prettify, prettify_sdxl
from packages.vision.rectify import rectify
from packages.vision.segment import SegmentResult, segment
from packages.vision.types import PrettifyMode, SegmentBackend

_DEFAULT_CATEGORY = "top"
_DEFAULT_PRETTIFY = PrettifyMode.CLASSICAL.value


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
    p.add_argument(
        "--prettify",
        dest="prettify_mode",
        choices=[mode.value for mode in PrettifyMode],
        default=_DEFAULT_PRETTIFY,
        help=(
            "prettify mode: classical (white balance + wrinkle mask + shadow attenuation, "
            "no model) | pass_through (bytes untouched) | sdxl_inpaint (generative inpaint; "
            "needs `make fetch-sdxl`)"
        ),
    )
    p.add_argument(
        "--sdxl-steps",
        type=int,
        metavar="N",
        default=None,
        help="sdxl_inpaint: diffusion steps (default SDXL_STEPS in prettify.py)",
    )
    p.add_argument(
        "--sdxl-seed",
        type=int,
        metavar="N",
        default=None,
        help="sdxl_inpaint: generator seed, for reproducing one exact output",
    )
    p.add_argument(
        "--sdxl-side",
        type=int,
        metavar="PX",
        default=None,
        help="sdxl_inpaint: working long edge, multiple of 64 (default SDXL_WORK_SIDE)",
    )
    return p


def _coverage(mask_png: bytes) -> float:
    with Image.open(io.BytesIO(mask_png)) as opened:
        arr = np.asarray(opened.convert("L"))
    return round(float((arr >= 128).mean()), 4)


def _alpha_on_flat_canvas(alpha_png: bytes, flat_png: bytes) -> bytes:
    """Resample the alpha onto the flat-lay canvas when the two disagree.

    `rectify()` warps the alpha internally but returns only the flat-lay, so the alpha this
    CLI holds is still in *photo* coordinates. Prettify refuses a shape mismatch on purpose
    (a silently rescaled mask is a silently rescaled wrinkle mask), so the resize happens
    here, visibly, and is a no-op once rectify returns its own alpha.
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


def _fmt_seconds(meta_stage: str, backend: object, seconds: float | None) -> str:
    """One line per backend, so the report can attribute the wall clock to a stage."""
    shown = "UNMEASURED" if seconds is None else f"{seconds:.3f}s"
    return f"  {meta_stage:<17} backend={backend} seconds={shown}"


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
    mode = PrettifyMode(args.prettify_mode)
    photo = src.read_bytes()

    started = time.perf_counter()
    try:
        seg: SegmentResult = segment(photo, backend)
    except StageNotImplemented as exc:
        print(f"SEGMENT UNVAILABLE: {exc}", file=sys.stderr)
        return 2

    mt = matte(photo, seg.mask_png)
    rect = rectify(photo, mt.alpha_png, args.category)
    alpha_for_flat = _alpha_on_flat_canvas(mt.alpha_png, rect.flat_png)

    sdxl_tuning = {
        "steps": args.sdxl_steps,
        "seed": args.sdxl_seed,
        "side": args.sdxl_side,
    }
    if mode is not PrettifyMode.SDXL_INPAINT:
        supplied = [name for name, value in sdxl_tuning.items() if value is not None]
        if supplied:
            print(
                f"--sdxl-* only applies to --prettify sdxl_inpaint, not {mode.value}; "
                f"refusing to accept {supplied} and ignore them",
                file=sys.stderr,
            )
            return 2
    try:
        if mode is PrettifyMode.SDXL_INPAINT:
            pt = prettify_sdxl(rect.flat_png, alpha_for_flat, **sdxl_tuning)
        else:
            pt = prettify(rect.flat_png, alpha_for_flat, mode=mode)
    except StageNotImplemented as exc:
        # A mode that this build cannot run is a terminal, deterministic failure. Printing the
        # refusal (which carries the fix) is the whole point of raising it.
        print(f"PRETTIFY UNAVAILABLE: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started

    # cutout = original photo composited onto the matte alpha
    with Image.open(io.BytesIO(photo)) as rgb:
        base = rgb.convert("RGB")
    with Image.open(io.BytesIO(mt.alpha_png)) as alpha:
        base.putalpha(alpha.convert("L"))
    base.save(dst / "cutout.png")

    # prettified = rectified flat-lay through the selected prettify mode
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
            # Real token counts make BRIEF.md's <= $0.03/item ceiling observable. They were
            # summed across the repair retry in the adapter; surface them for the cost report.
            attributes_doc["prompt_tokens"] = meta.notes.get("prompt_tokens")
            attributes_doc["completion_tokens"] = meta.notes.get("completion_tokens")
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
    generative = pt.meta.notes.get("generative") or {}
    print(
        f"OK  backend={seg.meta.backend} device={seg.meta.device} "
        f"category={args.category} mask_coverage={cov} "
        f"prettify={pt.meta.notes.get('mode')} total={elapsed:.2f}s\n"
        + "\n".join(
            [
                _fmt_seconds("segment", seg.meta.backend, seg.meta.seconds),
                _fmt_seconds("matte", mt.meta.backend, mt.meta.seconds),
                _fmt_seconds("rectify", rect.meta.backend, rect.meta.seconds),
                _fmt_seconds("prettify", pt.meta.backend, pt.meta.seconds),
            ]
        )
        + (
            "\n  generative         "
            f"device={pt.meta.device} steps={generative.get('num_inference_steps')} "
            f"strength={generative.get('strength')} seed={pt.meta.notes.get('seed')} "
            f"canvas={generative.get('work_canvas')} "
            f"load={generative.get('model_load_s')}s "
            f"generate={generative.get('generate_s')}s "
            f"repaint_px={generative.get('repaint_px')}"
            if generative.get("model_loaded")
            else ""
        )
        + f"\n  cutout     -> {dst / 'cutout.png'}\n"
        f"  prettified -> {dst / 'prettified.png'}\n"
        f"  attributes -> {dst / 'attributes.json'} ({attributes_doc['status']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
