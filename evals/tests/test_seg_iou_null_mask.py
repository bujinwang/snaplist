"""Regression tests for the E1-T4 crash on `mask_path: null` (`evals/tools/seg_iou.py`).

`evals/golden/manifest.json` has 40 entries and 20 masks (E0-T1); the other 20 carry
`mask_path: null` by contract. Before the fix, `run()` did `golden_dir / entry["mask_path"]`
for *every* entry and died on the first null row with

    TypeError: unsupported operand type(s) for /: 'PosixPath' and 'NoneType'

These tests hold down what replaced it: a null mask means "this photo has no reference", so the
entry is skipped, named in the report, and kept out of the mean — never defaulted to
`masks/<id>.png`, and never counted as a 0.0 so that the denominator silently doubles.

The fixtures are 8x8 PNGs drawn on the fly and `segment()` is replaced by a stub. That is
legitimate because the subject here is the *manifest split and the denominator*, not the model:
this file makes no claim about segmentation quality, needs no 176 MB weights, and never reads
or writes `evals/golden/`.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from evals.tools import seg_iou
from packages.vision.runtime import RuntimeInfo
from packages.vision.segment import SegmentResult
from packages.vision.types import SegmentBackend, StageMeta

# The stub prediction: 255 on the left half of an 8x8 mask, 0 on the right.
PREDICTED_COLUMNS = (0, 4)
# Two masked entries: the first agrees with the prediction exactly (IoU 1.0), the second
# overlaps for half its width (16/48 = 1/3). Two more have no reference at all.
MASKED: list[tuple[str, tuple[int, int]]] = [
    ("top-01", PREDICTED_COLUMNS),
    ("top-02", (2, 6)),
]
UNMASKED: list[tuple[str, None]] = [("top-03", None), ("top-04", None)]


@pytest.fixture
def workdir() -> Iterator[Path]:
    """A real temp dir.

    `pytest`'s `tmp_path` factory trips this machine's filesystem shim (EEXIST on its own
    cache dir), so the tests own the directory lifecycle — the same workaround as
    `test_golden_validation.py` and `test_provisional_set.py`.
    """
    path = Path(tempfile.mkdtemp(prefix="snaplist-nullmask-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def mask_png(columns: tuple[int, int]) -> bytes:
    """PNG bytes of an 8x8 single-channel mask: 255 between `columns`, 0 elsewhere."""
    array = np.zeros((8, 8), dtype=np.uint8)
    array[:, columns[0] : columns[1]] = 255
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class FakeSegment:
    """A `segment()` stand-in: one fixed mask, no weights, no onnxruntime, no network."""

    mask_png: bytes
    calls: list[bytes] = field(default_factory=list)

    def __call__(self, image: bytes, backend: SegmentBackend) -> SegmentResult:
        self.calls.append(image)
        return SegmentResult(
            mask_png=self.mask_png,
            meta=StageMeta(
                stage="segment",
                backend=SegmentBackend.U2NET,
                device="cpu",
                seconds=0.01,
                notes={"model_sha256": "0" * 64},
            ),
        )


@pytest.fixture
def fake_segment(monkeypatch: pytest.MonkeyPatch) -> FakeSegment:
    """Replace the model and the runtime probe so the test is deterministic by construction."""
    fake = FakeSegment(mask_png=mask_png(PREDICTED_COLUMNS))
    monkeypatch.setattr(seg_iou, "segment", fake)
    monkeypatch.setattr(
        seg_iou,
        "probe",
        lambda: RuntimeInfo(
            device="cpu",
            backend=SegmentBackend.U2NET,
            torch_available=False,
            hosted_available=False,
            reason="test double",
            notes=(),
        ),
    )
    return fake


def entry(entry_id: str, mask: str | None, category: str = "top") -> dict[str, Any]:
    return {
        "id": entry_id,
        "path": f"photos/{entry_id}.png",
        "mask_path": mask,
        "category": category,
    }


def build_set(root: Path, spec: list[tuple[str, tuple[int, int] | None]]) -> Path:
    """A throwaway golden dir: one 8x8 photo per entry, a mask only where `spec` names columns."""
    golden = root / "golden"
    (golden / "photos").mkdir(parents=True)
    (golden / "masks").mkdir()
    entries: list[dict[str, Any]] = []
    for entry_id, columns in spec:
        Image.new("RGB", (8, 8), (120, 120, 120)).save(golden / "photos" / f"{entry_id}.png")
        if columns is None:
            # Deliberately no file at masks/<id>.png: there is no reference for this photo,
            # and the tool must not read that absence as "the mask is at the usual place".
            entries.append(entry(entry_id, None))
            continue
        (golden / "masks" / f"{entry_id}.png").write_bytes(mask_png(columns))
        entries.append(entry(entry_id, f"masks/{entry_id}.png"))
    (golden / "manifest.json").write_text(json.dumps(entries))
    return golden


# ------------------------------------------------------------------ the null-mask regression


def test_null_mask_entries_are_skipped_and_named(workdir: Path, fake_segment: FakeSegment) -> None:
    """The crash itself: 2 null rows must not raise, must not be scored, must be listed."""
    golden = build_set(workdir, MASKED + UNMASKED)

    rows, meta = seg_iou.run(golden)

    assert meta["n"] == 2
    assert meta["manifest_entries"] == 4
    assert [row["id"] for row in rows] == ["top-01", "top-02"]
    assert meta["unscored"] == ["top-03", "top-04"]
    assert len(fake_segment.calls) == 2


def test_a_null_mask_is_never_turned_into_a_guessed_path(
    workdir: Path, fake_segment: FakeSegment
) -> None:
    """The failure mode one step later than the crash: defaulting to `masks/<id>.png`.

    A file exists at the conventional path here, so a default would find one and quietly
    score the photo against an unreviewed mask. `n` staying at 2 is what says it did not.
    """
    golden = build_set(workdir, MASKED + UNMASKED)
    for entry_id, _ in UNMASKED:
        (golden / "masks" / f"{entry_id}.png").write_bytes(mask_png((0, 8)))

    rows, meta = seg_iou.run(golden)

    assert meta["n"] == 2, "a file at the conventional path is not a manifest reference"
    assert {row["mask"] for row in rows} == {"masks/top-01.png", "masks/top-02.png"}


def test_the_mean_is_over_the_masked_entries_not_the_manifest(
    workdir: Path, fake_segment: FakeSegment
) -> None:
    golden = build_set(workdir, MASKED + UNMASKED)

    rows, _ = seg_iou.run(golden)
    stats = seg_iou.summarise(rows)

    assert rows[0]["iou"] == pytest.approx(1.0)
    assert rows[1]["iou"] == pytest.approx(1 / 3, abs=1e-4)
    assert stats["mean"] == pytest.approx((1.0 + 1 / 3) / 2, abs=1e-4)
    # The regression this guards: the same sum divided by the 4 manifest entries.
    assert stats["mean"] != pytest.approx((1.0 + 1 / 3) / 4, abs=1e-4)


@pytest.mark.parametrize("mask_value", [None, "", "   "])
def test_only_a_non_empty_string_counts_as_a_reference(
    workdir: Path, fake_segment: FakeSegment, mask_value: str | None
) -> None:
    """`_split_by_mask`'s documented rule, which must not drift from `validate_golden.py`."""
    golden = build_set(workdir, MASKED)
    entries = json.loads((golden / "manifest.json").read_text())
    entries.append(entry("top-03", mask_value))
    (golden / "manifest.json").write_text(json.dumps(entries))

    rows, meta = seg_iou.run(golden)

    assert meta["n"] == 2
    assert meta["unscored"] == ["top-03"]


# ------------------------------------------------------------- a set that cannot be measured


def test_an_all_null_manifest_raises_instead_of_reporting_zero(workdir: Path) -> None:
    """A mean over zero fixtures is not a measurement, and `0.0` is not an honest stand-in."""
    golden = build_set(workdir, UNMASKED)

    with pytest.raises(ValueError, match="carries a mask_path"):
        seg_iou.run(golden)


def test_an_unscorable_set_exits_2_and_writes_no_report(
    workdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = build_set(workdir, UNMASKED)
    report = workdir / "report.md"

    code = seg_iou.main(["--golden-dir", str(golden), "--report", str(report)])

    assert code == 2
    assert "no entry" in capsys.readouterr().err
    assert not report.exists(), "a report whose mean came from no fixtures is the failure mode"


def test_a_missing_mask_file_raises_with_the_entry_that_names_it(
    workdir: Path, fake_segment: FakeSegment
) -> None:
    golden = build_set(workdir, MASKED)
    (golden / "masks" / "top-02.png").unlink()

    with pytest.raises(FileNotFoundError) as caught:
        seg_iou.run(golden)

    message = str(caught.value)
    assert "top-02" in message, "the error has to name the entry, not just the path"
    assert "masks/top-02.png" in message
    # PIL would also raise FileNotFoundError here, but its message blames the open() call.
    # The manifest is what named a file that is not there, and the error has to say so.
    assert "manifest" in message


def test_a_missing_mask_file_exits_2_instead_of_scoring_the_rest(
    workdir: Path, fake_segment: FakeSegment, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = build_set(workdir, MASKED)
    (golden / "masks" / "top-01.png").unlink()

    code = seg_iou.main(["--golden-dir", str(golden), "--report", str(workdir / "report.md")])

    assert code == 2
    assert "cannot measure" in capsys.readouterr().err


# ------------------------------------------------------------------- the report's denominator


def test_the_report_states_the_denominator_and_lists_the_mask_paths(
    workdir: Path, fake_segment: FakeSegment
) -> None:
    """E1-T4 DoD: IoU per fixture, the mean over the masked ones, and the mask paths."""
    golden = build_set(workdir, MASKED + UNMASKED)

    rows, meta = seg_iou.run(golden)
    text = seg_iou.render(rows, meta, seg_iou.summarise(rows), golden, 0.92)

    assert "4 entries, n = 2 with a mask" in text
    assert "`top-03`" in text and "`top-04`" in text, "the unscored ids must be printed"
    assert "masks/top-01.png" in text and "masks/top-02.png" in text
    assert "| masked entries scored (n) | 2 |" in text
    assert "| entries without a mask (not scored) | 2 |" in text
