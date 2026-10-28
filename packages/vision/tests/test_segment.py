"""Tests for E1-T2 `segment()` (packages/vision/segment.py).

None of these tests open the 176 MB weights. The model is replaced by a `FakeSession`
that returns a known d0 field, so the arithmetic under test is *ours* — preprocessing,
min-max normalisation, peak restore, quantisation, encoding — and a red test always means
a bug in this repo rather than a slow download. The real model was exercised by hand; the
numbers are in `docs/HANDOFF-engineer.md` §17.

The DoD lines that matter here, and how each is proven:

* "`meta.backend` is populated for every call" -> asserted on the result, not on a log line.
* "soft mask" -> intermediate alpha values must exist between 0 and 255.
* "255 = garment" / matte() interop -> the peak is exactly 255, so E1-T3 finds an interior.
* "zero network calls" -> sockets are traps for every test in this module.
* honest metadata -> seconds is a measured positive float (never a 0.0 placeholder).

What is deliberately NOT tested here: mean IoU >= 0.92. That needs the 20 hand masks
(E0-T1), which do not exist yet, so the metric stays UNMEASURED — see E1-T4.
"""

from __future__ import annotations

import hashlib
import io
import socket
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

import packages.vision.segment as segment_module
from packages.vision import StageNotImplemented
from packages.vision.runtime import RuntimeInfo
from packages.vision.segment import (
    INPUT_SIZE,
    SegmentBackendUnavailable,
    _postprocess,
    _preprocess,
    _verify_weights,
    main,
    segment,
    weights_path,
)
from packages.vision.types import SegmentBackend, StageMeta

GARMENT = (90, 130, 160)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """segment() must never make a network call — not for weights, not for anything."""

    def _trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("segment() attempted a network call")

    monkeypatch.setattr(socket, "socket", _trap)
    monkeypatch.setattr(socket, "create_connection", _trap)
    yield


def photo(size: tuple[int, int] = (480, 640), alpha: bool = False) -> bytes:
    """A synthetic garment: a bright slab on a dark background. Fixture, not golden-set data."""
    width, height = size
    array = np.full((height, width, 3), (18, 18, 20), dtype=np.uint8)
    array[height // 5 : height * 4 // 5, width // 4 : width * 3 // 4] = GARMENT
    if alpha:
        channel = np.full((height, width, 1), 255, dtype=np.uint8)
        array = np.concatenate([array, channel], axis=2)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def d0_field(peak: float = 0.92) -> np.ndarray:
    """A plausible U²-Net head: near-zero background, a garment blob with a flat core."""
    field = np.zeros((1, 1, INPUT_SIZE, INPUT_SIZE), dtype=np.float32)
    field[:, :, 70:250, 60:250] = peak
    field[:, :, 100:200, 100:200] += peak / 2  # a brighter core, so the field is not flat
    return np.clip(field, 0.0, 1.0)


def d0_spike(peak: float = 0.92) -> np.ndarray:
    """An *exaggerated* field: one sharp peak, so the bilinear erosion is visible at 8-bit.

    A realistic smooth d0 erodes by ~0.0001 when upscaled, which rounding to uint8 hides —
    so a smooth fixture cannot tell whether the peak restore runs. This one can, and the
    mechanism it pins also matters for real photos smaller than 320px, where the field is
    downscaled and the erosion is not a rounding detail.
    """
    axis = np.arange(INPUT_SIZE, dtype=np.float32) - INPUT_SIZE / 2
    yy, xx = np.meshgrid(axis, axis, indexing="ij")
    field = peak * np.exp(-((xx**2 + yy**2) / (2 * 1.5**2)))
    return field.astype(np.float32).reshape(1, 1, INPUT_SIZE, INPUT_SIZE)


class FakeSession:
    """The smallest thing that behaves like an onnxruntime session: one input, seven heads."""

    def __init__(self, prediction: np.ndarray) -> None:
        self.prediction = prediction
        self.fed: dict[str, np.ndarray] | None = None

    def get_inputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="input.1")]

    def get_outputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=f"d{i}") for i in range(7)]

    def run(self, names: object, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.fed = feed
        zeros = np.zeros((1, 1, INPUT_SIZE, INPUT_SIZE), dtype=np.float32)
        return [self.prediction, zeros, zeros, zeros, zeros, zeros, zeros]


@pytest.fixture
def fake_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeSession]:
    """Point the stage at a stub weights file and a stub session. No download involved."""
    weights_dir = tmp_path / "models"
    weights_dir.mkdir()
    (weights_dir / "u2net.onnx").write_bytes(b"not really 176 MB")
    monkeypatch.setenv(segment_module.WEIGHTS_DIR_ENV, str(weights_dir))

    session = FakeSession(d0_field())
    monkeypatch.setattr(segment_module, "_load_session", lambda _path: (session, "deadbeef"))
    yield session


def decode_mask(mask_png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(mask_png)).convert("L"))


# ------------------------------------------------------------------------ the mask itself


def test_mask_is_same_size_single_channel_png(fake_model: FakeSession) -> None:
    result = segment(photo((480, 640)), SegmentBackend.U2NET)

    decoded = Image.open(io.BytesIO(result.mask_png))
    assert decoded.size == (480, 640)
    assert decoded.mode == "L"


def test_mask_keeps_an_fully_opaque_interior_for_matte(fake_model: FakeSession) -> None:
    """E1-T3 clamps against `mask == 255`; a peak of 247 would leave the garment see-through.

    The source is deliberately not 320px: bilinear upscaling is what can shave the peak,
    so this asserts the restore actually fired at a realistic photo size.
    """
    mask = decode_mask(segment(photo((500, 400)), SegmentBackend.U2NET).mask_png)

    assert mask.max() == 255
    assert (mask == 255).sum() > 100, "an interior, not a single lucky pixel"


def test_mask_is_soft_not_binary(fake_model: FakeSession) -> None:
    """Hard edges shred knit fabric; the resize must leave real intermediate values."""
    mask = decode_mask(segment(photo((480, 640)), SegmentBackend.U2NET).mask_png)

    intermediate = ((mask > 0) & (mask < 255)).sum()
    assert intermediate > 50, f"only {intermediate} soft pixels — the mask is a hard cutout"


def test_background_stays_transparent(fake_model: FakeSession) -> None:
    mask = decode_mask(segment(photo((480, 640)), SegmentBackend.U2NET).mask_png)

    assert (mask[:20, :20] == 0).all(), "the corners must not be part of the garment"


def test_mask_never_exceeds_uint8_range(fake_model: FakeSession) -> None:
    """The clip is load-bearing: d0 can exceed 1.0 on a saturated photo."""
    mask = decode_mask(segment(photo(), SegmentBackend.U2NET).mask_png)

    assert mask.dtype == np.uint8
    assert int(mask.min()) >= 0 and int(mask.max()) <= 255


def test_rgba_photo_is_accepted(fake_model: FakeSession) -> None:
    result = segment(photo((120, 90), alpha=True), SegmentBackend.U2NET)

    assert Image.open(io.BytesIO(result.mask_png)).size == (120, 90)


def test_segment_is_deterministic(fake_model: FakeSession) -> None:
    image = photo((240, 320))

    assert (
        segment(image, SegmentBackend.U2NET).mask_png
        == segment(image, SegmentBackend.U2NET).mask_png
    )


# ------------------------------------------------------------------ pure pixel functions


def test_preprocess_matches_the_u2net_input_contract() -> None:
    flat = Image.new("RGB", (777, 555), (128, 128, 128))  # non-square, non-320 on purpose

    batch = _preprocess(flat)

    assert batch.shape == (1, 3, INPUT_SIZE, INPUT_SIZE)
    assert batch.dtype == np.float32
    expected = (np.array([128, 128, 128], dtype=np.float32) / 255.0 - np.array(
        [0.485, 0.456, 0.406], dtype=np.float32
    )) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
    for channel in range(3):
        assert np.allclose(batch[0, channel], expected[channel], atol=1e-5)


def test_postprocess_restores_the_peak_it_documents() -> None:
    """A field whose peak the resize erodes must still top out at 255, not 240."""
    mask, notes = _postprocess(d0_spike(), (500, 400))

    assert mask.max() == 255
    assert notes["peak_restored"] is True
    assert notes["degenerate"] is False
    assert 0.0 < notes["raw_peak"] <= 1.0


def test_degenerate_output_is_an_empty_mask_not_amplified_noise() -> None:
    """A flat d0 has no garment in it. Min-max normalising it would invent one."""
    flat = np.full((1, 1, INPUT_SIZE, INPUT_SIZE), 0.31, dtype=np.float32)

    mask, notes = _postprocess(flat, (64, 48))

    assert (mask == 0).all()
    assert notes["degenerate"] is True
    assert notes["peak_restored"] is False


# --------------------------------------------------------------------- honest metadata


def test_meta_is_measured_and_explains_itself(fake_model: FakeSession) -> None:
    meta = segment(photo(), SegmentBackend.U2NET).meta

    assert isinstance(meta, StageMeta)
    assert meta.stage == "segment"
    assert meta.backend == "u2net"
    assert meta.device == "cpu"
    assert meta.seconds is not None and meta.seconds > 0.0, (
        "UNMEASURED would be None; 0.0 would be a lie — this must be a real measurement"
    )
    for key in ("model_file", "model_sha256", "input_size", "providers", "inference_seconds"):
        assert key in meta.notes, f"cost/audit report needs notes[{key!r}]"
    assert meta.notes["network_calls"] == 0
    assert meta.notes["input_size"] == INPUT_SIZE
    assert meta.notes["providers"] == ["CPUExecutionProvider"]
    assert 0.0 < meta.notes["mask_coverage"] < 1.0
    assert "u2net is the cheap backend" in meta.notes["quality_note"]


def test_input_reaches_the_model_as_the_documented_tensor(fake_model: FakeSession) -> None:
    segment(photo(), SegmentBackend.U2NET)

    fed = fake_model.fed
    assert fed is not None and "input.1" in fed
    assert fed["input.1"].shape == (1, 3, INPUT_SIZE, INPUT_SIZE)


# ------------------------------------------------------------------------- failure paths


@pytest.mark.parametrize("bad", [b"", b"not an image", b"\x89PNG\r\n\x1a\ntruncated"])
def test_bad_photo_bytes_raise_valueerror(bad: bytes, fake_model: FakeSession) -> None:
    with pytest.raises(ValueError, match="segment"):
        segment(bad, SegmentBackend.U2NET)


def test_missing_weights_name_the_make_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(segment_module.WEIGHTS_DIR_ENV, str(tmp_path / "empty"))

    with pytest.raises(SegmentBackendUnavailable, match="make fetch-models"):
        segment(photo(), SegmentBackend.U2NET)

    assert weights_path() == tmp_path / "empty" / "u2net.onnx"


def test_weights_dir_is_overridable_and_defaults_to_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(segment_module.WEIGHTS_DIR_ENV, raising=False)
    assert str(weights_path()).startswith(str(Path.home() / ".cache" / "snaplist"))

    monkeypatch.setenv(segment_module.WEIGHTS_DIR_ENV, str(tmp_path))
    assert weights_path() == tmp_path / "u2net.onnx"


def test_a_doctored_weights_file_is_refused(tmp_path: Path) -> None:
    """A truncated download must fail at load, not produce quietly worse masks."""
    doctored = tmp_path / "u2net.onnx"
    doctored.write_bytes(b"wrong model")

    with pytest.raises(SegmentBackendUnavailable, match="corrupt or a different model"):
        _verify_weights(doctored)


def test_verify_weights_accepts_the_pinned_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = tmp_path / "u2net.onnx"
    stub.write_bytes(b"pinned bytes")
    monkeypatch.setattr(segment_module, "U2NET_SHA256", hashlib.sha256(b"pinned bytes").hexdigest())

    assert _verify_weights(stub) == hashlib.sha256(b"pinned bytes").hexdigest()


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        (SegmentBackend.SAM2_HOSTED, "MODAL_TOKEN_ID"),
        (SegmentBackend.BIREFNET, "torch"),
        (SegmentBackend.UNMEASURED, "not a runnable backend"),
    ],
)
def test_backends_this_build_cannot_run_say_what_is_missing(
    backend: SegmentBackend, expected: str
) -> None:
    with pytest.raises(SegmentBackendUnavailable, match=expected):
        segment(photo(), backend)


def test_unavailable_backend_is_terminal_for_the_worker() -> None:
    """The worker maps StageNotImplemented to a terminal failure without burning retries."""
    with pytest.raises(StageNotImplemented):
        segment(photo(), SegmentBackend.SAM2_HOSTED)


# ------------------------------------------------------------------------------- stage CLI


def test_cli_writes_the_mask_and_prints_meta(
    fake_model: FakeSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "top.png"
    source.write_bytes(photo((120, 160)))
    # The CLI asks probe() for the backend; pin it to the backend this test can run.
    monkeypatch.setattr(
        "packages.vision.runtime.probe",
        lambda: RuntimeInfo(
            device="cpu",
            backend=SegmentBackend.U2NET,
            torch_available=False,
            hosted_available=False,
            reason="pinned by test",
            notes=(),
        ),
    )

    code = main(["--in", str(source), "--out", str(tmp_path / "out")])

    assert code == 0
    mask_file = tmp_path / "out" / "mask.png"
    assert mask_file.is_file()
    assert Image.open(mask_file).mode == "L"


def test_cli_refuses_a_missing_photo(tmp_path: Path) -> None:
    assert main(["--in", str(tmp_path / "nope.png"), "--out", str(tmp_path / "out")]) == 2


def test_cli_exits_nonzero_when_the_backend_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "top.png"
    source.write_bytes(photo((64, 64)))
    monkeypatch.setattr(
        "packages.vision.runtime.probe",
        lambda: RuntimeInfo(
            device="cpu",
            backend=SegmentBackend.SAM2_HOSTED,
            torch_available=False,
            hosted_available=False,
            reason="pinned by test",
            notes=(),
        ),
    )

    assert main(["--in", str(source), "--out", str(tmp_path / "out")]) == 2
