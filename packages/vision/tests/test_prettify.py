"""Tests for E2-T2 `prettify()` (packages/vision/prettify.py) — round 1 (no model) + round 2.

Round 1 (white balance / mask / protection / shadow attenuation) is covered first. Round 2
adds `mode=SDXL_INPAINT`; its tests are in the `SDXL` section at the bottom and they obey one
rule above all: **`make test` must stay fast and must not need the 6.9 GB of weights.** So the
generative assertions are behind `requires_sdxl_weights` (module-scope skip), while the
*refusal* path — the thing every machine without the weights will actually hit — is tested
everywhere, in a subprocess with `torch` blocked from importing.

The ticket's DoD lines and how each one is proven here:

* **"the inpaint mask never overlaps the logo/text region"** — the only "never" in the DoD.
  `test_protection_region_is_never_invaded` walks all 5 logo fixtures and asserts
  `final_mask ∩ protect_box == 0`, using the mask `prettify()` itself consumed
  (`wrinkle_mask_for`), plus the premise that the same fixtures *would* have been invaded
  without the subtraction (`test_protection_subtraction_is_load_bearing`). Without the premise
  half, deleting the subtraction could leave the first test green on a quieter fixture — which
  is the mutation `M-protect` below.
* **"wrinkle mask coverage is capped"** — `WRINKLE_COVERAGE_CAP` is a rail, not a decoration:
  `test_coverage_cap_binds_on_an_over_creased_garment` builds a garment whose natural
  candidate coverage is far above the cap and asserts the cap pulled it back *and* that
  `cap_bound` says so. `test_coverage_never_exceeds_the_cap_on_the_fixtures` holds the
  property on all 9.
* **"never touch the boundary band"** — `test_mask_never_enters_the_boundary_band`, checked
  against the eroded interior rather than against a number.
* **"the fill only touches masked pixels"** — two tests, because prettify also applies a
  global gain: `test_fill_does_not_change_pixels_outside_the_feathered_mask` (the fill alone)
  and `test_outside_the_mask_only_the_white_balance_gain_moves` (the whole stage).
* **"PASS_THROUGH is reported honestly"** — `test_pass_through_is_byte_identical_and_says_so`.
* **"every mode reports a real `seconds`"** — `test_every_runnable_mode_measures_seconds`;
  `None` is what `StageMeta` uses for UNMEASURED, so a `None` here would be a lie in the cost
  report.
* **determinism** — `test_two_runs_produce_identical_bytes`.
* **zero network** — the autouse socket trap, same shape as `test_dedupe.py`.
* **SDXL round 2 refuses with the fix inside the error, without importing torch** —
  `test_sdxl_inpaint_refuses_with_the_fix_inside_the_error` and
  `test_importing_prettify_does_not_import_torch`.
* **round 2 writes nothing outside the feathered mask** —
  `test_sdxl_output_equals_the_white_balanced_input_where_the_weight_is_zero`, plus
  `test_repaint_weight_never_enters_the_protection_region_or_the_boundary_band` which runs
  without the weights because it is arithmetic on the mask, not a model call.
* **round 2 is deterministic** — `test_sdxl_is_byte_identical_for_the_same_seed`.

The fixtures are licensed photos with **synthetic** labels and folds drawn on them
(`evals/fixtures/prettify/README.md`). Nothing here is a claim that the detector recalls real
packaging, and nothing here measures "are the creases gone" — that is E2-T3's manual rubric.
"""

from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import packages.vision.prettify as prettify_module
from packages.vision.prettify import (
    BACKDROP_RGB,
    BALANCE_MIN_BACKGROUND_PX,
    SDXL_FEATHER_MIN_PX,
    SDXL_REQUIRED_FILES,
    SDXL_SEED,
    SDXL_STEPS,
    SDXL_STRENGTH,
    SDXL_WORK_SIDE,
    WRINKLE_COVERAGE_CAP,
    PrettifyBackendUnavailable,
    _attenuate,
    _feathered_repaint_weight,
    _outward_feather,
    _quantise,
    _sdxl_work_size,
    fetch_sdxl_weights,
    prettify,
    prettify_sdxl,
    sdxl_backend_available,
    sdxl_missing_deps,
    sdxl_missing_files,
    sdxl_weights_available,
    sdxl_weights_dir,
    wrinkle_mask_for,
)
from packages.vision.types import PrettifyMode

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "evals" / "fixtures" / "prettify"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
MAX_FIXTURE_BYTES = 4_000_000
MAX_FIXTURE_LONG_EDGE = 800


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """prettify() must never touch the network: no weights, no model, nothing to fetch."""

    def _trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("prettify() attempted a network call")

    monkeypatch.setattr(socket, "socket", _trap)
    monkeypatch.setattr(socket, "create_connection", _trap)
    monkeypatch.setattr(socket, "getaddrinfo", _trap)
    yield


# --------------------------------------------------------------------------------- helpers


def manifest() -> dict:
    loaded = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def cases(kind: str | None = None) -> list[dict]:
    entries = manifest()["cases"]
    return [entry for entry in entries if kind is None or entry["kind"] == kind]


def fixture_bytes(entry: dict) -> tuple[bytes, bytes]:
    return (FIXTURE_DIR / entry["flat"]).read_bytes(), (FIXTURE_DIR / entry["alpha"]).read_bytes()


def fixture_ink(entry: dict) -> np.ndarray:
    """Ground truth of the *printed* pixels (glyphs + emblem), from `ink/<id>.png`."""
    path = FIXTURE_DIR / entry["ink"]
    with Image.open(path) as opened:
        return np.asarray(opened.convert("L")) > 127


def alpha_shape(flat_png: bytes, alpha_png: bytes) -> tuple[int, int]:
    return wrinkle_mask_for(flat_png, alpha_png).garment.shape


def png_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def rgb_bytes(array: np.ndarray) -> bytes:
    return png_bytes(np.rint(np.clip(array, 0, 255)).astype(np.uint8))


def decode(payload: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(payload)) as opened:
        return np.asarray(opened.convert("RGB"), dtype=np.float64)


def box_region(box: list[int] | tuple[int, ...], shape: tuple[int, int]) -> np.ndarray:
    height, width = shape
    x, y, box_w, box_h = (int(value) for value in box)
    region = np.zeros((height, width), dtype=bool)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + box_w, width), min(y + box_h, height)
    region[y0:y1, x0:x1] = True
    return region


def synthetic_garment(
    *,
    size: tuple[int, int] = (180, 240),
    garment_rgb: tuple[int, int, int] = (196, 198, 202),
    background_rgb: tuple[float, float, float] = (242.0, 241.0, 238.0),
    cast: tuple[float, float, float] = (1.0, 1.0, 1.0),
    folds: int = 0,
    fold_amplitude: float = 0.0,
) -> tuple[bytes, bytes, np.ndarray]:
    """A rectangle of "garment" on a backdrop, optionally with periodic creases.

    Returns `(flat_png, alpha_png, garment_mask)`. Synthetic on purpose: the white-balance and
    coverage-cap tests need a *known* answer, which no photograph provides.
    """
    height, width = size
    flat = np.zeros((height, width, 3), dtype=np.float64)
    flat[:, :] = np.array(BACKDROP_RGB, dtype=np.float64)
    margin_y, margin_x = height // 8, width // 8
    garment = np.zeros((height, width), dtype=bool)
    garment[margin_y : height - margin_y, margin_x : width - margin_x] = True
    flat[garment] = np.array(garment_rgb, dtype=np.float64)
    if folds:
        # Periodic dark/bright band pairs: a garment that is nothing but creases, which is the
        # only way to drive natural candidate coverage past the cap without editing it.
        spacing = max(6.0, (height - 2 * margin_y) / float(folds))
        ys = np.arange(height, dtype=np.float64)
        phase = np.sin(2.0 * np.pi * (ys - margin_y) / spacing)
        field = np.repeat((1.0 + fold_amplitude * phase)[:, None], width, axis=1)
        flat[garment] *= field[garment][:, None]
    flat = flat * np.array(cast, dtype=np.float64)
    alpha = np.where(garment, 255, 0).astype(np.uint8)
    return rgb_bytes(flat), png_bytes(alpha), garment


# ------------------------------------------------------------------------ fixture material


def test_fixtures_are_small_and_long_edge_bounded() -> None:
    """BRIEF.md caps fixture bulk; a 40 MB fixture set is a fixture set nobody clones."""
    total = 0
    for entry in cases():
        for key in ("flat", "alpha"):
            path = FIXTURE_DIR / entry[key]
            payload = path.read_bytes()
            total += len(payload)
            with Image.open(io.BytesIO(payload)) as opened:
                assert max(opened.size) <= MAX_FIXTURE_LONG_EDGE, path
    assert total <= MAX_FIXTURE_BYTES, f"{total} bytes > {MAX_FIXTURE_BYTES}"


def test_fixture_manifest_records_provenance_and_truth_boxes() -> None:
    """A fixture whose provenance or ground truth is missing is worse than no fixture."""
    logo_cases = cases("logo")
    crease_cases = cases("crease")
    assert len(logo_cases) == 5, "the DoD's logo protection test names 5 fixtures"
    assert crease_cases, "at least one crease case is needed for 'did the mask fire here'"
    for entry in logo_cases:
        assert entry["protect_box"], entry["id"]
        assert entry["ink"], f"{entry['id']}: no printed-ink ground truth"
        assert entry["overlay"]["synthetic"] is True, entry["id"]
        assert entry["source"]["licence"], entry["id"]
        assert entry["source"]["commons_page"].startswith("https://commons.wikimedia.org/")
        # The ink truth must live inside the rectangle it is supposed to be part of, or the two
        # ground truths contradict each other and the red-line test measures nothing.
        ink = fixture_ink(entry)
        region = box_region(entry["protect_box"], ink.shape)
        assert int((ink & ~region).sum()) == 0, entry["id"]
    for entry in crease_cases:
        assert entry["crease_box"], entry["id"]
        assert entry["overlay"]["synthetic"] is True, entry["id"]
        assert entry["source"]["licence"], entry["id"]


# ------------------------------------------------------------------------- white balance


def test_white_balance_pulls_a_known_cast_back_to_neutral() -> None:
    """A ~5–8 % channel split must come back to #F2F1EE on the backdrop, not stay cast."""
    for cast in ((1.080, 1.000, 0.930), (1.030, 1.000, 0.970)):
        flat, alpha, garment = synthetic_garment(cast=cast)
        audit = wrinkle_mask_for(flat, alpha)
        balanced_rgb = audit.balanced
        ring = ~prettify_module._binary_dilate(garment, 6)
        measured = np.median(balanced_rgb[ring], axis=0)
        assert abs(measured[0] - BACKDROP_RGB[0]) <= 2.0, (cast, measured)
        assert abs(measured[1] - BACKDROP_RGB[1]) <= 2.0, (cast, measured)
        assert abs(measured[2] - BACKDROP_RGB[2]) <= 2.0, (cast, measured)
        # The gains are reported, three numbers, and they are not the identity.
        gains = audit.notes["white_balance"]
        assert len(gains) == 3
        assert not np.allclose(gains, (1.0, 1.0, 1.0)), "white balance was an identity"
        assert audit.notes["white_balance_detail"]["skipped"] is False


def test_white_balance_is_a_gain_and_does_not_wash_the_colour_out() -> None:
    """Multiplicative only: a red garment stays red, with its saturation intact.

    An additive offset (or a grey-world desaturation) would pass the backdrop test above and
    fail this one, which is why both exist.
    """
    garment_rgb = (196.0, 74.0, 62.0)
    flat, alpha, garment = synthetic_garment(garment_rgb=(196, 74, 62), cast=(1.070, 1.0, 0.940))
    audit = wrinkle_mask_for(flat, alpha)
    interior = prettify_module._binary_erode(garment, 12)
    before = decode(flat)[interior].mean(axis=0)
    after = audit.balanced[interior].mean(axis=0)
    saturation_before = float(before.max() - before.min())
    saturation_after = float(after.max() - after.min())
    assert saturation_after > saturation_before * 0.85, (saturation_before, saturation_after)
    assert saturation_after < saturation_before * 1.25, (saturation_before, saturation_after)
    # And the hue ordering survives: red is still the dominant channel.
    assert int(np.argmax(after)) == 0
    assert garment_rgb[0] > garment_rgb[1]


def test_white_balance_is_skipped_honestly_when_there_is_no_backdrop() -> None:
    """A garment cropped to the frame has no ring to measure; say so instead of guessing."""
    size = (120, 120)
    flat = np.full((size[0], size[1], 3), 150.0)
    alpha = np.full(size, 255, dtype=np.uint8)
    audit = wrinkle_mask_for(rgb_bytes(flat), png_bytes(alpha))
    assert audit.notes["white_balance"] == [1.0, 1.0, 1.0]
    detail = audit.notes["white_balance_detail"]
    assert detail["skipped"] is True
    assert str(BALANCE_MIN_BACKGROUND_PX) in detail["reason"]


# ------------------------------------------------------------------------ wrinkle mask


def test_coverage_never_exceeds_the_cap_on_the_fixtures() -> None:
    for entry in cases():
        flat, alpha = fixture_bytes(entry)
        audit = wrinkle_mask_for(
            flat, alpha, protect=[tuple(entry["protect_box"])] if entry.get("protect_box") else None
        )
        coverage = audit.notes["wrinkle_mask"]["coverage"]
        assert coverage <= WRINKLE_COVERAGE_CAP, (entry["id"], coverage)
        assert audit.notes["wrinkle_mask"]["coverage_cap"] == WRINKLE_COVERAGE_CAP


def test_coverage_cap_binds_on_an_over_creased_garment() -> None:
    """The cap is a rail, and a rail nothing ever touches is indistinguishable from none.

    This garment is nothing but creases, so the *natural* candidate coverage is far above the
    cap. Both halves are asserted: the candidate went over, and the final mask was pulled
    back under. Flip `WRINKLE_COVERAGE_CAP` to 1.0 and the second assertion goes red.
    """
    flat, alpha, garment = synthetic_garment(folds=12, fold_amplitude=0.24)
    audit = wrinkle_mask_for(flat, alpha)
    notes = audit.notes["wrinkle_mask"]
    assert notes["natural_coverage"] > WRINKLE_COVERAGE_CAP, notes
    assert notes["coverage"] <= WRINKLE_COVERAGE_CAP, notes
    assert notes["candidate_coverage"] <= WRINKLE_COVERAGE_CAP, notes
    assert notes["cap_bound"] is True, notes
    assert notes["threshold"] > notes["score_floor"], notes
    assert int(audit.final_mask.sum()) <= int(
        np.floor(WRINKLE_COVERAGE_CAP * int(audit.interior.sum()))
    )
    assert garment.any()


def test_mask_never_enters_the_boundary_band() -> None:
    """The fill pulls a low-frequency guide inward; at the silhouette that guide is backdrop."""
    for entry in cases():
        flat, alpha = fixture_bytes(entry)
        audit = wrinkle_mask_for(flat, alpha)
        band_px = audit.notes["wrinkle_mask"]["boundary_band_px"]
        assert band_px >= 2
        outside_interior = audit.garment & ~audit.interior
        assert outside_interior.any(), "fixture has no boundary band to test"
        assert int((audit.final_mask & outside_interior).sum()) == 0, entry["id"]
        assert int((audit.final_mask & ~audit.garment).sum()) == 0, entry["id"]


def test_mask_fires_inside_the_drawn_crease_boxes() -> None:
    """'The parts that should move did move' — the only recall-shaped claim the material allows."""
    hits: list[tuple[str, float]] = []
    for entry in cases("crease"):
        flat, alpha = fixture_bytes(entry)
        audit = wrinkle_mask_for(flat, alpha)
        region = box_region(entry["crease_box"], audit.garment.shape) & audit.interior
        pool = int(region.sum())
        assert pool > 0, entry["id"]
        hit = float((audit.final_mask & region).sum()) / pool
        hits.append((entry["id"], hit))
        assert hit > 0.0, (entry["id"], hits)
    # Printed so the actual rates travel with the pass: a `> 0.0` threshold is a very weak
    # claim and the handoff quotes these numbers instead of the boolean.
    print("crease hit rates:", ", ".join(f"{name}={hit:.3f}" for name, hit in hits))


def test_logo_fixtures_also_fire_in_the_crease_box() -> None:
    """Each logo fixture has a fold drawn through it — otherwise subtracting the protection
    region would be a no-op and the protection test would be vacuous.

    The claim asserted here is only the *premise*: the mask wants into the label box. The
    outcome (it never gets there) is `test_protection_region_is_never_invaded`, which passes
    the caller box; on this test no caller box is given, so the automatic region — a dilated
    stroke mask, not the whole rectangle — is all that stands in the way, and the box is
    legitimately not fully covered.
    """
    for entry in cases("logo"):
        flat, alpha = fixture_bytes(entry)
        box = tuple(entry["protect_box"])
        region = box_region(box, wrinkle_mask_for(flat, alpha).garment.shape)
        unprotected = prettify_module._build_wrinkle_mask(
            prettify_module._decode_rgb(flat, "flat"),
            prettify_module._decode_l(alpha, "alpha"),
            protect=None,
        )
        assert int((unprotected.final_mask & region).sum()) > 0, (
            entry["id"],
            "no crease fires inside the label box: the protection subtraction is not "
            "load bearing on this fixture",
        )


# ------------------------------------------------------------------------------ protection


def test_protection_region_is_never_invaded() -> None:
    """`final_mask ∩ protect_box == ∅` on all 5 logo fixtures, caller box passed.

    This is the guarantee the *caller* buys by declaring the rectangle. The harder claim — the
    same thing with nobody declaring anything — is the two tests below it.
    """
    for entry in cases("logo"):
        flat, alpha = fixture_bytes(entry)
        box = tuple(entry["protect_box"])
        audit = wrinkle_mask_for(flat, alpha, protect=[box])
        region = box_region(box, audit.garment.shape)
        overlap = int((audit.final_mask & region).sum())
        assert overlap == 0, (entry["id"], overlap)
        assert audit.notes["protect"]["final_mask_overlap_px"] == 0
        assert audit.notes["protect"]["pixels_removed_by_protection"] > 0, entry["id"]
        # The dilated protection region, not just the raw box, is what the mask is off.
        assert int((audit.final_mask & audit.protect_mask).sum()) == 0, entry["id"]


def test_mask_never_overlaps_the_printed_ink_without_a_caller_box() -> None:
    """**The red line**, measured on print: `protect=None` and the mask still misses the ink.

    `protect_box` is the label *rectangle*, which includes blank padding. Padding is pixel-
    identical to the fabric around it, so no pixel-level detector can separate the two, and
    the round-1 test that measured 0 there was measuring `~(box) & box` — a tautology (QA's
    finding). The printed pixels are a different claim, and it is the one the DoD is about:
    "the inpaint mask never overlaps the logo/text region". `ink/*.png` is the ground truth
    the fixture builder drew, so this is not the detector grading itself.

    The automatic detector really does have to do the work here: no caller box is passed.
    """
    for entry in cases("logo"):
        flat, alpha = fixture_bytes(entry)
        ink = fixture_ink(entry)
        audit = wrinkle_mask_for(flat, alpha)  # no caller box
        assert ink.any(), entry["id"]
        leaked = int((audit.final_mask & ink).sum())
        assert leaked == 0, (entry["id"], leaked, int((ink & audit.garment).sum()))
        # ...and the reason it can be 0: the print is *covered*, not merely untouched.
        covered = float((audit.protect_mask & ink).sum()) / int((ink & audit.garment).sum())
        assert covered > 0.99, (entry["id"], covered)
        assert "auto" in audit.notes["protect"]["sources"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known gap, with numbers: the automatic detector leaves a few pixels of the label "
        "RECTANGLE unprotected (logo-01 149, logo-02 34, logo-03 65, logo-04 56, logo-05 78 "
        "px on the round-2 fixtures; 382 px total, <= 1.8 % of any box). They are crease "
        "pixels inside the label's blank padding, not print — the printed pixels are covered "
        "(see the test above). strict=True so that reaching 0 turns this into XPASS and "
        "forces the assertion to be promoted instead of the xfail rotting in place."
    ),
)
def test_mask_never_enters_the_label_rectangle_without_a_caller_box() -> None:
    """The DoD sentence taken literally on the rectangle, with no caller help. Currently red.

    Reported rather than relaxed: the alternative (`<= N px`) is a threshold that would hide a
    regression back to the QA-measured 362 px. `evals/metrics/prettify.py` prints this same
    residual as `auto only px`, so the number lives in the artifact and not only here.
    """
    residuals: list[tuple[str, int, int]] = []
    for entry in cases("logo"):
        flat, alpha = fixture_bytes(entry)
        region = box_region(entry["protect_box"], alpha_shape(flat, alpha))
        audit = wrinkle_mask_for(flat, alpha)
        residuals.append((entry["id"], int((audit.final_mask & region).sum()), int(region.sum())))
    offenders = [(name, px) for name, px, _size in residuals if px]
    print("auto-only residual per logo case:", residuals)
    assert not offenders, offenders


def test_protection_subtraction_is_load_bearing() -> None:
    """Delete the subtraction and this goes red: the mask demonstrably wanted that box."""
    for entry in cases("logo"):
        flat, alpha = fixture_bytes(entry)
        box = tuple(entry["protect_box"])
        region = box_region(box, wrinkle_mask_for(flat, alpha).garment.shape)
        unprotected = prettify_module._build_wrinkle_mask(
            prettify_module._decode_rgb(flat, "flat"),
            prettify_module._decode_l(alpha, "alpha"),
            protect=None,
        )
        auto_only = int((unprotected.final_mask & region).sum())
        assert auto_only > 0, (entry["id"], upstream_hint(auto_only))


def upstream_hint(auto_only: int) -> str:
    return (
        f"the automatic detector alone removed nothing from the label box ({auto_only} px); "
        "check evals/fixtures/prettify/build_fixtures.py — the fold must cross the label"
    )


def test_caller_boxes_are_validated() -> None:
    flat, alpha, _garment = synthetic_garment()
    with pytest.raises(ValueError):
        wrinkle_mask_for(flat, alpha, protect=[(0, 0, 0, 10)])
    with pytest.raises(ValueError):
        wrinkle_mask_for(flat, alpha, protect=[(0, 0, 10)])


def test_automatic_detector_fires_on_the_synthetic_ink() -> None:
    """Protection has two sources; this one proves the automatic half is not dead code."""
    fired = 0
    for entry in cases("logo"):
        flat, alpha = fixture_bytes(entry)
        audit = wrinkle_mask_for(flat, alpha)  # no caller boxes: automatic only
        region = box_region(entry["protect_box"], audit.garment.shape)
        if int((audit.protect_mask & region).sum()) > 0:
            fired += 1
    assert fired == len(cases("logo")), (
        f"the automatic detector fired on {fired}/{len(cases('logo'))} synthetic labels"
    )


def printed_type_case(
    *, size: int, fabric: tuple[int, int, int], ink: tuple[int, int, int], text: str = "SNAP"
) -> tuple[bytes, bytes, np.ndarray]:
    """A garment swatch with printed type, plus the ground truth of the printed pixels.

    Built here rather than in the fixture set because these are *detector* probes, and QA's
    two named failures — a 48 px solid wordmark and light type on dark fabric — have to stay
    locked down even though no fixture photographs them.
    """
    from PIL import ImageDraw, ImageFont

    height, width = 260, 340
    flat = np.zeros((height, width, 3), dtype=np.float64)
    flat[:, :] = np.array(BACKDROP_RGB, dtype=np.float64)
    y0, y1, x0, x1 = height // 8, height - height // 8, width // 8, width - width // 8
    flat[y0:y1, x0:x1] = np.array(fabric, dtype=np.float64)
    alpha = np.zeros((height, width), dtype=np.uint8)
    alpha[y0:y1, x0:x1] = 255

    font = ImageFont.load_default(size=size)
    stencil = Image.new("L", (width, height), 0)
    ImageDraw.Draw(stencil).text((x0 + 14, (height - size) // 2), text, font=font, fill=255)
    truth = np.asarray(stencil) > 127
    canvas = Image.fromarray(np.rint(flat).astype(np.uint8)).convert("RGB")
    canvas.paste(Image.new("RGB", (width, height), ink), (0, 0), stencil)
    return rgb_bytes(np.asarray(canvas, dtype=np.float64)), png_bytes(alpha), truth


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        (
            "large solid wordmark (48 px)",
            {"size": 48, "fabric": (196, 198, 202), "ink": (24, 24, 32)},
        ),
        (
            "light type on dark fabric",
            {"size": 48, "fabric": (38, 40, 44), "ink": (248, 248, 245)},
        ),
        (
            "light type on dark, small",
            {"size": 14, "fabric": (38, 40, 44), "ink": (248, 248, 245)},
        ),
        ("mid-size dark type", {"size": 24, "fabric": (196, 198, 202), "ink": (24, 24, 32)}),
    ],
)
def test_auto_detector_covers_large_solid_and_light_type(label: str, kwargs: dict) -> None:
    """QA's two missed shape families, locked down.

    Round 1 scored 12 % coverage on a 48 px solid glyph and 0 % on white-on-dark; both are
    ordinary shapes on real garments, not edge cases. The pyramid in
    `_auto_protect_mask` is what fixes them: a large glyph at 1/4 resolution *is* small type,
    and the score is polarity-free so light ink scores like dark ink.
    """
    flat, alpha, truth = printed_type_case(**kwargs)
    audit = wrinkle_mask_for(flat, alpha)
    printed = int((truth & audit.garment).sum())
    assert printed > 100, (label, printed)
    covered = float((audit.protect_mask & truth).sum()) / printed
    assert covered >= 0.95, (label, covered)
    assert int((audit.final_mask & truth).sum()) == 0, label



# ----------------------------------------------------------------------------------- modes


def test_pass_through_is_byte_identical_and_says_so() -> None:
    flat, alpha = fixture_bytes(cases("logo")[0])
    result = prettify(flat, alpha, mode=PrettifyMode.PASS_THROUGH)
    assert result.prettified_png == flat
    notes = result.meta.notes
    assert notes["mode"] == "pass_through"
    assert notes["generative_step"] == "skipped"
    assert notes["pixels_touched"] == 0
    assert "NOT a cleaned image" in notes["note"]
    assert "fill" not in notes


def test_classical_mode_is_not_labelled_pass_through() -> None:
    """`CLASSICAL` changes pixels; calling it pass_through would be the forbidden lie."""
    flat, alpha = fixture_bytes(cases("crease")[0])
    result = prettify(flat, alpha, mode=PrettifyMode.CLASSICAL)
    assert result.meta.notes["mode"] == "classical"
    assert result.meta.notes["mode"] != PrettifyMode.PASS_THROUGH.value
    assert result.meta.backend != "pass_through"
    assert result.prettified_png != flat
    assert PrettifyMode.CLASSICAL in tuple(PrettifyMode)


def test_every_runnable_mode_measures_seconds() -> None:
    flat, alpha = fixture_bytes(cases("crease")[0])
    for mode in (PrettifyMode.PASS_THROUGH, PrettifyMode.CLASSICAL):
        result = prettify(flat, alpha, mode=mode)
        assert result.meta.seconds is not None, mode
        assert result.meta.seconds > 0.0, mode
        assert result.meta.stage == "prettify"
        assert result.meta.device == "cpu"


def test_two_runs_produce_identical_bytes() -> None:
    """Deterministic: no RNG, no model, no dictionary-order dependence."""
    flat, alpha = fixture_bytes(cases("logo")[0])
    first = prettify(flat, alpha, mode=PrettifyMode.CLASSICAL)
    second = prettify(flat, alpha, mode=PrettifyMode.CLASSICAL)
    assert first.prettified_png == second.prettified_png
    assert first.meta.notes["wrinkle_mask"] == second.meta.notes["wrinkle_mask"]


def test_classical_output_is_a_png_of_the_same_canvas() -> None:
    flat, alpha = fixture_bytes(cases("logo")[0])
    result = prettify(flat, alpha, mode=PrettifyMode.CLASSICAL)
    with Image.open(io.BytesIO(result.prettified_png)) as opened:
        assert opened.format == "PNG"
        assert opened.mode == "RGB"
        with Image.open(io.BytesIO(alpha)) as alpha_image:
            assert opened.size == alpha_image.size


# ------------------------------------------------------------------------------------ fill


def test_fill_does_not_change_pixels_outside_the_feathered_mask() -> None:
    """The fill is scoped to the mask; everything outside must be bit-identical."""
    flat, alpha, _garment = synthetic_garment(folds=8, fold_amplitude=0.28)
    audit = wrinkle_mask_for(flat, alpha)
    assert audit.final_mask.any(), "nothing to fill: the test would pass trivially"
    before = prettify_module._decode_rgb(flat, "flat")
    after, note = _attenuate(
        before, audit.final_mask, height=before.shape[0], width=before.shape[1]
    )
    support = prettify_module._binary_dilate(audit.final_mask, 8)
    changed = np.any(np.abs(after - before) > 0.5, axis=2)
    assert int((changed & ~support).sum()) == 0
    assert note["texture_synthesis"] is False
    assert note["pixels_changed"] > 0


def test_fill_actually_attenuates_local_contrast_inside_the_mask() -> None:
    """The fill's *effect*, not just its footprint: local contrast inside the mask must drop.

    `test_fill_does_not_change_pixels_outside_the_feathered_mask` says where the fill may
    write; this one says it does the job it exists for. Without it, `FILL_STRENGTH = 0.0`
    would be caught only by a "something changed" count — and a stage that looks busy while
    correcting nothing is exactly the failure mode this project keeps writing tests against.
    """
    flat, alpha, _garment = synthetic_garment(folds=8, fold_amplitude=0.30)
    audit = wrinkle_mask_for(flat, alpha)
    before = prettify_module._decode_rgb(flat, "flat")
    after, note = _attenuate(
        before, audit.final_mask, height=before.shape[0], width=before.shape[1]
    )
    sigma = 3.0
    before_lum = prettify_module._luminance(before)
    after_lum = prettify_module._luminance(after)
    high_pass_before = np.abs(before_lum - prettify_module._blur(before_lum, sigma))
    high_pass_after = np.abs(after_lum - prettify_module._blur(after_lum, sigma))
    inside = audit.final_mask
    assert int(inside.sum()) > 500, "no mask to measure the attenuation on"
    drop = float(high_pass_after[inside].mean()) / float(high_pass_before[inside].mean())
    assert drop < 0.95, f"local contrast inside the mask only fell to {drop:.3f}"
    # The mirror claim ("nothing outside the mask changes") lives in
    # test_fill_does_not_change_pixels_outside_the_feathered_mask, which compares pixels
    # rather than contrast: on a garment whose folds tile the whole area there is no
    # "far from the mask" region left to compare once the feather and blur supports are
    # excluded, and a test that silently measured an empty slice would prove nothing.
    assert note["pixels_changed"] > 0


def test_outside_the_mask_only_the_white_balance_gain_moves() -> None:
    """The whole stage, not just the fill: away from the mask the change is one global ratio.

    This is the sharp form of "only masked pixels get processed". A stray local edit, a
    vignette, a texture pass — anything with spatial structure — breaks the constant ratio.
    """
    flat, alpha, _garment = synthetic_garment(folds=6, fold_amplitude=0.26, cast=(1.05, 1.0, 0.96))
    result = prettify(flat, alpha, mode=PrettifyMode.CLASSICAL)
    audit = wrinkle_mask_for(flat, alpha)
    before = prettify_module._decode_rgb(flat, "flat")
    after = decode(result.prettified_png)
    support = prettify_module._binary_dilate(audit.final_mask, 10)
    safe = (~support) & (before.min(axis=2) > 40.0) & (before.max(axis=2) < 220.0)
    assert int(safe.sum()) > 1000, "not enough untouched pixels to test the claim"
    ratio = after[safe] / before[safe]
    gains = np.array(result.meta.notes["white_balance"], dtype=np.float64)
    assert np.allclose(ratio, gains, atol=0.02), (ratio.mean(axis=0), gains)


# ----------------------------------------------------------------------------------- SDXL
#
# Two kinds of test live here and they must not be confused:
#
#   * the ones that need the 6.9 GB of weights (marked `requires_sdxl_weights`). On a machine
#     without them they SKIP — a CI box must not pull 7 GB to run the test suite;
#   * the ones that need nothing but numpy: the refusal text, the feather arithmetic, and the
#     "no torch at import time" rule. Those run everywhere, because they are what a machine
#     without the weights experiences.

requires_sdxl_backend = pytest.mark.skipif(
    not sdxl_backend_available()[0],
    reason=(
        "mode=SDXL_INPAINT needs both the `sdxl` dependency group (0.72 GB, `make "
        "install-sdxl`) and the pinned 6.9 GB of weights (`make fetch-sdxl`). `make test` "
        "must run on a checkout with neither — the refusal path is what gets tested there. "
        "The check uses importlib.util.find_spec, so torch is not imported even here"
    ),
)
# Kept as an alias so the round-2 test names stay readable in old reports.
requires_sdxl_weights = requires_sdxl_backend


def test_sdxl_inpaint_refuses_with_the_fix_inside_the_error(monkeypatch, tmp_path) -> None:
    """Without the weights the stage says what is missing, how big it is, and what to run."""
    monkeypatch.delenv("SNAPLIST_SDXL_DIR", raising=False)  # the more specific override wins
    monkeypatch.setenv("SNAPLIST_MODEL_DIR", str(tmp_path))
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(PrettifyBackendUnavailable) as excinfo:
        prettify(flat, alpha, mode=PrettifyMode.SDXL_INPAINT)
    message = str(excinfo.value)
    assert "make fetch-sdxl" in message
    assert str(sdxl_weights_dir()) in message
    assert str(tmp_path) in message
    assert "E2-T2" in message
    assert "never downloads" in message
    # A refusal must not leave a directory behind as a side effect of looking.
    assert not sdxl_weights_dir().exists()
    # And it must be a StageNotImplemented, so the worker treats it as terminal.
    from packages.vision import StageNotImplemented

    assert isinstance(excinfo.value, StageNotImplemented)


def test_sdxl_refusal_quotes_size_and_licence(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("SNAPLIST_SDXL_DIR", raising=False)  # the more specific override wins
    monkeypatch.setenv("SNAPLIST_MODEL_DIR", str(tmp_path))
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(PrettifyBackendUnavailable) as excinfo:
        prettify(flat, alpha, mode=PrettifyMode.SDXL_INPAINT)
    message = str(excinfo.value)
    assert "GB" in message
    assert "CreativeML Open RAIL++-M" in message
    assert "sdxl-inpaint-0.1" in message
    assert "restrictions" in message, "a restricted licence must be named as restricted"


def test_missing_dependency_group_is_named_as_a_group(monkeypatch, tmp_path) -> None:
    """A missing `sdxl` group must not be reported as "missing weights".

    The two gaps have different fixes costing 0.72 GB and 6.9 GB. Sending an operator to
    `make fetch-sdxl` when what is missing is torch would have them re-download weights they
    already have; this test is what keeps the refusal pointing at the right one.
    """
    monkeypatch.delenv("SNAPLIST_SDXL_DIR", raising=False)
    monkeypatch.setenv("SNAPLIST_MODEL_DIR", str(tmp_path))
    assert isinstance(sdxl_missing_deps(), list), "the real check returns module names"
    monkeypatch.setattr(prettify_module, "sdxl_missing_deps", lambda: ["torch", "diffusers"])
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(PrettifyBackendUnavailable) as excinfo:
        prettify(flat, alpha, mode=PrettifyMode.SDXL_INPAINT)
    message = str(excinfo.value)
    assert "`sdxl` dependency group is not installed" in message
    assert "torch, diffusers" in message
    assert "make install-sdxl" in message
    assert "make fetch-sdxl" in message, "the reader may need both, so name both"
    assert "zero network calls" in message


@pytest.mark.skipif(
    not sdxl_weights_available(),
    reason="needs the weights on disk to construct the 'only the group is missing' case",
)
def test_only_a_missing_group_does_not_send_anyone_to_a_6_9_gb_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Fix sentence must close the *actual* gap, or the operator loops.

    This is the state this very machine was in: 18 weight files present and verified, the
    optional `sdxl` group absent. The refusal used to append `fetch_hint()` unconditionally, so
    it said "run `make fetch-sdxl` (… 6.9 GB …)" — which is idempotent, prints "already present
    and verified", exits 0, and leaves the operator on the same error. A refusal that names a
    command that cannot help is a refusal that wastes a round trip.
    """
    monkeypatch.setattr(prettify_module, "sdxl_missing_deps", lambda: ["torch"])
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(PrettifyBackendUnavailable) as excinfo:
        prettify(flat, alpha, mode=PrettifyMode.SDXL_INPAINT)
    message = str(excinfo.value)
    fix = message[message.index("Fix:") : message.index(". The base install")]
    assert "make install-sdxl" in fix
    assert "fetch-sdxl" not in fix, fix
    # Say why nothing will be downloaded, or the sentence reads as a 6.9 GB ask anyway.
    assert "already on disk" in fix, fix


def test_both_gaps_are_reported_and_the_fetch_target_alone_is_called_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Neither, alone, is the fix — and the message says so instead of picking one at random."""
    monkeypatch.delenv("SNAPLIST_SDXL_DIR", raising=False)
    monkeypatch.setenv("SNAPLIST_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(prettify_module, "sdxl_missing_deps", lambda: ["torch"])
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(PrettifyBackendUnavailable) as excinfo:
        prettify(flat, alpha, mode=PrettifyMode.SDXL_INPAINT)
    message = str(excinfo.value)
    fix = message[message.index("Fix:") : message.index(". The base install")]
    assert "make install-sdxl" in fix
    assert "make fetch-sdxl" in fix
    assert "same error would come back" in fix, fix


def test_sdxl_dir_env_overrides_the_shared_model_dir(monkeypatch, tmp_path) -> None:
    """`SNAPLIST_SDXL_DIR` is checked first, so a second model dir does not fight u2net."""
    monkeypatch.setenv("SNAPLIST_MODEL_DIR", str(tmp_path / "shared"))
    monkeypatch.setenv("SNAPLIST_SDXL_DIR", str(tmp_path / "direct"))
    assert sdxl_weights_dir() == tmp_path / "direct"
    monkeypatch.delenv("SNAPLIST_SDXL_DIR")
    assert sdxl_weights_dir() == tmp_path / "shared" / "sdxl-inpaint-0.1"


def test_importing_prettify_does_not_import_torch() -> None:
    """The module-level import must stay model-free, or `make test` needs 581 MB of torch.

    Checked in a fresh interpreter (`sys.modules` is the evidence), because by the time the
    rest of this file has run, torch is loaded and an in-process assertion would be measuring
    the wrong thing. This is a slow-machine-first rule, not a nicety: the cheap segmentation
    backend runs on onnxruntime precisely so a checkout without torch works — and since
    round 2 the stack is an optional dependency group, so a base install has no torch at all.

    The same test covers `sdxl_missing_deps()`: it runs during pytest **collection** (the skip
    marker) and on every refusal, so it has to answer by finding modules, never by importing
    them. If it ever imported, this test would go red and `make test` would start paying it.
    """
    script = textwrap.dedent(
        """
        import sys

        import packages.vision.prettify as p

        # No import blocker here on purpose: the availability check *legitimately* looks torch
        # up by name (`importlib.util.find_spec`), so a hook that raises on any mention of
        # torch would break the thing it is meant to test. What must not happen is torch being
        # **executed** and landing in `sys.modules`, and that is what this asserts — in a fresh
        # interpreter, so it measures this import and not the rest of the suite's.
        assert "torch" not in sys.modules, sorted(sys.modules)
        assert "diffusers" not in sys.modules, sorted(sys.modules)

        missing = p.sdxl_missing_deps()
        assert isinstance(missing, list), missing
        assert set(missing) <= set(p.SDXL_DEP_MODULES), missing
        assert p.sdxl_backend_available()[0] in (True, False)

        assert "torch" not in sys.modules, "find_spec must not import its target"
        assert "diffusers" not in sys.modules, sorted(sys.modules)
        print(p.SDXL_STEPS)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == str(SDXL_STEPS)


def test_weights_manifest_pins_a_commit_not_a_branch() -> None:
    """A revision that can move is not a pin, and 18 named files is not a directory glob."""
    import re

    revision = prettify_module.SDXL_REVISION
    assert re.fullmatch(r"[0-9a-f]{40}", revision), revision
    assert len(SDXL_REQUIRED_FILES) == 18, sorted(SDXL_REQUIRED_FILES)
    # Only the fp16 variant: the fp32 files are ~3x the size and produce identical output.
    safetensors = [name for name in SDXL_REQUIRED_FILES if name.endswith(".safetensors")]
    assert len(safetensors) == 4, safetensors
    for name in safetensors:
        assert name.endswith(".fp16.safetensors"), name
    for name, size in SDXL_REQUIRED_FILES.items():
        assert size > 0, name


def test_missing_files_are_named_per_file(monkeypatch, tmp_path) -> None:
    """A truncated file is as unusable as an absent one, and the error must say which."""
    monkeypatch.setenv("SNAPLIST_SDXL_DIR", str(tmp_path))
    (tmp_path / "model_index.json").write_bytes(b"{}")
    missing = sdxl_missing_files()
    assert any(name.startswith("model_index.json (") for name in missing), missing
    assert "unet/diffusion_pytorch_model.fp16.safetensors (missing)" in missing
    assert not sdxl_weights_available()


def test_fetch_deletes_a_half_download_and_never_promotes_it(monkeypatch, tmp_path) -> None:
    """`make fetch-sdxl` on a flaky network must leave the loader with *nothing*, not a stub.

    A half tree that a loader accepts is worse than no tree: it produces pixels, so it does not
    look broken. Both failure shapes are covered — the download raising, and the download
    "succeeding" with the wrong bytes.
    """
    monkeypatch.setenv("SNAPLIST_SDXL_DIR", str(tmp_path / "sdxl"))
    # huggingface_hub ships in the `sdxl` group, so on a base install this test skips rather
    # than failing: the download path is not something `make test` may depend on.
    huggingface_hub = pytest.importorskip("huggingface_hub")

    def _explode(**kwargs: object) -> str:
        partial = Path(str(kwargs["local_dir"]))
        (partial / "unet").mkdir(parents=True, exist_ok=True)
        (partial / "unet" / "diffusion_pytorch_model.fp16.safetensors").write_bytes(b"\0" * 64)
        raise OSError("connection reset by peer")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _explode)
    with pytest.raises(OSError):
        fetch_sdxl_weights(verbose=False)
    assert not (tmp_path / "sdxl").exists()
    assert not (tmp_path / "sdxl.partial").exists()

    def _truncate(**kwargs: object) -> str:
        partial = Path(str(kwargs["local_dir"]))
        (partial / "unet").mkdir(parents=True, exist_ok=True)
        (partial / "unet" / "diffusion_pytorch_model.fp16.safetensors").write_bytes(b"\0" * 64)
        return str(partial)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _truncate)
    with pytest.raises(RuntimeError) as excinfo:
        fetch_sdxl_weights(verbose=False)
    assert "does not match the pin" in str(excinfo.value)
    assert not (tmp_path / "sdxl").exists()
    assert not (tmp_path / "sdxl.partial").exists()


def test_outward_feather_is_full_inside_and_decays_outside() -> None:
    """The feather's shape, as arithmetic: 1 on the mask, monotone, exactly 0 past the radius.

    This is the property the Gaussian form failed: a 2 px-wide mask blurred with sigma 2 has a
    centre value below 0.5, so "repaint strength 1 inside the mask" is not something a blur can
    promise for a thin-line mask.
    """
    mask = np.zeros((40, 40), dtype=bool)
    mask[18:20, 8:32] = True  # a 2 px-wide line, like a crease core
    radius = 4
    weight = _outward_feather(mask, radius=radius)
    assert float(weight[mask].min()) == 1.0, "the mask core must be fully repainted"
    assert float(weight.max()) == 1.0
    # Ring by ring inside the radius: strictly decreasing and strictly between 0 and 1.
    ring_values = []
    for ring in range(1, radius + 1):
        grown = prettify_module._binary_dilate(mask, ring)
        inner = prettify_module._binary_dilate(mask, ring - 1)
        band = grown & ~inner
        if band.any():
            ring_values.append(float(weight[band].mean()))
    assert len(ring_values) == radius, ring_values
    assert ring_values == sorted(ring_values, reverse=True), ring_values
    assert all(0.0 < value < 1.0 for value in ring_values)
    # …and exactly zero one ring further out: the ramp is bounded, not asymptotic.
    beyond = ~prettify_module._binary_dilate(mask, radius)
    assert float(weight[beyond].max()) == 0.0
    assert _outward_feather(mask, radius=0).max() == 1.0


def test_repaint_weight_never_enters_the_protection_region_or_the_boundary_band() -> None:
    """Round 2 inherits round 1's two exclusions, and the *feather* must inherit them too.

    Without the `support` clip the outward ring would grow straight over printed ink: the mask
    stops at the label, but the ring does not know that. Asserted on the same mask object
    `prettify()` consumes, so this audits the real thing.
    """
    for entry in cases("logo"):
        flat, alpha = fixture_bytes(entry)
        audit = wrinkle_mask_for(flat, alpha, protect=[tuple(entry["protect_box"])])
        weight, radius = _feathered_repaint_weight(audit)
        assert radius >= SDXL_FEATHER_MIN_PX, radius
        assert int((weight > 0.0)[audit.protect_mask].sum()) == 0, entry["id"]
        assert int((weight > 0.0)[~audit.interior].sum()) == 0, entry["id"]
        assert float(weight[audit.final_mask].min()) == 1.0, entry["id"]


def test_sdxl_work_size_keeps_aspect_and_is_64_aligned() -> None:
    for height, width in ((480, 360), (480, 480), (1200, 960), (300, 2000)):
        work_h, work_w = _sdxl_work_size(height, width, SDXL_WORK_SIDE)
        assert work_h % 64 == 0 and work_w % 64 == 0, (height, width, work_h, work_w)
        assert max(work_h, work_w) <= SDXL_WORK_SIDE + 64, (height, width, work_h, work_w)
        assert abs((work_h / work_w) - (height / width)) < 0.12, (height, width, work_h, work_w)


def test_sdxl_rejects_impossible_parameters_before_loading_anything(monkeypatch, tmp_path) -> None:
    """Bad numbers must fail on the numbers, not inside a 7 GB model load."""
    flat, alpha = fixture_bytes(cases("crease")[0])
    for kwargs in ({"steps": 0}, {"strength": 0.0}, {"strength": 1.5}, {"guidance": 0.0}):
        with pytest.raises(ValueError):
            prettify_sdxl(flat, alpha, **kwargs)
    with pytest.raises(ValueError) as excinfo:
        prettify_sdxl(flat, alpha, side=256)
    assert "SDXL below that duplicates hems" in str(excinfo.value)


def test_empty_mask_skips_the_model_and_says_so(monkeypatch, tmp_path) -> None:
    """No creases selected => no inpaint. Loading 7 GB to paint zero pixels would be a lie.

    The weights directory here is empty, so if the code reached for the model it would raise
    instead of returning — which is exactly the assertion. The cast matters: without it the
    white balance is an identity and "the output is the input" would be true for the wrong
    reason.
    """
    monkeypatch.setenv("SNAPLIST_SDXL_DIR", str(tmp_path / "absent"))
    flat, alpha, _garment = synthetic_garment(cast=(1.060, 1.000, 0.950))
    result = prettify_sdxl(flat, alpha, steps=2, side=512)
    notes = result.meta.notes
    assert notes["generative_step"].startswith("skipped"), notes["generative_step"]
    assert notes["generative"]["model_loaded"] is False
    assert notes["generative"]["repaint_px"] == 0
    assert notes["mode"] == PrettifyMode.SDXL_INPAINT.value
    assert result.meta.device == "cpu", "no model ran, so no device did either"
    # The white balance still ran, and *only* it: every pixel is the balanced input.
    audit = wrinkle_mask_for(flat, alpha)
    assert np.array_equal(decode(result.prettified_png), _quantise(audit.balanced).astype(float))
    assert result.prettified_png != flat
    with Image.open(io.BytesIO(result.prettified_png)) as opened:
        assert opened.mode == "RGB"


@pytest.fixture(scope="module")
def sdxl_run() -> tuple[dict, bytes, bytes, list[tuple[int, int, int, int]], object, object]:
    """Two identical SDXL runs on one logo fixture, computed once for the whole module.

    `steps=4, side=512` on purpose: these tests are about *contracts* (determinism, zero change
    outside the weight, canvas), not about image quality, and the contract does not change with
    the step count. The measured default is 30 steps at 1024 — see
    `docs/HANDOFF-vision.md`.
    """
    available, why_not = sdxl_backend_available()
    if not available:
        pytest.skip(f"mode=SDXL_INPAINT is not runnable here: {why_not}")
    entry = cases("logo")[0]
    flat, alpha = fixture_bytes(entry)
    protect = [tuple(entry["protect_box"])]
    first = prettify_sdxl(flat, alpha, protect=protect, steps=4, side=512)
    second = prettify_sdxl(flat, alpha, protect=protect, steps=4, side=512)
    return entry, flat, alpha, protect, first, second


@requires_sdxl_weights
def test_sdxl_is_byte_identical_for_the_same_seed(sdxl_run) -> None:
    """Same input, same seed, same bytes — the reason `SEED` is a module constant."""
    _entry, _flat, _alpha, _protect, first, second = sdxl_run
    assert first.prettified_png == second.prettified_png
    assert first.meta.notes["seed"] == SDXL_SEED == second.meta.notes["seed"]
    assert first.meta.notes["generative"]["num_inference_steps"] == 4
    assert first.meta.notes["generative"]["strength"] == SDXL_STRENGTH
    assert first.meta.notes["generative_step"] == "sdxl-inpainting-0.1"
    assert first.meta.backend == "sdxl_inpaint"
    assert first.meta.device in ("mps", "cpu"), first.meta.device
    assert first.meta.seconds is not None and first.meta.seconds > 0.0


@requires_sdxl_weights
def test_sdxl_output_equals_the_white_balanced_input_where_the_weight_is_zero(sdxl_run) -> None:
    """The generative step's write boundary, exactly: `w == 0` ⇒ output is the *input*.

    Not "close to": bit-identical. That is what makes the logo/text protection and the boundary
    band real guarantees in this mode rather than strong preferences, and it is why the
    composite is written in `prettify_sdxl` instead of inside the pipeline.
    """
    entry, flat, alpha, protect, first, _second = sdxl_run
    audit = wrinkle_mask_for(flat, alpha, protect=protect)
    weight, _radius = _feathered_repaint_weight(audit)
    reference = _quantise(audit.balanced)
    with Image.open(io.BytesIO(first.prettified_png)) as opened:
        produced = np.asarray(opened.convert("RGB"))
    unchanged = weight == 0.0
    assert int(unchanged.sum()) > 1000, "not enough untouched pixels to make the claim"
    assert np.array_equal(produced[unchanged], reference[unchanged]), entry["id"]
    # The protection rectangle is a subset of "weight == 0", but assert it on its own so a
    # regression that zeroes the wrong region cannot hide behind the union.
    assert np.array_equal(produced[audit.protect_mask], reference[audit.protect_mask]), entry["id"]


@requires_sdxl_weights
def test_sdxl_output_is_a_png_of_the_same_canvas(sdxl_run) -> None:
    _entry, _flat, _alpha, _protect, first, _second = sdxl_run
    with Image.open(io.BytesIO(first.prettified_png)) as opened:
        assert opened.format == "PNG"
        assert opened.mode == "RGB"
        assert max(opened.size) == 480, opened.size


# ------------------------------------------------------------------------------ validation


def test_empty_bytes_are_rejected() -> None:
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(ValueError):
        prettify(b"", alpha, mode=PrettifyMode.CLASSICAL)
    with pytest.raises(ValueError):
        prettify(flat, b"", mode=PrettifyMode.CLASSICAL)


def test_shape_mismatch_names_the_cause() -> None:
    flat, _alpha, _garment = synthetic_garment(size=(120, 160))
    other_alpha = np.full((90, 160), 255, dtype=np.uint8)
    with pytest.raises(ValueError) as excinfo:
        prettify(flat, png_bytes(other_alpha), mode=PrettifyMode.CLASSICAL)
    assert "alpha shape" in str(excinfo.value)
    assert "rectify" in str(excinfo.value)


def test_empty_garment_is_named_not_guessed() -> None:
    flat, _alpha, _garment = synthetic_garment()
    empty = np.zeros((180, 240), dtype=np.uint8)
    with pytest.raises(ValueError) as excinfo:
        prettify(flat, png_bytes(empty), mode=PrettifyMode.CLASSICAL)
    assert "no_garment" in str(excinfo.value)


def test_unknown_mode_string_raises_instead_of_guessing() -> None:
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(ValueError) as excinfo:
        prettify(flat, alpha, mode="oil_painting")  # type: ignore[arg-type]
    assert "not a PrettifyMode" in str(excinfo.value)
