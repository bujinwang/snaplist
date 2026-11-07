"""Tests for the E0-T1 golden-set validator (`evals/tools/validate_golden.py`).

Every test builds a throwaway golden directory in `workdir`. The fixtures here are 8×8
PNGs generated on the fly — that is legitimate because these test the *validator*, not the
golden set: `evals/golden/` itself must stay empty until real photos arrive, and nothing in
this file writes there.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from evals.tools import validate_golden as vg


@pytest.fixture
def workdir() -> Iterator[Path]:
    """A real temp dir.

    `pytest`'s `workdir` factory trips this machine's filesystem shim (EEXIST on its own
    cache dir), so the tests own the directory lifecycle instead of fighting it.
    """
    path = Path(tempfile.mkdtemp(prefix="snaplist-golden-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _photo(path: Path, size: tuple[int, int] = (8, 8), mode: str = "RGB") -> None:
    channels = 3 if mode == "RGB" else 1
    Image.new(mode, size, color=(120,) * channels if channels > 1 else 120).save(path)


def _entry(
    entry_id: str,
    category: str,
    *,
    hard_case: bool = False,
    with_mask: bool = False,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "path": f"photos/{entry_id}.png",
        "mask_path": f"masks/{entry_id}.png" if with_mask else None,
        "category": category,
        "colors": ["charcoal"],
        "pattern": "solid",
        "material": ["cotton"],
        "hard_attributes": ["ribbed hem", "slight pilling", "faded print"],
        "hard_case": hard_case,
    }


def build_set(golden: Path, *, drop_last_photo: bool = False) -> list[dict[str, Any]]:
    """A complete, valid 40-entry set: 35 quota entries + 5 hard cases, 20 masks."""
    plan: list[tuple[str, str, bool]] = []
    plan += [(f"top-{i:02d}", "top", False) for i in range(1, 11)]
    plan += [(f"bottom-{i:02d}", "bottom", False) for i in range(1, 11)]
    plan += [(f"dress-{i:02d}", "dress", False) for i in range(1, 6)]
    plan += [(f"shoe-{i:02d}", "shoe", False) for i in range(1, 6)]
    plan += [(f"accessory-{i:02d}", "accessory", False) for i in range(1, 6)]
    plan += [(f"hard-{i:02d}", "top" if i % 2 else "other", True) for i in range(1, 6)]

    (golden / "photos").mkdir(parents=True, exist_ok=True)
    (golden / "masks").mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index, (entry_id, category, hard) in enumerate(plan):
        with_mask = index < vg.TOTAL_MASKS
        entry = _entry(entry_id, category, hard_case=hard, with_mask=with_mask)
        entries.append(entry)
        if not (drop_last_photo and index == len(plan) - 1):
            _photo(golden / entry["path"])
        if with_mask:
            _photo(golden / entry["mask_path"], mode="L")

    (golden / "manifest.json").write_text(json.dumps(entries))
    return entries


def rewrite_manifest(golden: Path, entries: list[dict[str, Any]]) -> None:
    (golden / "manifest.json").write_text(json.dumps(entries))


def failed_names(report: vg.Report) -> set[str]:
    return {check.name for check in report.checks if not check.ok}


# ---------------------------------------------------------------------------- happy path


def test_a_complete_set_passes_every_check(workdir: Path) -> None:
    golden = workdir / "golden"
    build_set(golden)

    report = vg.validate(golden)

    assert report.passed, failed_names(report)
    assert not report.fatal
    assert vg.main(["--golden-dir", str(golden)]) == 0


# ------------------------------------------------------------------------------ fatal cases


def test_missing_manifest_is_fatal_not_merely_incomplete(workdir: Path) -> None:
    golden = workdir / "golden"
    golden.mkdir()

    report = vg.validate(golden)

    assert report.fatal
    assert vg.main(["--golden-dir", str(golden)]) == 2


def test_manifest_that_is_an_object_is_fatal(workdir: Path) -> None:
    golden = workdir / "golden"
    golden.mkdir()
    (golden / "manifest.json").write_text(json.dumps({"items": []}))

    assert vg.validate(golden).fatal
    assert vg.main(["--golden-dir", str(golden)]) == 2


def test_invalid_json_is_fatal(workdir: Path) -> None:
    golden = workdir / "golden"
    golden.mkdir()
    (golden / "manifest.json").write_text("{not json")

    assert vg.validate(golden).fatal


# ---------------------------------------------------------------------------- completeness


def test_39_entries_fails_the_count(workdir: Path) -> None:
    golden = workdir / "golden"
    entries = build_set(golden)
    rewrite_manifest(golden, entries[:-1])

    report = vg.validate(golden)

    assert not report.passed
    assert f"entry count is {vg.TOTAL_ENTRIES}" in failed_names(report)
    assert vg.main(["--golden-dir", str(golden)]) == 1


def test_wrong_category_quota_is_caught(workdir: Path) -> None:
    golden = workdir / "golden"
    entries = build_set(golden)
    entries[0]["category"] = "outerwear"  # 9 tops, 1 stray outerwear, everything else intact
    rewrite_manifest(golden, entries)

    assert "category quotas (35 non-hard entries)" in failed_names(vg.validate(golden))


def test_too_few_hard_cases_is_caught(workdir: Path) -> None:
    golden = workdir / "golden"
    entries = build_set(golden)
    entries[-1]["hard_case"] = False
    rewrite_manifest(golden, entries)

    assert f"exactly {vg.HARD_CASES} hard cases" in failed_names(vg.validate(golden))


def test_too_few_masks_is_caught(workdir: Path) -> None:
    golden = workdir / "golden"
    entries = build_set(golden)
    entries[0]["mask_path"] = None
    rewrite_manifest(golden, entries)

    assert (
        f"exactly {vg.TOTAL_MASKS} mask files (presence only)"
        in failed_names(vg.validate(golden))
    )


# ---------------------------------------------------------------------------- content rules


def test_missing_photo_file_is_caught_even_when_the_count_is_right(workdir: Path) -> None:
    golden = workdir / "golden"
    build_set(golden, drop_last_photo=True)

    assert "every photo exists" in failed_names(vg.validate(golden))


def test_undecodable_photo_is_caught(workdir: Path) -> None:
    golden = workdir / "golden"
    build_set(golden)
    (golden / "photos" / "top-01.png").write_bytes(b"not an image")

    assert "every photo decodes" in failed_names(vg.validate(golden))


def test_rgb_mask_is_rejected(workdir: Path) -> None:
    golden = workdir / "golden"
    build_set(golden)
    (golden / "masks" / "top-01.png").unlink()
    _photo(golden / "masks" / "top-01.png", mode="RGB")

    assert "masks are single-channel and sized like their photo" in failed_names(
        vg.validate(golden)
    )


def test_mask_size_mismatch_is_rejected(workdir: Path) -> None:
    golden = workdir / "golden"
    build_set(golden)
    (golden / "masks" / "top-02.png").unlink()
    _photo(golden / "masks" / "top-02.png", size=(4, 4), mode="L")

    assert "masks are single-channel and sized like their photo" in failed_names(
        vg.validate(golden)
    )


def test_duplicate_ids_are_rejected(workdir: Path) -> None:
    golden = workdir / "golden"
    entries = build_set(golden)
    entries[1]["id"] = entries[0]["id"]
    entries[1]["path"] = entries[0]["path"]
    rewrite_manifest(golden, entries)

    assert "ids are unique slugs" in failed_names(vg.validate(golden))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hard_attributes", ["only two", "attributes"]),
        ("pattern", "paisley"),
        ("colors", "charcoal"),
        ("material", ["Cotton"]),  # not lowercase
    ],
)
def test_vocabulary_and_shape_violations_are_caught(
    workdir: Path, field: str, value: object
) -> None:
    golden = workdir / "golden"
    entries = build_set(golden)
    entries[0][field] = value
    rewrite_manifest(golden, entries)

    assert not vg.validate(golden).passed


def test_placeholder_looking_entry_is_rejected(workdir: Path) -> None:
    golden = workdir / "golden"
    entries = build_set(golden)
    entries[0]["id"] = "sample-top-01"
    rewrite_manifest(golden, entries)

    assert "no placeholder-looking entries" in failed_names(vg.validate(golden))


def test_missing_golden_dir_is_a_usage_error(workdir: Path) -> None:
    assert vg.main(["--golden-dir", str(workdir / "nope")]) == 2
