"""Tests for the provisional set's tooling: the mask generator, the IoU metric, and the
`--profile provisional` path through the golden validator.

These run without model weights and without network. The photo fixture is drawn on the fly —
that is legitimate here because the subject under test is the *arithmetic*, not the images;
`evals/golden-provisional/` itself is never written by this file.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from evals.tools import provisional_masks as pm
from evals.tools import seg_iou
from evals.tools import validate_golden as vg


@pytest.fixture
def workdir() -> Iterator[Path]:
    """A real temp dir. `pytest`'s own factory trips this machine's filesystem shim."""
    path = Path(tempfile.mkdtemp(prefix="snaplist-provisional-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def garment_photo(path: Path, size: tuple[int, int] = (240, 320)) -> tuple[bytes, np.ndarray]:
    """A dark garment on a light plain background; returns (jpeg bytes, true mask)."""
    width, height = size
    array = np.full((height, width, 3), (238, 236, 230), dtype=np.uint8)
    truth = Image.new("L", size, 0)
    ImageDraw.Draw(truth).polygon(
        [(60, 40), (180, 40), (200, 120), (180, 280), (60, 280), (40, 120)], fill=255
    )
    array[np.asarray(truth) > 0] = (40, 44, 58)
    Image.fromarray(array).save(path, format="JPEG", quality=95)
    return path.read_bytes(), np.asarray(truth) > 0


# ------------------------------------------------------------------- the mask generator


def test_mask_recovers_a_garment_on_a_plain_background(workdir: Path) -> None:
    photo = workdir / "garment.jpg"
    _, truth = garment_photo(photo)

    mask, stats = pm.mask_from_photo(photo)

    assert mask.mode == "L" and mask.size == (240, 320)
    got = np.asarray(mask) >= 128
    iou = (got & truth).sum() / (got | truth).sum()
    assert iou > 0.97, f"colour mask only reached IoU {iou:.3f} on a flat backdrop"
    assert 0.2 < stats["coverage"] < 0.8


def test_mask_is_not_everything(workdir: Path) -> None:
    """The Pillow 11 regression: `fromarray` is read-only and `floodfill` silently no-ops,
    which produced a mask of coverage 1.0 for every photo. This is the guard."""
    photo = workdir / "garment.jpg"
    garment_photo(photo)

    mask, stats = pm.mask_from_photo(photo)

    assert stats["coverage"] < 0.95, "coverage 1.0 means the background flood never ran"
    corner = np.asarray(mask)[:10, :10]
    assert (corner == 0).all(), "the corners are background and must be transparent"


def test_mask_drops_a_second_object_in_frame(workdir: Path) -> None:
    """One connected body only: the left-hand block must not survive."""
    photo = workdir / "two.jpg"
    image = np.full((300, 300, 3), 235, dtype=np.uint8)
    image[50:250, 150:280] = (30, 30, 30)  # the garment, right of centre
    image[60:120, 10:60] = (30, 30, 30)  # a second object, far left
    Image.fromarray(image).save(photo, format="JPEG", quality=95)

    mask, stats = pm.mask_from_photo(photo)

    assert stats["dropped_px"] > 0
    assert np.asarray(mask)[80, 30] == 0, "the smaller block should have been dropped"


def test_generate_for_manifest_writes_one_mask_per_entry(workdir: Path) -> None:
    golden = workdir / "set"
    (golden / "photos").mkdir(parents=True)
    garment_photo(golden / "photos" / "top-01.jpg")
    (golden / "manifest.json").write_text(
        json.dumps([{"id": "top-01", "path": "photos/top-01.jpg", "mask_path": "masks/top-01.png"}])
    )

    written = pm.generate_for_manifest(golden)

    assert written == 1
    with Image.open(golden / "masks" / "top-01.png") as mask:
        assert mask.mode == "L"


# ------------------------------------------------------------------------- the metric


def test_iou_of_identical_masks_is_one() -> None:
    mask = np.full((10, 10), 255, dtype=np.uint8)
    assert seg_iou._iou(mask, mask) == 1.0


def test_iou_of_disjoint_masks_is_zero() -> None:
    left = np.zeros((10, 10), dtype=np.uint8)
    left[:, :5] = 255
    right = np.zeros((10, 10), dtype=np.uint8)
    right[:, 5:] = 255
    assert seg_iou._iou(left, right) == 0.0


def test_iou_counts_half_overlap_as_one_third() -> None:
    predicted = np.zeros((10, 10), dtype=np.uint8)
    predicted[:, :6] = 255  # 60 px
    reference = np.zeros((10, 10), dtype=np.uint8)
    reference[:, 3:9] = 255  # 60 px, 30 shared -> 30/90
    assert seg_iou._iou(predicted, reference) == pytest.approx(1 / 3, abs=1e-6)


def test_iou_binarises_at_128() -> None:
    """A soft edge must not be counted as partial area — it is in or out at the midpoint."""
    predicted = np.full((10, 10), 127, dtype=np.uint8)
    reference = np.full((10, 10), 255, dtype=np.uint8)
    assert seg_iou._iou(predicted, reference) == 0.0


def test_summary_reports_per_category_means() -> None:
    rows = [
        {"id": "a", "category": "top", "iou": 1.0, "seconds": 0.2},
        {"id": "b", "category": "top", "iou": 0.5, "seconds": 0.2},
        {"id": "c", "category": "shoe", "iou": 0.75, "seconds": 0.4},
    ]
    stats = seg_iou.summarise(rows)

    assert stats["mean"] == pytest.approx(0.75)
    assert stats["min"] == 0.5 and stats["max"] == 1.0
    assert stats["by_category"]["top"] == pytest.approx(0.75)
    assert stats["by_category"]["shoe"] == pytest.approx(0.75)
    assert stats["seconds_total"] == pytest.approx(0.8)


# --------------------------------------------------------------------- validator profile


def _entry(slug: str, category: str = "top", mask: bool = True) -> dict:
    return {
        "id": slug,
        "path": f"photos/{slug}.jpg",
        "mask_path": f"masks/{slug}.png" if mask else None,
        "category": category,
        "colors": ["black"],
        "pattern": "solid",
        "material": ["cotton"],
        "hard_attributes": ["a", "b", "c"],
        "hard_case": False,
    }


def build_set(root: Path, entries: list[dict]) -> Path:
    golden = root / "set"
    (golden / "photos").mkdir(parents=True)
    (golden / "masks").mkdir()
    for entry in entries:
        garment_photo(golden / entry["path"])
        if entry.get("mask_path"):
            Image.new("L", (240, 320), 255).save(golden / entry["mask_path"])
    (golden / "manifest.json").write_text(json.dumps(entries))
    return golden


def test_provisional_profile_accepts_a_set_that_is_not_the_golden_composition(
    workdir: Path,
) -> None:
    """10 entries in one category is not the E0-T1 shape — under the provisional profile that
    is fine, and the composition is still printed so nobody has to infer it."""
    golden = build_set(workdir, [_entry(f"top-{i:02d}") for i in range(10)])

    report = vg.validate(golden, profile="provisional")

    assert report.passed, [c.name for c in report.checks if not c.ok]
    composition = [c.detail for c in report.checks if c.name.startswith("composition")]
    assert composition and "top 10" in composition[0]


def test_provisional_profile_still_fails_a_missing_mask(workdir: Path) -> None:
    entries = [_entry(f"top-{i:02d}") for i in range(10)]
    entries[4]["mask_path"] = None
    golden = build_set(workdir, entries)

    report = vg.validate(golden, profile="provisional")

    assert not report.passed
    assert "every entry carries a mask" in {c.name for c in report.checks if not c.ok}


def test_provisional_profile_still_fails_an_unknown_category(workdir: Path) -> None:
    entries = [_entry(f"top-{i:02d}") for i in range(10)]
    entries[0]["category"] = "gadget"
    golden = build_set(workdir, entries)

    report = vg.validate(golden, profile="provisional")

    assert "categories are contract values" in {c.name for c in report.checks if not c.ok}


def test_provisional_profile_rejects_too_small_a_set(workdir: Path) -> None:
    golden = build_set(workdir, [_entry(f"top-{i:02d}") for i in range(3)])

    report = vg.validate(golden, profile="provisional")

    assert f"at least {vg.PROVISIONAL_MIN_ENTRIES} entries" in {
        c.name for c in report.checks if not c.ok
    }


def test_golden_profile_is_still_the_default_and_still_strict(workdir: Path) -> None:
    """The provisional path must not have loosened the real contract."""
    golden = build_set(workdir, [_entry(f"top-{i:02d}") for i in range(10)])

    report = vg.validate(golden)  # no profile argument: the golden contract

    assert report.profile == "golden"
    failures = {c.name for c in report.checks if not c.ok}
    assert f"entry count is {vg.TOTAL_ENTRIES}" in failures
    assert vg.main(["--golden-dir", str(golden)]) == 1


def test_provisional_render_says_it_is_not_the_golden_set(workdir: Path) -> None:
    golden = build_set(workdir, [_entry(f"top-{i:02d}") for i in range(10)])

    text = vg.validate(golden, profile="provisional").render(golden)

    assert "PROVISIONAL" in text
    assert "not the E0-T1 golden set" in text


def test_unknown_profile_is_rejected(workdir: Path) -> None:
    golden = build_set(workdir, [_entry("top-01")])
    with pytest.raises(ValueError, match="profile must be one of"):
        vg.validate(golden, profile="lenient")
