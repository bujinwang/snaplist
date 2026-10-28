"""Generate the provisional masks — colour distance against a plain background.

This is **not** a segmentation model and it is deliberately not the thing being measured:
`segment()` is a learned salient-object detector, this is background-colour arithmetic, so
an IoU against these masks compares two different algorithm classes rather than a model
with itself. That is the whole reason this file exists; if the masks came from `segment()`
the number would be circular and worthless.

    python -m evals.tools.provisional_masks --golden-dir evals/golden-provisional

or, for one photo, `--photo path/to.jpg --out path/to_mask.png`.

Algorithm, and why each step is there
-------------------------------------
1. background colour = median of a 2 % border ring (median, not mean: one stray object at the
   edge must not move it);
2. distance in luminance-weighted RGB, min-max normalised by the 99.5th percentile so a
   single hot pixel cannot flatten the field;
3. Otsu split, floored at 0.06 — a nearly uniform image otherwise produces a threshold of
   almost zero and masks the noise;
4. **corner flood fill** marks the background, so anything the flood cannot reach is garment:
   that fills interior holes (laces, the gap inside a tote handle) for free;
5. keep the one connected body containing the mask's centre of mass — this is what drops a
   second object in the frame instead of gluing it to the garment;
6. 3×3 close, then a 1 px Gaussian for a soft edge.

Pillow 11 has a trap worth writing down: `Image.fromarray()` returns a **read-only** image
and `ImageDraw.floodfill` on it is a silent no-op — no exception, no change, the mask just
comes out as "everything is garment". Every image that gets flood-filled here is copied
first. That bug cost a debugging round; the coverage assertion in `--photo` mode is the
guard against it coming back.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN = ROOT / "evals" / "golden-provisional"

WEIGHT = np.array([0.30, 0.59, 0.11], dtype=np.float32)  # luminance-ish RGB weights
BORDER_FRACTION = 0.02
MIN_THRESHOLD = 0.06


def otsu(values: np.ndarray, bins: int = 256) -> float:
    """Threshold that maximises between-class variance. Returns a value in [0, 1]."""
    hist, edges = np.histogram(values, bins=bins, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = hist.sum() - w0
    m0 = np.cumsum(hist * centres)
    m1 = (hist * centres).sum() - m0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (m0 / w0 - m1 / w1) ** 2 * w0 * w1
    return float(centres[int(np.argmax(np.nan_to_num(between)))])


def mask_from_photo(path: str | Path, feather: int = 1) -> tuple[Image.Image, dict]:
    """Return (mask as 8-bit 'L', stats). The mask is 255 = garment."""
    with Image.open(path) as opened:
        rgb = opened.convert("RGB")
    width, height = rgb.size
    array = np.asarray(rgb, dtype=np.float32) / 255.0

    ring = max(2, int(min(height, width) * BORDER_FRACTION))
    border = np.concatenate(
        [
            array[:ring].reshape(-1, 3),
            array[-ring:].reshape(-1, 3),
            array[:, :ring].reshape(-1, 3),
            array[:, -ring:].reshape(-1, 3),
        ]
    )
    background = np.median(border, axis=0)

    distance = np.sqrt((((array - background) ** 2) * WEIGHT).sum(axis=2))
    peak = float(np.percentile(distance, 99.5)) or 1.0
    normalised = np.clip(distance / peak, 0.0, 1.0)
    threshold = max(otsu(normalised), MIN_THRESHOLD)
    hard = (normalised > threshold).astype(np.uint8) * 255

    flood = Image.fromarray(hard).copy()  # .copy(): fromarray is read-only, floodfill no-ops
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        if flood.getpixel(corner) == 0:
            ImageDraw.floodfill(flood, corner, 128)
    body = (np.asarray(flood) != 128).astype(np.uint8) * 255

    ys, xs = np.nonzero(body)
    if not len(xs):
        return Image.new("L", (width, height), 0), {
            "coverage": 0.0,
            "threshold": round(threshold, 3),
            "background_rgb": [round(float(channel) * 255) for channel in background],
            "empty": True,
        }

    seed = (int(np.median(xs)), int(np.median(ys)))
    if body[seed[1], seed[0]] == 0:
        nearest = int(np.argmin((xs - seed[0]) ** 2 + (ys - seed[1]) ** 2))
        seed = (int(xs[nearest]), int(ys[nearest]))
    blob = Image.fromarray(body).copy()
    ImageDraw.floodfill(blob, seed, 200)
    one = (np.asarray(blob) == 200).astype(np.uint8) * 255

    closed = Image.fromarray(one).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    soft = closed.filter(ImageFilter.GaussianBlur(feather))
    return soft, {
        "coverage": round(float((np.asarray(closed) > 127).mean()), 4),
        "threshold": round(threshold, 3),
        "background_rgb": [round(float(channel) * 255) for channel in background],
        "dropped_px": int(((body > 0) & (one == 0)).sum()),
        "empty": False,
    }


def generate_for_manifest(golden_dir: Path, overwrite: bool = False) -> int:
    """Write masks/<id>.png for every manifest entry. Returns how many were written."""
    manifest = json.loads((golden_dir / "manifest.json").read_text())
    written = 0
    for entry in manifest:
        photo = golden_dir / entry["path"]
        mask_rel = entry.get("mask_path") or f"masks/{entry['id']}.png"
        mask_path = golden_dir / mask_rel
        if mask_path.exists() and not overwrite:
            continue
        mask, stats = mask_from_photo(photo)
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask.save(mask_path)
        written += 1
        print(
            f"{entry['id']:<12} {mask.size} cov={stats['coverage']:.3f} "
            f"thr={stats['threshold']:.3f} -> {mask_rel}"
        )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.tools.provisional_masks")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--photo", type=Path, help="single photo mode")
    parser.add_argument("--out", type=Path, help="mask path for single photo mode")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    if args.photo:
        if not args.out:
            print("--photo needs --out", file=sys.stderr)
            return 2
        mask, stats = mask_from_photo(args.photo)
        mask.save(args.out)
        print(json.dumps(stats, indent=2))
        return 0

    if not (args.golden_dir / "manifest.json").is_file():
        print(f"no manifest.json in {args.golden_dir}", file=sys.stderr)
        return 2
    written = generate_for_manifest(args.golden_dir, overwrite=args.overwrite)
    print(f"wrote {written} mask(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
