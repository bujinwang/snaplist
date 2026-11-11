"""Tests for evals.tools.generate_proposals (step 1 of the ADR-010 mask SOP).

The 40 real golden photos are not present in a clean clone, so these tests build small
synthetic golden environments in a system temp dir via `tempfile.TemporaryDirectory()`.
Nothing is written into the repo working tree: the CI `dirty_check` step runs
`git status --porcelain -uall` and would fail on any leftover test artefact. pytest's
`tmp_path` is deliberately NOT used because this host's sandbox raises EEXIST for
pytest's temp root; `tempfile.TemporaryDirectory()` is the pattern the repo's own
mask-provenance / harness tests use.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from evals.tools import generate_proposals as gp


def _photo(size=(64, 64), bg=(180, 180, 180), square=None):
    """White-background photo with an optional centered foreground square (RGB)."""
    img = Image.new("RGB", size, bg)
    if square is not None:
        x0, y0, s = square
        for y in range(y0, y0 + s):
            for x in range(x0, x0 + s):
                img.putpixel((x, y), (255, 255, 255))
    return img


def _mask_bytes(pattern):
    """Encode a single-channel PIL image of `pattern` (HxW uint8) as PNG bytes."""
    buf = io.BytesIO()
    Image.fromarray(pattern.astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def _make_fake_genai(mask_bytes):
    """Build a fake `google.genai` module whose model returns `mask_bytes`.

    The fake's response object mirrors the REAL google-genai 3.x shape for an image returned
    by `client.models.generate_content`: the bytes live at
    `response.candidates[0].content.parts[0].inline_data.data` -- NOT a `generated_images`
    attribute. Using the real shape keeps the unit test honest (an earlier version asserted
    the fictional `.generated_images` path, which would have crashed against the actual SDK).

    Does NOT touch sys.modules; the caller registers it (and the `google` parent) via
    monkeypatch so cleanup is automatic.
    """
    mod = types.ModuleType("google.genai")

    class _Blob:
        def __init__(self, data):
            self.data = data
            self.mime_type = "image/png"

    class _Part:
        def __init__(self, data):
            self.inline_data = _Blob(data)

    class _Content:
        def __init__(self, data):
            self.parts = [_Part(data)]

    class _Candidate:
        def __init__(self, data):
            self.content = _Content(data)

    class _Response:
        def __init__(self, data):
            self.candidates = [_Candidate(data)]

    class _Models:
        def generate_content(self, model, contents):
            return _Response(mask_bytes)

    class Client:
        def __init__(self, api_key):
            self.api_key = api_key

        @property
        def models(self):
            return _Models()

    mod.Client = Client
    return mod


def _make_golden_env(base: Path, photos: dict[str, Image.Image]):
    golden = base / "golden"
    photos_dir = golden / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries = []
    for rid, img in photos.items():
        img.save(photos_dir / f"{rid}.jpg", format="JPEG")
        manifest_entries.append({"id": rid, "path": f"photos/{rid}.jpg"})
    (golden / "manifest.draft.json").write_text(
        json.dumps(manifest_entries), encoding="utf-8"
    )
    register = {
        "expected_ids": list(photos),
        "records": [
            {"id": rid, "proposal": {"mask_path": f"masks_draft/{rid}.png"}}
            for rid in photos
        ],
    }
    review_dir = base / "golden-review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "point_review_register.json").write_text(
        json.dumps(register), encoding="utf-8"
    )
    return golden, review_dir


def _make_missing_photo_env(base: Path, ids: list[str]) -> tuple[Path, Path]:
    """Manifest/register list ids whose photo files are absent (all-SKIP scenario)."""
    golden = base / "golden"
    (golden / "photos").mkdir(parents=True, exist_ok=True)
    (golden / "manifest.draft.json").write_text(
        json.dumps([{"id": rid, "path": f"photos/{rid}.jpg"} for rid in ids]),
        encoding="utf-8",
    )
    review_dir = base / "golden-review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "point_review_register.json").write_text(
        json.dumps({"expected_ids": ids, "records": []}), encoding="utf-8"
    )
    return golden, review_dir


def _proposal_args(base: Path, golden: Path, review: Path, *,
                   dry_run: bool = False) -> list[str]:
    args = [
        "--manifest", str(golden / "manifest.draft.json"),
        "--register", str(review / "point_review_register.json"),
        "--golden-dir", str(golden),
        "--out-dir", str(base / "out"),
    ]
    if dry_run:
        args.append("--dry-run")
    return args


def test_floodfill_isolates_centered_foreground():
    photo = _photo(square=(20, 20, 24))
    mask = np.asarray(gp.floodfill_border(photo, tolerance=32))
    assert mask.shape == (64, 64)
    # border is background -> 0; centred white garment -> 255
    assert mask[0, 0] == 0
    assert mask[32, 32] == 255


def test_floodfill_fullbleed_subject_is_all_background():
    photo = _photo(bg=(120, 120, 120))
    mask = np.asarray(gp.floodfill_border(photo, tolerance=32))
    # subject fills the frame: the border flood reaches everything, nothing is foreground
    assert int(mask.max()) == 0 and int(mask.min()) == 0


def test_floodfill_respects_tolerance():
    # bg 180, with a centred near-background blob of colour 205 (|205-180|*3 = 75 from bg)
    photo = Image.new("RGB", (64, 64), (180, 180, 180))
    for y in range(24, 40):
        for x in range(24, 40):
            photo.putpixel((x, y), (205, 205, 205))
    low = np.asarray(gp.floodfill_border(photo, tolerance=32))
    high = np.asarray(gp.floodfill_border(photo, tolerance=90))
    # below tol=32 the blob is not background -> stays foreground (255)
    assert low[32, 32] == 255
    # above tol=90 the flood merges it into background -> 0
    assert high[32, 32] == 0


def test_main_dry_run_reports_missing_photo_and_writes_nothing(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "dry"
        # top-02 has no photo file, only an entry in the manifest
        golden, review = _make_golden_env(
            base, {"top-01": _photo(square=(20, 20, 24))}
        )
        manifest = golden / "manifest.draft.json"
        register = review / "point_review_register.json"
        out = base / "out"
        rc = gp.main(
            [
                "--manifest", str(manifest),
                "--register", str(register),
                "--golden-dir", str(golden),
                "--out-dir", str(out),
                "--dry-run",
            ]
        )
        out_files = list(out.glob("*.png"))
        assert rc == 0
        assert out_files == [], "dry-run must write nothing"
        captured = capsys.readouterr().out
        assert "top-01" in captured and "DRY" in captured


def test_main_generates_and_skips_existing(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "gen"
        golden, review = _make_golden_env(
            base,
            {
                "top-01": _photo(square=(20, 20, 24)),
                "top-02": _photo(bg=(120, 120, 120)),
            },
        )
        manifest = golden / "manifest.draft.json"
        register = review / "point_review_register.json"
        out = base / "out"
        rc1 = gp.main(
            [
                "--manifest", str(manifest),
                "--register", str(register),
                "--golden-dir", str(golden),
                "--out-dir", str(out),
            ]
        )
        assert rc1 == 0
        assert (out / "top-01.png").is_file()
        assert (out / "top-02.png").is_file()

        # second run without --force must skip the existing drafts
        capsys.readouterr()
        rc2 = gp.main(
            [
                "--manifest", str(manifest),
                "--register", str(register),
                "--golden-dir", str(golden),
                "--out-dir", str(out),
            ]
        )
        assert rc2 == 0
        captured = capsys.readouterr().out
        assert "SKIP" in captured and "draft exists" in captured


def test_google_nano_banana_backend_mock(monkeypatch):
    # left half 200 (->255 after binarise), right half 0 (->0)
    pattern = np.zeros((8, 8), dtype=np.uint8)
    pattern[:, :4] = 200
    mask_bytes = _mask_bytes(pattern)
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    fake = _make_fake_genai(mask_bytes)
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    photo = _photo(square=(20, 20, 24))
    result = gp.google_nano_banana(photo, api_key="test-key")
    arr = np.asarray(result)
    assert arr.shape == (8, 8)
    assert arr[0, 0] == 255 and arr[0, 7] == 0
    # no key -> RuntimeError even with the SDK importable
    with pytest.raises(RuntimeError):
        gp.google_nano_banana(photo, api_key=None)


def test_google_nano_banana_requires_sdk_and_key(monkeypatch):
    # QA fix #2: isolate the no-key guard by injecting a fake SDK so the ImportError
    # branch does not mask it. Calling with no key MUST raise RuntimeError.
    pattern = np.zeros((8, 8), dtype=np.uint8)
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    fake = _make_fake_genai(_mask_bytes(pattern))
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    photo = _photo(square=(20, 20, 24))
    with pytest.raises(RuntimeError):
        gp.google_nano_banana(photo, api_key=None)


# --------------------------------------------------------------------------- zero-output honesty

_TWENTY_IDS = [f"top-{i:02d}" for i in range(1, 21)]


def test_all_photos_missing_is_reported_as_failure(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "all-missing"
        golden, review = _make_missing_photo_env(base, _TWENTY_IDS)
        rc = gp.main(_proposal_args(base, golden, review))
        captured = capsys.readouterr()
        assert rc != 0
        assert "20 id(s) processed: OK=0 SKIP=20 DRY=0 FAIL=0." in captured.out
        assert "0 failures." not in captured.out
        assert "no proposal was produced" in captured.err
        assert list((base / "out").glob("*.png")) == []


def test_partial_missing_photos_with_one_ok_still_succeeds(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "partial"
        golden, review = _make_golden_env(
            base, {"top-01": _photo(square=(20, 20, 24))}
        )
        manifest = golden / "manifest.draft.json"
        entries = json.loads(manifest.read_text(encoding="utf-8"))
        entries.append({"id": "top-02", "path": "photos/top-02.jpg"})
        manifest.write_text(json.dumps(entries), encoding="utf-8")
        register = review / "point_review_register.json"
        payload = json.loads(register.read_text(encoding="utf-8"))
        payload["expected_ids"] = ["top-01", "top-02"]
        register.write_text(json.dumps(payload), encoding="utf-8")

        rc = gp.main(_proposal_args(base, golden, review))
        captured = capsys.readouterr()
        assert rc == 0
        assert "2 id(s) processed: OK=1 SKIP=1 DRY=0 FAIL=0." in captured.out
        assert (base / "out" / "top-01.png").is_file()
        assert not (base / "out" / "top-02.png").exists()


def test_dry_run_with_zero_output_is_not_a_failure(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "dry-zero"
        golden, review = _make_missing_photo_env(base, _TWENTY_IDS)
        rc = gp.main(_proposal_args(base, golden, review, dry_run=True))
        captured = capsys.readouterr()
        assert rc == 0
        assert "20 id(s) processed: OK=0 SKIP=20 DRY=0 FAIL=0." in captured.out
        assert "no proposal was produced" not in captured.err
        assert list((base / "out").glob("*.png")) == []


def test_dry_run_reports_dry_count_and_writes_nothing(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "dry-one"
        golden, review = _make_golden_env(
            base, {"top-01": _photo(square=(20, 20, 24))}
        )
        rc = gp.main(_proposal_args(base, golden, review, dry_run=True))
        captured = capsys.readouterr()
        assert rc == 0
        assert "1 id(s) processed: OK=0 SKIP=0 DRY=1 FAIL=0." in captured.out
        assert list((base / "out").glob("*.png")) == []


def test_draft_exists_rerun_is_idempotent_not_zero_output(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "idempotent"
        golden, review = _make_golden_env(
            base, {"top-01": _photo(square=(20, 20, 24))}
        )
        args = _proposal_args(base, golden, review)
        assert gp.main(args) == 0
        capsys.readouterr()
        rc2 = gp.main(args)
        captured = capsys.readouterr()
        assert rc2 == 0
        assert "1 id(s) processed: OK=0 SKIP=1 DRY=0 FAIL=0." in captured.out
        assert "SKIP" in captured.out and "draft exists" in captured.out
        assert "no proposal was produced" not in captured.err


def test_stale_draft_does_not_mask_an_all_missing_run(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "stale"
        ids = ["top-01", "top-02"]
        golden, review = _make_missing_photo_env(base, ids)
        out = base / "out"
        out.mkdir(parents=True, exist_ok=True)
        _photo(square=(20, 20, 24)).save(out / "top-01.png")
        rc = gp.main(_proposal_args(base, golden, review))
        captured = capsys.readouterr()
        assert rc != 0
        assert "2 id(s) processed: OK=0 SKIP=2 DRY=0 FAIL=0." in captured.out
        assert "no proposal was produced" in captured.err
