"""Builds `evals/fixtures/prettify/**` — the E2-T2 logo-protection and crease fixtures.

    .venv/bin/python evals/fixtures/prettify/build_fixtures.py

Owner: CV role. Ticket: `docs/PLAN.md` E2-T2. Consumers: `evals/metrics/prettify.py`,
`packages/vision/tests/test_prettify.py`, `packages/vision/prettify.py`.

Why the fixtures are **synthesised on top of licensed photos**
--------------------------------------------------------------
Two things have to be true for the DoD line *"the inpaint mask never overlaps the logo/text
region"* to be testable, and neither can be had from a museum catalogue:

1. a known-good rectangle that is known to contain print, and
2. a garment region where a wrinkle detector genuinely wants to fire **inside** that
   rectangle — otherwise subtracting the protection region would be a no-op, the test would
   pass with the subtraction deleted, and the fixture would be measuring nothing.

So each `logo-*` case draws a synthetic label/wordmark and then runs a fold **across the
label**, and records both boxes as ground truth. The overlay is drawn by this script from a
bitmap font — **no logo is downloaded, and these are not the marks of any real product**. The
consequence, stated where the numbers are quoted: the fixtures measure "the protection region
is not invaded", *not* "the detector recalls real packaging logos".

The `crease-*` cases draw folds (a dark band with an adjacent highlight) on flat fabric for
the same reason in reverse: catalogue photography is pressed and evenly lit, so there are no
real creases in `evals/golden-provisional/` to measure recall on. They record `crease_box` so
a test can assert "the mask did move something there", which is all the material supports.

Determinism: pure numpy + Pillow, no randomness, no network. The generated images are
committed so the tests never depend on re-running this script.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIR = ROOT / "evals" / "golden-provisional"
OUT_DIR = Path(__file__).resolve().parent

# Long edge of every fixture. Small on purpose: 9 cases have to fit in <= 4 MB, and the
# geometry of a crease or a label does not need resolution to be testable.
LONG_EDGE = 480
BACKDROP = (242, 241, 238)
# A deliberate camera white-balance error applied to every fixture (~3–4° warm). Without it
# the input's backdrop is *exactly* #F2F1EE — which is what `rectify()` composites — and the
# white-balance step would be a no-op on every fixture, i.e. measured as identity. A real
# phone's auto-white-balance does not land on the backdrop, so the fixture carries the error
# the stage exists to remove.
CAMERA_CAST = (1.035, 1.000, 0.960)
# Same boundary rule as `packages/vision/prettify.py` (which owns it): overlays and creases
# must live inside what the stage is allowed to touch, or the fixture would be asking the
# stage to violate a constraint it is measured on.
BOUNDARY_FRACTION = 0.015
BOUNDARY_MIN_PX = 2
FOLD_SIGMA_PX = 3.2


@dataclass(frozen=True, slots=True)
class Fold:
    """One fold: a straight dark band with a highlight beside it, crossing the garment."""

    angle_rad: float
    amplitude: float
    offset_px: float  # offset of the band from the image centre, along the band's normal


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    source: str  # id in evals/golden-provisional/manifest.json
    kind: str  # "logo" | "crease"
    note: str
    text: str = ""
    sub: str = ""
    ink: tuple[int, int, int] = (26, 26, 34)
    emblem: tuple[int, int, int] | None = None
    folds: tuple[Fold, ...] = ()
    # For logo cases: `True` means "the first fold is re-centred to cross the label", which is
    # what makes the protection subtraction load bearing.
    fold_through_label: bool = True


CASES: tuple[Case, ...] = (
    Case(
        "logo-01-tee-chest",
        "top-02",
        "logo",
        "chest wordmark on a white tee; a fold runs through the print, so deleting the "
        "protection subtraction makes the mask overlap the box",
        text="SPRING",
        sub="1948",
        ink=(22, 22, 30),
        emblem=(176, 32, 40),
        folds=(Fold(0.10, 0.30, 0.0), Fold(-0.22, 0.16, 46.0)),
    ),
    Case(
        "logo-02-breeches-waistband",
        "bottom-03",
        "logo",
        "maker stamp near the waistband, with two folds across it",
        text="BREE",
        sub="W 32",
        ink=(30, 28, 34),
        folds=(Fold(0.07, 0.29, 0.0), Fold(-0.14, 0.17, 40.0)),
    ),
    Case(
        "logo-03-sneaker-side",
        "shoe-02",
        "logo",
        "side-of-shoe label, dark ink on light canvas",
        text="GOODYEAR",
        sub="1890",
        ink=(20, 24, 40),
        emblem=(30, 72, 132),
        folds=(Fold(0.16, 0.30, 0.0),),
    ),
    Case(
        "logo-04-belt-stamp",
        "acc-03",
        "logo",
        "stamped initials on a leather belt; a narrow garment, so a shallow-angle fold",
        text="CH",
        sub="1884",
        ink=(40, 26, 20),
        folds=(Fold(0.05, 0.28, 0.0), Fold(0.18, 0.15, 34.0)),
    ),
    Case(
        "logo-05-jacket-emblem",
        "outer-01",
        "logo",
        "chest emblem on a wool jacket; low-contrast fabric, the hard case for the detector",
        text="AB",
        sub="2002",
        ink=(34, 30, 44),
        emblem=(150, 44, 96),
        folds=(Fold(-0.09, 0.30, 0.0), Fold(0.13, 0.17, 52.0)),
    ),
    Case(
        "crease-01-breeches-leg",
        "bottom-01",
        "crease",
        "three folds across a pressed leg",
        folds=(Fold(0.11, 0.32, -40.0), Fold(-0.06, 0.30, 10.0), Fold(0.17, 0.18, 55.0)),
    ),
    Case(
        "crease-02-tee-body",
        "top-01",
        "crease",
        "two folds across a tee body",
        folds=(Fold(0.09, 0.31, -30.0), Fold(-0.12, 0.19, 35.0)),
    ),
    Case(
        "crease-03-tennis-shoe",
        "shoe-04",
        "crease",
        "toe-box creases on a leather sneaker",
        folds=(Fold(0.20, 0.30, -25.0), Fold(-0.15, 0.18, 30.0)),
    ),
    Case(
        "crease-04-breeches-back",
        "bottom-06",
        "crease",
        "two long folds across a dark ground",
        folds=(Fold(-0.08, 0.32, -35.0), Fold(0.10, 0.20, 40.0)),
    ),
)


# ------------------------------------------------------------------------------- geometry


def _long_edge_resize(image: Image.Image, long_edge: int) -> Image.Image:
    width, height = image.size
    scale = long_edge / float(max(width, height))
    if scale >= 1.0:
        return image.copy()
    return image.resize(
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        Image.LANCZOS,
    )


def _min_filter_erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """Erosion by a (2r+1)² Chebyshev kernel — same rule the stage uses for its exclusion."""
    if radius <= 0:
        return mask.copy()
    image = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8))
    return np.asarray(image.filter(ImageFilter.MinFilter(2 * radius + 1))) > 0


def _interior_centroid(garment: np.ndarray) -> tuple[int, int]:
    """The garment-interior centroid.

    Centroid rather than bounding-box centre: the bounding box of a tilted garment is mostly
    backdrop, and an overlay drawn on the backdrop would test nothing.
    """
    height, width = garment.shape
    band = max(BOUNDARY_MIN_PX, int(round(BOUNDARY_FRACTION * min(height, width))))
    interior = _min_filter_erode(garment.astype(bool), band)
    ys, xs = np.nonzero(interior)
    if xs.size == 0:
        raise SystemExit("garment interior is empty — the source mask is degenerate")
    return int(xs.mean()), int(ys.mean())


def _best_overlay_origin(
    garment: np.ndarray, size: tuple[int, int]
) -> tuple[tuple[int, int], float]:
    """The placement for a `size` patch that sits on the most garment, nearest the centroid.

    Maximising garment coverage rather than trusting the centroid matters: the centroid of a
    V-shaped belt sits in the empty triangle between its halves, and a label drawn there would
    be on the *backdrop* — testing nothing, while looking perfectly reasonable in the manifest.
    Returns `((x, y), coverage_fraction)`.
    """
    patch_w, patch_h = size
    height, width = garment.shape
    if patch_w + 2 >= width or patch_h + 2 >= height:
        raise SystemExit(f"patch {size} does not fit a {width}x{height} fixture")
    cx, cy = _interior_centroid(garment)
    integral = np.cumsum(np.cumsum(garment.astype(np.int64), axis=0), axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    best_key: tuple[int, int] | None = None
    best_origin = (1, 1)
    for y in range(1, height - patch_h - 1, 4):
        for x in range(1, width - patch_w - 1, 4):
            covered = int(
                integral[y + patch_h, x + patch_w]
                - integral[y, x + patch_w]
                - integral[y + patch_h, x]
                + integral[y, x]
            )
            key = (covered, -(abs(x + patch_w // 2 - cx) + abs(y + patch_h // 2 - cy)))
            if best_key is None or key > best_key:
                best_key = key
                best_origin = (x, y)
    assert best_key is not None
    return best_origin, best_key[0] / float(patch_w * patch_h)


def _flatten(source_id: str, manifest: dict[str, dict]) -> tuple[Image.Image, np.ndarray, dict]:
    entry = manifest[source_id]
    photo = _long_edge_resize(Image.open(SOURCE_DIR / entry["path"]).convert("RGB"), LONG_EDGE)
    mask = _long_edge_resize(Image.open(SOURCE_DIR / entry["mask_path"]).convert("L"), LONG_EDGE)
    rgb = np.asarray(photo, dtype=np.float64)
    alpha = np.asarray(mask, dtype=np.float64) / 255.0
    flat = rgb * alpha[:, :, None] + np.array(BACKDROP, dtype=np.float64) * (
        1.0 - alpha[:, :, None]
    )
    return Image.fromarray(np.rint(flat).astype(np.uint8)), np.asarray(mask, dtype=np.uint8), entry


def _fold_field(shape: tuple[int, int], folds: tuple[Fold, ...]) -> np.ndarray:
    """A multiplicative luminance field: each fold is a dark band with a highlight beside it.

    Dark on one side of the line and bright on the other is what a fold does to a diffuse
    surface on a lit backdrop, and it is why the detector needs a *directional* term.
    """
    height, width = shape
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
    total = np.zeros((height, width), dtype=np.float64)
    for fold in folds:
        normal_x, normal_y = -np.sin(fold.angle_rad), np.cos(fold.angle_rad)
        distance = (
            (xs - width / 2.0) * normal_x + (ys - height / 2.0) * normal_y - fold.offset_px
        )
        total += fold.amplitude * (
            np.exp(-((distance - 2.0) ** 2) / (2 * FOLD_SIGMA_PX**2))
            - np.exp(-((distance + 2.0) ** 2) / (2 * FOLD_SIGMA_PX**2))
        )
    return np.clip(1.0 + total, 0.52, 1.06)


def _fold_box(field: np.ndarray, garment: np.ndarray) -> tuple[int, int, int, int]:
    """Bounding box of the drawn folds, clipped to the garment — the `crease_box` truth."""
    ys, xs = np.nonzero((np.abs(field - 1.0) > 0.05) & garment)
    if xs.size == 0:
        return (0, 0, 0, 0)
    return (
        int(xs.min()),
        int(ys.min()),
        int(xs.max() - xs.min() + 1),
        int(ys.max() - ys.min() + 1),
    )


def _signed_offset_to(point: tuple[int, int], shape: tuple[int, int], angle: float) -> float:
    """Offset of the line through `point` with `angle`, measured the way `_fold_field` does."""
    height, width = shape
    normal_x, normal_y = -np.sin(angle), np.cos(angle)
    return (point[0] - width / 2.0) * normal_x + (point[1] - height / 2.0) * normal_y


# ---------------------------------------------------------------------------------- build


def build() -> dict:
    """Write every fixture and the manifest. Deterministic; no network."""
    manifest_src = {
        entry["id"]: entry
        for entry in json.loads((SOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    }
    for directory in (OUT_DIR / "flat", OUT_DIR / "alpha"):
        if not directory.is_dir():
            directory.mkdir(parents=True)

    cases: list[dict] = []
    for case in CASES:
        flat_image, alpha, source_entry = _flatten(case.source, manifest_src)
        shape = (alpha.shape[0], alpha.shape[1])
        garment = alpha >= 128
        height, width = shape

        entry: dict = {
            "id": case.id,
            "kind": case.kind,
            "flat": f"flat/{case.id}.png",
            "alpha": f"alpha/{case.id}.png",
            "note": case.note,
            "source": {
                "fixture": f"evals/golden-provisional/{source_entry['path']}",
                "commons_page": source_entry["source"]["page"],
                "commons_file": source_entry["source"]["file"],
                "collection": source_entry.get("notes", ""),
                "licence": source_entry["source"]["licence"],
                "artist": source_entry["source"]["artist"],
                "mask": "algorithmic:background_colour_distance (provisional set, not hand-drawn)",
                # What `evals/metrics/prettify.py` needs in order to time the *rectify* half of
                # "the rectify+prettify path, per backend" (PLAN E2-T2 acceptance): the photo
                # and mask this flat-lay was derived from, and the category rectify needs.
                "rectify_inputs": {
                    "photo": f"evals/golden-provisional/{source_entry['path']}",
                    "alpha": f"evals/golden-provisional/{source_entry['mask_path']}",
                    "category": source_entry["category"],
                },
            },
        }

        # 1. the overlay, if any, is placed on the garment interior centroid
        folds = list(case.folds)
        ink_layer = Image.new("L", (width, height), 0)
        if case.kind == "logo":
            font = ImageFont.load_default(size=22)
            sub_font = ImageFont.load_default(size=15)
            patch_w = max(int(font.getlength(case.text)), int(sub_font.getlength(case.sub)))
            patch_w += 34 + (30 if case.emblem else 0)
            patch_h = 58
            (x, y), on_garment = _best_overlay_origin(garment, (patch_w, patch_h))
            if on_garment < 0.90:
                raise SystemExit(
                    f"{case.id}: the best label placement covers only {on_garment:.0%} garment "
                    "— pick a source whose garment is wide enough to hold a label"
                )
            draw = ImageDraw.Draw(flat_image)
            # Pillow's `rectangle` includes BOTH endpoints, so the far corner must be
            # `x + w - 1` / `y + h - 1` for the drawn patch to occupy exactly the w×h box the
            # manifest records. With `x + w` the patch is one column and one row larger than
            # `protect_box`, and `region[y:y+h, x:x+w]` (half-open, as every consumer slices
            # it) then never looks at that last column/row — a ground-truth box that silently
            # excludes part of the thing it claims to cover. QA measured 48 escaping pixels on
            # logo-01 from exactly this.
            draw.rectangle(
                [x, y, x + patch_w - 1, y + patch_h - 1],
                fill=(246, 245, 241),
                outline=(206, 203, 197),
            )
            draw.text((x + 10, y + 8), case.text, font=font, fill=case.ink)
            draw.text((x + 12, y + 36), case.sub, font=sub_font, fill=case.ink)
            emblem_note = ""
            if case.emblem:
                ex = x + patch_w - 26
                draw.ellipse([ex, y + 16, ex + 20, y + 36], fill=case.emblem)
                emblem_note = f" plus a 20px circle in rgb{case.emblem}"
            # Ground truth for "the printed ink" — the glyphs and the emblem, **not** the patch
            # background or its outline. This is the region the DoD's "never overlaps the
            # logo/text region" is about: blank padding inside a label is not print, and it is
            # pixel-identical to the fabric around it, so no detector can tell them apart.
            ink_draw = ImageDraw.Draw(ink_layer)
            ink_draw.text((x + 10, y + 8), case.text, font=font, fill=255)
            ink_draw.text((x + 12, y + 36), case.sub, font=sub_font, fill=255)
            if case.emblem:
                ink_draw.ellipse([ex, y + 16, ex + 20, y + 36], fill=255)
            entry["protect_box"] = [x, y, patch_w, patch_h]
            entry["overlay"] = {
                "drawn": (
                    f'a {patch_w}x{patch_h} label patch at ({x},{y}) with "{case.text}" and '
                    f'"{case.sub}" drawn in a bitmap font, ink rgb{case.ink}{emblem_note}; a '
                    "fold is re-centred to cross the patch and the fold field is applied "
                    "*after* the label, so the crease is visible on the print too"
                ),
                "colour": f"ink rgb{case.ink}, patch rgb(246,245,241), outline rgb(206,203,197)",
                "synthetic": True,
                "why_synthetic": (
                    "no logo of any real brand is used or downloaded; this fixture measures "
                    "that the protection region is not invaded, not that a detector recalls "
                    "real packaging"
                ),
            }
            if case.fold_through_label and folds:
                # Put the first fold's band through the label's centre. Without this, a
                # fixture that happens to be lit flat gets no candidate pixels inside the box
                # and the protection test stops proving anything.
                first = folds[0]
                folds[0] = Fold(
                    first.angle_rad,
                    first.amplitude,
                    _signed_offset_to(
                        (x + patch_w // 2, y + patch_h // 2), shape, first.angle_rad
                    ),
                )

        # 2. folds on top of the flat-lay (and on top of the label: a crease bends the print)
        field = _fold_field(shape, tuple(folds)) if folds else np.ones(shape)
        field = np.where(garment, field, 1.0)  # folds happen on fabric, not on the backdrop
        rgb = np.asarray(flat_image, dtype=np.float64) * field[:, :, None]
        flat_image = Image.fromarray(np.rint(np.clip(rgb, 0.0, 255.0)).astype(np.uint8))

        if folds:
            entry["crease_box"] = list(_fold_box(field, garment))
            entry["overlay"] = {
                **entry.get("overlay", {}),
                "folds": (
                    f"{len(folds)} fold band(s): multiplicative luminance field, dark band with "
                    f"an adjacent highlight, sigma {FOLD_SIGMA_PX} px, clipped to [0.52, 1.06]"
                ),
                "fold_colour": "luminance multiplier (achromatic)",
            }
        if case.kind == "crease":
            entry["overlay"] = {
                "drawn": entry["overlay"]["folds"],
                "colour": "luminance multiplier (achromatic)",
                "synthetic": True,
            }

        # 3. the camera's white-balance error goes on last, so ink, label and folds carry it too
        cast = np.asarray(flat_image, dtype=np.float64) * np.array(CAMERA_CAST)
        flat_image = Image.fromarray(np.rint(np.clip(cast, 0.0, 255.0)).astype(np.uint8))

        buffer = io.BytesIO()
        flat_image.save(buffer, format="PNG", optimize=True)
        (OUT_DIR / entry["flat"]).write_bytes(buffer.getvalue())
        buffer = io.BytesIO()
        Image.fromarray(alpha).save(buffer, format="PNG", optimize=True)
        (OUT_DIR / entry["alpha"]).write_bytes(buffer.getvalue())
        if case.kind == "logo":
            ink_dir = OUT_DIR / "ink"
            if not ink_dir.is_dir():
                ink_dir.mkdir(parents=True)
            entry["ink"] = f"ink/{case.id}.png"
            buffer = io.BytesIO()
            ink_layer.save(buffer, format="PNG", optimize=True)
            (OUT_DIR / entry["ink"]).write_bytes(buffer.getvalue())
        cases.append(entry)

    manifest = {
        "owner": "CV role (E2-T2)",
        "generated_by": "evals/fixtures/prettify/build_fixtures.py",
        "honesty": (
            "Every overlay (label, wordmark, emblem, fold) is synthesised by the build script "
            "on top of a Wikimedia Commons photograph whose licence is recorded per case. "
            "These are not the marks of any real product."
        ),
        "camera_cast": list(CAMERA_CAST),
        "camera_cast_note": (
            "every flat-lay is multiplied by camera_cast before it is written, i.e. it carries "
            "a ~3 degree warm white-balance error; without it the backdrop would be exactly "
            "#F2F1EE and the white-balance step would be measured as an identity"
        ),
        "cases": cases,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    manifest = build()
    total = 0
    for entry in manifest["cases"]:
        for key in ("flat", "alpha"):
            total += (OUT_DIR / entry[key]).stat().st_size
    print(f"{len(manifest['cases'])} cases, {total / 1e6:.2f} MB total")
    for entry in manifest["cases"]:
        box = entry.get("protect_box", entry.get("crease_box"))
        print(f"  {entry['id']:26s} {entry['kind']:6s} box={box}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
