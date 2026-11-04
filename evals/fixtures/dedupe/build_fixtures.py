"""Generate `evals/fixtures/dedupe/` — the E3-T4 false-merge fixture (docs/PLAN.md ticket E3-T4).

Run from the repo root with the project venv:

    .venv/bin/python -m evals.fixtures.dedupe.build_fixtures

What it writes
--------------
* `base/<source-id>.jpg`   — a licensed photo from `evals/golden-provisional/photos/`,
                             re-encoded to at most 800 px on the long edge.
* `exact/<pair-id>.jpg`    — a **byte-for-byte copy** of its base file. This is what the exact
                             layer (`sha256`) is for: the same upload stored twice.
* `variant/<pair-id>.jpg`  — a *transform* of its base file (scale / re-encode / mild crop /
                             white padding / brightness nudge). Same source, different bytes,
                             so only the perceptual layer can catch it.
* `manifest.json`          — pair id, `a_path`, `b_path`, `label` (`duplicate`/`distinct`),
                             `kind` (`exact`/`near`/null), why that label, provenance, licence.

Two layers, because `apps/api/requests.py` already fixed the key
----------------------------------------------------------------
`CreateItemRequest.source_sha256` is the dedupe key and predates this stage. So the fixture has
to exercise both halves of `find_duplicate()`:

* `kind: "exact"` — `b` is the same bytes as `a`. Detected by digest, no thresholds, no risk.
* `kind: "near"`  — `b` is a transform of `a`. Only the perceptual layer can catch this, and it
  is the only kind whose recall is worth reporting.

Why the positives are generated instead of collected
----------------------------------------------------
A real duplicate upload is one photograph stored twice, and a real near-duplicate is that
photograph re-encoded on the way. `evals/golden-provisional/` holds 17 photos, one per item, so
it contains **zero** duplicate pairs of either kind. Deriving them from licensed photos is the
only honest option, and `README.md` says what that costs: exact pairs are a digest equality (a
definition), near pairs are same-source transforms (so recall is an upper bound, threshold
in-sample).

Why the negatives are all from the same 17 photos
-------------------------------------------------
Every negative pair is two *different items* — distinguishable by accession number in
`CREDITS.md` (e.g. `Breeches (AM 1973.193-1)` vs `-2`). They are chosen to be hard: six of the
ten are tan riding breeches on a white sweep, i.e. same category, same colour family, same
studio. An easy negative (a black boot against a white tee) proves nothing.

Licences: every source file is CC0 / public domain / CC BY, recorded in
`evals/golden-provisional/CREDITS.md`; `CREDITS.md` next to this script carries the
attribution chain for the twelve sources actually used.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageEnhance

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
SOURCE_DIR = REPO_ROOT / "evals" / "golden-provisional" / "photos"

MAX_SIDE = 800
BASE_QUALITY = 82
WHITE = (255, 255, 255)


# --------------------------------------------------------------------------- transforms
# Every transform is a pure function of its input image plus literal constants, so two runs
# produce byte-identical files (PIL's JPEG encoder is deterministic for fixed parameters).


def _resize_long_edge(image: Image.Image, target: int) -> Image.Image:
    """Scale so the long edge is exactly `target` px. Upscaling is refused (it adds nothing)."""
    width, height = image.size
    long_edge = max(width, height)
    if long_edge == target:
        return image.copy()
    scale = target / long_edge
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(size, Image.LANCZOS)


def scale(image: Image.Image, fraction: float) -> Image.Image:
    """Shrink to `fraction` of the long edge. The classic "sent through a chat app" case."""
    return _resize_long_edge(image, max(16, round(max(image.size) * fraction)))


def recompress(image: Image.Image, quality: int) -> Image.Image:
    """Round-trip through a low-quality JPEG to inject compression artefacts.

    The pixels change (blocking, chroma bleed); the geometry does not. Returns a decoded
    image so the caller still controls the final encode.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    with Image.open(buffer) as reopened:
        return reopened.convert("RGB")


def pad_white(image: Image.Image, fraction: float) -> Image.Image:
    """Add a white border of `fraction` × the long edge on every side (the screenshot case)."""
    width, height = image.size
    pad = max(1, round(max(width, height) * fraction))
    canvas = Image.new("RGB", (width + 2 * pad, height + 2 * pad), WHITE)
    canvas.paste(image, (pad, pad))
    return canvas


def centre_crop(
    image: Image.Image, fraction: float, dx: float = 0.0, dy: float = 0.0
) -> Image.Image:
    """Keep the central `fraction` of each side, shifted by `dx`/`dy` × the crop size.

    Models a seller cropping the frame before uploading. The shift keeps the crop from being
    a perfectly symmetric operation, which would be easier to match than the real thing.
    """
    width, height = image.size
    crop_w = max(8, round(width * fraction))
    crop_h = max(8, round(height * fraction))
    left = round((width - crop_w) / 2 + dx * crop_w)
    top = round((height - crop_h) / 2 + dy * crop_h)
    left = min(max(0, left), width - crop_w)
    top = min(max(0, top), height - crop_h)
    return image.crop((left, top, left + crop_w, top + crop_h))


def brightness(image: Image.Image, factor: float) -> Image.Image:
    """Multiply the luminance by `factor`. 1.0 is identity."""
    return ImageEnhance.Brightness(image).enhance(factor)


# --------------------------------------------------------------------------- pair recipes

#: (pair id, source id) — `b` is a byte-for-byte copy of `a`. Four are enough to prove the
#: exact layer is not a fluke; each one is a digest equality, so more of them add no
#: information about capability, only about the manifest plumbing. The rest of the duplicate
#: pairs go to the layer that can actually be wrong.
EXACT_RECIPES: tuple[tuple[str, str], ...] = (
    ("exact-01", "bottom-03"),
    ("exact-02", "shoe-01"),
    ("exact-03", "top-01"),
)

#: (pair id, source id, human description, transform callable) — `b` is a same-source transform
#: of `a`: different bytes, so the exact layer cannot see it and the perceptual layer must.
#: Seven keeps every transform class represented (scale, pad, crop, exposure, re-encode) without
#: spending pairs on near-identical variations of one idea.
NEAR_RECIPES: tuple[tuple[str, str, str, object], ...] = (
    (
        "near-01",
        "bottom-01",
        "scaled to 65% of the long edge, then re-encoded at JPEG q=60",
        lambda im: recompress(scale(im, 0.65), 60),
    ),
    (
        "near-02",
        "bottom-02",
        "white border of 12% of the long edge added on every side",
        lambda im: pad_white(im, 0.12),
    ),
    (
        "near-03",
        "bottom-04",
        "centre crop to 88% with a 2% shift, then a 6% white border",
        lambda im: pad_white(centre_crop(im, 0.88, dx=0.02, dy=-0.01), 0.06),
    ),
    (
        "near-04",
        "bottom-05",
        "brightness -12% with JPEG q=55 re-encode",
        lambda im: recompress(brightness(im, 0.88), 55),
    ),
    (
        "near-05",
        "shoe-03",
        "centre crop to 90% shifted 3%, brightness +6%",
        lambda im: brightness(centre_crop(im, 0.90, dx=-0.03, dy=0.02), 1.06),
    ),
    (
        "near-06",
        "outer-01",
        "white border of 10% then JPEG q=70 re-encode",
        lambda im: recompress(pad_white(im, 0.10), 70),
    ),
    (
        "near-07",
        "bottom-06",
        "scaled to 55% of the long edge, then re-encoded at JPEG q=65",
        lambda im: recompress(scale(im, 0.55), 65),
    ),
)

# (pair id, source a, source b, why these two different items are hard to tell apart)
DISTINCT_RECIPES: tuple[tuple[str, str, str, str], ...] = (
    (
        "dist-01",
        "bottom-01",
        "bottom-02",
        "same breeches series (AM 1973.193-1 vs -2): same tan wool, same white sweep, "
        "same camera, similar standing pose; only the fold of the legs differs",
    ),
    (
        "dist-02",
        "bottom-03",
        "bottom-04",
        "same series again (-3 vs -4): both shot from the same height with the legs "
        "overlapping, so the silhouettes occupy almost the same box",
    ),
    (
        "dist-03",
        "bottom-05",
        "bottom-06",
        "the two palest pairs in the set — a near-white garment on a white sweep, so the "
        "colour histogram of both is dominated by the backdrop",
    ),
    (
        "dist-04",
        "bottom-01",
        "bottom-04",
        "same tan hue to within a few RGB units; the difference is pose, which is exactly "
        "what a colour-only matcher throws away",
    ),
    (
        "dist-05",
        "bottom-02",
        "bottom-05",
        "one pair is scaled small in frame, the other large: a scale-sensitive descriptor "
        "scores these as dissimilar for the wrong reason, a scale-invariant one has to work",
    ),
    (
        "dist-06",
        "bottom-03",
        "bottom-06",
        "same series, near-identical framing; the crop tightness is the only cue a naive "
        "bounding-box matcher would latch onto",
    ),
    (
        "dist-07",
        "shoe-01",
        "shoe-02",
        "both are dark footwear on a white sweep, both occupy the lower half of the frame, "
        "and both are small in frame — colour and layout both point the wrong way",
    ),
    (
        "dist-08",
        "shoe-02",
        "shoe-03",
        "both are a *pair* of shoes side by side, laid out left-to-right at the same angle; "
        "a layout-only matcher cannot separate them",
    ),
    (
        "dist-09",
        "top-01",
        "top-02",
        "both are white tops; one is a sleeveless jersey on navy, the other a plain tee on "
        "a brown sweep, so the garment colour matches while the surroundings do not",
    ),
    (
        "dist-10",
        "outer-01",
        "bottom-06",
        "the tan jacket and the cream breeches are within a few units of each other in mean "
        "colour, on the same white sweep, at similar scale",
    ),
)

USED_SOURCES: tuple[str, ...] = tuple(
    dict.fromkeys(
        [source for _, source in EXACT_RECIPES]
        + [source for _, source, _, _ in NEAR_RECIPES]
        + [source for _, a, b, _ in DISTINCT_RECIPES for source in (a, b)]
    )
)

LICENCES: dict[str, str] = {
    "bottom-01": "CC BY 4.0, author not researched (Auckland Museum) — attribution in CREDITS.md",
    "bottom-02": "CC BY 4.0, author not researched (Auckland Museum) — attribution in CREDITS.md",
    "bottom-03": "CC BY 4.0, author not researched (Auckland Museum) — attribution in CREDITS.md",
    "bottom-04": "CC BY 4.0, author not researched (Auckland Museum) — attribution in CREDITS.md",
    "bottom-05": "CC BY 4.0, author not researched (Auckland Museum) — attribution in CREDITS.md",
    "bottom-06": "CC0",
    "outer-01": "CC0",
    "shoe-01": "CC BY 4.0, Free open sources aholaS — attribution in CREDITS.md",
    "shoe-02": "CC0",
    "shoe-03": "CC BY 2.0, SPERA.de Designerschuhe — attribution in CREDITS.md",
    "top-01": "CC BY 2.0, Erik Drost — attribution in CREDITS.md",
    "top-02": "Public domain",
}


def _write_jpeg(image: Image.Image, path: Path, quality: int) -> None:
    if not path.parent.is_dir():
        path.parent.mkdir(parents=True)
    image.convert("RGB").save(path, format="JPEG", quality=quality, optimize=False)


def _prune_stale(directory: Path, keep: set[str]) -> list[str]:
    """Delete *.jpg files this run did not write. Returns the names removed.

    A stale fixture image is worse than a missing one: it still counts against the size budget
    and it still looks like part of the set, so a renamed pair would leave debris nobody
    notices. The fixture is reproducible, so deleting is safe.
    """
    removed: list[str] = []
    for path in sorted(directory.glob("*.jpg")):
        if path.name not in keep:
            path.unlink()
            removed.append(path.name)
    return removed


def main() -> int:
    if not SOURCE_DIR.is_dir():
        raise SystemExit(f"missing source directory: {SOURCE_DIR}")

    base_dir = HERE / "base"
    exact_dir = HERE / "exact"
    variant_dir = HERE / "variant"
    for directory in (base_dir, exact_dir, variant_dir):
        if not directory.is_dir():
            directory.mkdir(parents=True)

    # 1. Bases: one per used source, long edge exactly MAX_SIDE (or smaller, never upscaled).
    base_images: dict[str, Image.Image] = {}
    for source_id in USED_SOURCES:
        source_path = SOURCE_DIR / f"{source_id}.jpg"
        if not source_path.is_file():
            raise SystemExit(f"missing licensed source photo: {source_path}")
        with Image.open(source_path) as opened:
            image = opened.convert("RGB")
        image = _resize_long_edge(image, min(MAX_SIDE, max(image.size)))
        _write_jpeg(image, base_dir / f"{source_id}.jpg", BASE_QUALITY)
        base_images[source_id] = image

    removed = _prune_stale(base_dir, {f"{source_id}.jpg" for source_id in USED_SOURCES})

    pairs: list[dict[str, object]] = []

    # 2. Exact pairs: copy the base file's bytes. Not a re-encode — a re-encode would change the
    #    digest and this pair would quietly stop testing the exact layer.
    for pair_id, source_id in EXACT_RECIPES:
        base_path = base_dir / f"{source_id}.jpg"
        base_bytes = base_path.read_bytes()
        target = exact_dir / f"{pair_id}.jpg"
        target.write_bytes(base_bytes)
        if target.read_bytes() != base_bytes:  # pragma: no cover - defensive
            raise SystemExit(f"copy of {base_path} is not byte-identical: {target}")
        pairs.append(
            {
                "id": pair_id,
                "a_path": f"base/{source_id}.jpg",
                "b_path": f"exact/{pair_id}.jpg",
                "label": "duplicate",
                "kind": "exact",
                "transform": "byte-for-byte copy (nothing changed, not even the encoding)",
                "why": (
                    "the same bytes stored twice: sha256(a) == sha256(b). This pair is decided "
                    "by the digest layer, so no perceptual threshold can change its verdict."
                ),
                "source": f"evals/golden-provisional/photos/{source_id}.jpg",
                "licence": LICENCES[source_id],
            }
        )
    removed += _prune_stale(exact_dir, {f"{pair_id}.jpg" for pair_id, _ in EXACT_RECIPES})

    # 3. Near pairs: derived from their own base file, read back from disk so the chain
    #    base -> variant is the same one the fixture is scored on.
    for pair_id, source_id, description, transform in NEAR_RECIPES:
        base_path = base_dir / f"{source_id}.jpg"
        with Image.open(base_path) as opened:
            base = opened.convert("RGB")
        variant = transform(base)  # type: ignore[operator]
        if max(variant.size) > MAX_SIDE:
            variant = _resize_long_edge(variant, MAX_SIDE)
        variant_path = variant_dir / f"{pair_id}.jpg"
        _write_jpeg(variant, variant_path, BASE_QUALITY)
        pairs.append(
            {
                "id": pair_id,
                "a_path": f"base/{source_id}.jpg",
                "b_path": f"variant/{pair_id}.jpg",
                "label": "duplicate",
                "kind": "near",
                "transform": description,
                "why": (
                    "b is a transform of a, not an independent second photograph: same source "
                    "file, same pixels, different scale/encoding/framing — and different bytes, "
                    "so the digest layer cannot catch it. Recall on this pair is an upper bound "
                    "(see README.md)."
                ),
                "source": f"evals/golden-provisional/photos/{source_id}.jpg",
                "licence": LICENCES[source_id],
            }
        )
    removed += _prune_stale(variant_dir, {f"{pair_id}.jpg" for pair_id, _, _, _ in NEAR_RECIPES})

    # 4. Negatives: two different physical items, no transform anywhere.
    for pair_id, source_a, source_b, why in DISTINCT_RECIPES:
        pairs.append(
            {
                "id": pair_id,
                "a_path": f"base/{source_a}.jpg",
                "b_path": f"base/{source_b}.jpg",
                "label": "distinct",
                "kind": None,
                "transform": None,
                "why": why,
                "source": (
                    f"evals/golden-provisional/photos/{source_a}.jpg; "
                    f"evals/golden-provisional/photos/{source_b}.jpg"
                ),
                "licence": f"{LICENCES[source_a]}; {LICENCES[source_b]}",
            }
        )

    manifest = {
        "fixture": "dedupe",
        "version": 2,
        "ticket": "E3-T4",
        "note": (
            "Two layers over one key. kind=exact: b is byte-identical to a, decided by sha256, "
            "no threshold involved. kind=near: b is a same-source transform of a, decided by "
            "the perceptual layer, so its recall is an upper bound and the threshold is "
            "in-sample. label=distinct: two different physical objects (distinct accession "
            "numbers in evals/golden-provisional/CREDITS.md), never two photos of one item. "
            "Paths are relative to this directory."
        ),
        "n_pairs": len(pairs),
        "n_duplicate": sum(1 for p in pairs if p["label"] == "duplicate"),
        "n_exact": sum(1 for p in pairs if p["kind"] == "exact"),
        "n_near": sum(1 for p in pairs if p["kind"] == "near"),
        "n_distinct": sum(1 for p in pairs if p["label"] == "distinct"),
        "max_side_px": MAX_SIDE,
        "pairs": pairs,
    }
    with (HERE / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    images = [
        *base_dir.glob("*.jpg"),
        *exact_dir.glob("*.jpg"),
        *variant_dir.glob("*.jpg"),
    ]
    total_bytes = sum(path.stat().st_size for path in images)
    if removed:
        print(f"pruned {len(removed)} stale image(s): {', '.join(sorted(removed))}")
    print(
        f"wrote {len(pairs)} pairs ({manifest['n_exact']} exact / {manifest['n_near']} near / "
        f"{manifest['n_distinct']} distinct), {len(base_images)} base + "
        f"{len(EXACT_RECIPES)} exact + {len(NEAR_RECIPES)} variant images, "
        f"{total_bytes / 1_000_000:.2f} MB total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
