"""STAGE 1 — garment segmentation. Owner: CV role (E1, ticket E1-T2).

    segment(image: bytes, backend: SegmentBackend) -> SegmentResult
    SegmentResult.mask_png   # 8-bit single channel, 255 = garment, soft edges allowed
    SegmentResult.meta       # StageMeta(backend=..., device=...)

Model order (frozen in BRIEF.md): SAM 2.1 hosted → BiRefNet → U²-Net.
**Only the U²-Net path exists in this file.** The other two raise
`SegmentBackendUnavailable`, which is a `StageNotImplemented`: the worker treats it as a
deterministic terminal failure and does not burn the retry budget on it.

Why U²-Net is the one that landed first
---------------------------------------
E1-T1's `probe()` already selects `u2net` on a CPU-only machine without hosted credentials,
and that is this machine. U²-Net runs through **onnxruntime**, not torch — so "no torch"
was never the blocker for the cheap path; "no weights" was, and weights are a 176 MB file
fetched once by `make fetch-models`, not a credential. That is the honest difference
between a backend that needs an account (SAM 2.1 on Modal) and one that does not.

Backend selection is *not* repeated here. `segment()` takes the label it is given and
refuses to guess: resolving policy in two places is how a silent model swap happens.
Callers use `runtime.probe().backend`; the stage CLI below does exactly that.

The weights are never downloaded from inside this function
---------------------------------------------------------
A stage that fetches 176 MB on first call is a stage that makes a network call in a
pipeline whose contract says there are none, and it turns a missing file into a four-minute
stall inside a job. So: missing file ⇒ `SegmentBackendUnavailable` naming the make target,
and `make fetch-models` is where the download (and its sha256 check) lives.

Pre/post-processing, and the two things worth knowing
-----------------------------------------------------
* Input is the standard U²-Net contract: 320×320, ImageNet mean/std, CHW float32. A
  different normalisation still produces a mask, just a worse one — which is exactly the
  kind of quiet wrongness the eval harness exists to catch.
* Output `d0` (the fused head) is min-max normalised, then resized to the source size.
  Bilinear upscaling from 320px **lowers the peak**, so the resized field is divided by its
  own maximum before quantising: without that, the mask can top out at 247 and `matte()`
  finds no `== 255` interior to clamp against. `peak_restored` in `notes` says whether that
  correction fired.
* Ties into `matte()` (E1-T3) by convention, not by import: matte treats `== 255` as
  interior, so the peak-restore above is what keeps a garment body fully opaque.

What this does NOT do yet
-------------------------
* No `no_garment` decision. CONTRACTS §4 has the reason code, but the threshold that would
  trigger it is a number nobody has measured — so `mask_coverage` is reported and the
  decision waits for the golden set (E0-T1/E1-T4). A guessed threshold here would reject
  real photos and we would never know why.
* No acceleration beyond CPU. `CoreMLExecutionProvider` is available on Apple silicon and
  is deliberately not used: switching to it without a measured IoU/latency comparison is a
  silent model swap in hardware clothing. `notes["providers"]` records what actually ran.
* Colour decontamination and edge refinement belong to `matte()`, not here.

The signature is frozen by `test_public_signatures_are_stable`; changing it is a contract
change (`docs/CONTRACTS.md`), not a refactor.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

from packages.vision import StageNotImplemented
from packages.vision.types import SegmentBackend, StageMeta

# ----------------------------------------------------------------------------- constants

INPUT_SIZE = 320
_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

WEIGHTS_DIR_ENV = "SNAPLIST_MODEL_DIR"
U2NET_FILENAME = "u2net.onnx"
# Pinned to the file this build was measured against, so a swapped or truncated download
# fails at load time instead of quietly producing worse masks for a week.
U2NET_SHA256 = "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491"
U2NET_BYTES = 175_997_641

_PROVIDERS = ("CPUExecutionProvider",)
# d0 is a sigmoid field. A perfectly flat one carries no garment information at all, and
# min-max normalising it would divide by ~0 and then amplify rounding noise into a mask.
_DEGENERATE_SPAN = 1e-6

_QUALITY_NOTE = (
    "u2net is the cheap backend selected by policy on a CPU-only machine: expect a lower "
    "IoU than SAM 2.1 on thin straps, knit edges and low-contrast backgrounds"
)


class SegmentBackendUnavailable(StageNotImplemented):
    """A backend named by the runtime policy that this build cannot run.

    Subclasses `StageNotImplemented` on purpose: "BiRefNet needs torch and torch is not
    installed" is deterministic, so the worker must terminate the job immediately rather
    than retry it three times and refund it.
    """


@dataclass(slots=True)
class SegmentResult:
    mask_png: bytes
    meta: StageMeta


# ------------------------------------------------------------------------- weights/model


def weights_path() -> Path:
    """Where the weights are looked for. Never creates the directory, never downloads."""
    override = os.environ.get(WEIGHTS_DIR_ENV, "").strip()
    if override:
        base = Path(override).expanduser()
    else:
        base = Path.home() / ".cache" / "snaplist" / "models"
    return base / U2NET_FILENAME


def fetch_hint() -> str:
    """The sentence a missing-weights failure carries, so the fix is inside the error."""
    size_mb = U2NET_BYTES / 1e6
    return f"run `make fetch-models` (downloads {size_mb:.0f} MB once into {weights_path().parent})"


def _verify_weights(path: Path) -> str:
    """sha256 of the file on disk, checked against the pin. Returns the digest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != U2NET_SHA256:
        raise SegmentBackendUnavailable(
            f"u2net weights at {path} have sha256 {actual}, expected {U2NET_SHA256}; the "
            f"file is corrupt or a different model — delete it and {fetch_hint()}"
        )
    return actual


@lru_cache(maxsize=2)
def _load_session(path_str: str) -> tuple[ort.InferenceSession, str]:
    """Verify the file, then open the ONNX session. Cached: load is ~0.1 s, hashing is not."""
    digest = _verify_weights(Path(path_str))
    session = ort.InferenceSession(path_str, providers=list(_PROVIDERS))
    return session, digest


# ---------------------------------------------------------------------------- pixel work


def _decode(image: bytes) -> Image.Image:
    """Decode photo bytes to RGB, or fail loudly with the stage named."""
    if not image:
        raise ValueError("segment(): image bytes are empty")
    try:
        with Image.open(io.BytesIO(image)) as opened:
            frame = opened.load()  # force the decode: a truncated file fails here
            del frame
            return opened.convert("RGB").copy()
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - PIL raises a zoo of formats for bad bytes
        raise ValueError(f"segment(): could not decode image bytes as an image ({exc})") from exc


def _preprocess(image: Image.Image) -> np.ndarray:
    """RGB → 1×3×320×320 float32, ImageNet-normalised. The U²-Net input contract."""
    resized = image.resize((INPUT_SIZE, INPUT_SIZE), Image.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    array = (array - _IMAGENET_MEAN) / _IMAGENET_STD
    return np.ascontiguousarray(array.transpose(2, 0, 1)[None, ...], dtype=np.float32)


def _postprocess(prediction: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, dict]:
    """d0 → uint8 mask at the source size, plus the numbers a reviewer will ask for."""
    field = np.asarray(prediction, dtype=np.float32).reshape(INPUT_SIZE, INPUT_SIZE)
    raw_peak = float(field.max())
    span = raw_peak - float(field.min())
    if span < _DEGENERATE_SPAN:
        zeros = np.zeros((size[1], size[0]), dtype=np.uint8)
        return zeros, {"raw_peak": raw_peak, "peak_restored": False, "degenerate": True}

    normalised = (field - field.min()) / span  # reference U²-Net postprocessing
    upscaled = np.asarray(
        Image.fromarray(normalised).resize(size, Image.BILINEAR), dtype=np.float32
    )
    # Bilinear upscaling averages the peak with its neighbours, so the maximum can land
    # below 1.0. Dividing by the observed peak restores exactly one 255 pixel, which is
    # what matte() needs to find an opaque interior to clamp against.
    peak = float(upscaled.max())
    peak_restored = 0.0 < peak < 1.0
    if peak_restored:
        upscaled = upscaled / peak

    mask = np.rint(np.clip(upscaled, 0.0, 1.0) * 255.0).astype(np.uint8)
    return mask, {"raw_peak": raw_peak, "peak_restored": peak_restored, "degenerate": False}


def _encode(mask: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(mask).save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


# --------------------------------------------------------------------------------- stage


def segment(image: bytes, backend: SegmentBackend = SegmentBackend.SAM2_HOSTED) -> SegmentResult:
    """Segment one garment photo. Offline, deterministic, no network call.

    `backend` must come from `runtime.probe()` (or from the stage CLI, which asks the
    probe). The default is the contract primary, so forgetting the argument fails loudly on
    a machine that cannot run SAM 2.1 instead of silently downgrading.
    """
    decoded = _decode(image)  # validated first: a bad photo must not reach model load

    if backend is SegmentBackend.U2NET:
        pass
    elif backend is SegmentBackend.BIREFNET:
        raise SegmentBackendUnavailable(
            "birefnet backend needs torch, which is not installed in this environment "
            "(SNAPLIST_SEGMENT_BACKEND=birefnet); the cheap u2net path is available "
            "(docs/PLAN.md ticket E1-T2)"
        )
    elif backend is SegmentBackend.SAM2_HOSTED:
        raise SegmentBackendUnavailable(
            "sam2_hosted backend needs MODAL_TOKEN_ID/MODAL_TOKEN_SECRET and a deployed "
            "endpoint; neither exists in this build (docs/PLAN.md ticket E1-T2, gated on "
            "E5-T4 storage). Pass runtime.probe().backend to use the local backend"
        )
    else:
        raise SegmentBackendUnavailable(
            f"backend={backend!r} is not a runnable backend; pass runtime.probe().backend "
            "rather than letting the stage guess"
        )

    path = weights_path()
    if not path.is_file():
        raise SegmentBackendUnavailable(
            f"u2net weights are not on disk at {path}; {fetch_hint()} and retry "
            "(docs/PLAN.md ticket E1-T2). This stage never downloads them itself: the "
            "pipeline contract is zero network calls"
        )

    started = time.perf_counter()
    batch = _preprocess(decoded)

    session, digest = _load_session(str(path))
    input_name = session.get_inputs()[0].name
    inference_started = time.perf_counter()
    outputs = session.run(None, {input_name: batch})
    inference_seconds = time.perf_counter() - inference_started

    mask, mask_notes = _postprocess(outputs[0], decoded.size)
    mask_png = _encode(mask)
    seconds = time.perf_counter() - started

    pixels = int(mask.size)
    mask_px = int((mask > 0).sum())
    meta = StageMeta(
        stage="segment",
        backend=SegmentBackend.U2NET,
        device="cpu",
        seconds=seconds,  # measured wall-clock; the harness treats None as UNMEASURED
        notes={
            "model_file": str(path),
            "model_sha256": digest,
            "input_size": INPUT_SIZE,
            "providers": list(_PROVIDERS),
            "inference_seconds": inference_seconds,
            "mask_px": mask_px,
            "mask_coverage": round(mask_px / pixels, 4) if pixels else 0.0,
            "network_calls": 0,
            "quality_note": _QUALITY_NOTE,
            **mask_notes,
        },
    )
    return SegmentResult(mask_png=mask_png, meta=meta)


# ---------------------------------------------------------------------------- stage CLI
# Not the pipeline CLI (`python -m packages.vision.cli`): that one promises
# prettified.png/cutout.png/attributes.json and cannot finish until E2/E3 exist. This one
# runs exactly one stage, which is what an E1-T2 acceptance needs in order to be repeatable.


def main(argv: list[str] | None = None) -> int:
    from packages.vision.runtime import probe  # local: keep the import off the stage path

    usable = [b.value for b in SegmentBackend if b is not SegmentBackend.UNMEASURED]
    parser = argparse.ArgumentParser(prog="python -m packages.vision.segment")
    parser.add_argument("--in", dest="src", required=True, help="input photo")
    parser.add_argument("--out", dest="dst", required=True, help="output directory")
    parser.add_argument(
        "--backend", choices=usable, default=None, help="override probe() (for A/B checks)"
    )
    args = parser.parse_args(argv)

    runtime = probe()
    backend = SegmentBackend(args.backend) if args.backend else runtime.backend
    print(f"probe: backend={runtime.backend.value} device={runtime.device} ({runtime.reason})")

    source = Path(args.src)
    try:
        image = source.read_bytes()
    except OSError as exc:
        print(f"cannot read {source}: {exc}", file=sys.stderr)
        return 2

    try:
        result = segment(image, backend)
    except (SegmentBackendUnavailable, ValueError) as exc:
        print(f"SEGMENT FAILED: {exc}", file=sys.stderr)
        return 2

    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    mask_file = dst / "mask.png"
    mask_file.write_bytes(result.mask_png)
    print(f"wrote {mask_file} ({len(result.mask_png)} bytes)")

    meta = asdict(result.meta)
    meta["backend"] = str(result.meta.backend)
    print(json.dumps(meta, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
