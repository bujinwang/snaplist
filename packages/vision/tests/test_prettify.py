"""Tests for E2-T2 `prettify()` (packages/vision/prettify.py) — round 1, no model.

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
* **SDXL round 2 refuses with the fix inside the error** — `test_sdxl_inpaint_refusal_...`.

The fixtures are licensed photos with **synthetic** labels and folds drawn on them
(`evals/fixtures/prettify/README.md`). Nothing here is a claim that the detector recalls real
packaging, and nothing here measures "are the creases gone" — that is E2-T3's manual rubric.
"""

from __future__ import annotations

import io
import json
import socket
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import packages.vision.prettify as prettify_module
from packages.vision.prettify import (
    BACKDROP_RGB,
    BALANCE_MIN_BACKGROUND_PX,
    WRINKLE_COVERAGE_CAP,
    PrettifyBackendUnavailable,
    _attenuate,
    prettify,
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


def test_sdxl_inpaint_refuses_with_the_fix_inside_the_error(monkeypatch, tmp_path) -> None:
    """Round 2 is not implemented: the error must carry the fix, and nothing may be fetched."""
    monkeypatch.setenv("SNAPLIST_MODEL_DIR", str(tmp_path))
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(PrettifyBackendUnavailable) as excinfo:
        prettify(flat, alpha, mode=PrettifyMode.SDXL_INPAINT)
    message = str(excinfo.value)
    assert "make fetch-models" in message
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
    monkeypatch.setenv("SNAPLIST_MODEL_DIR", str(tmp_path))
    flat, alpha = fixture_bytes(cases("logo")[0])
    with pytest.raises(PrettifyBackendUnavailable) as excinfo:
        prettify(flat, alpha, mode=PrettifyMode.SDXL_INPAINT)
    message = str(excinfo.value)
    assert "GB" in message
    assert "CreativeML Open RAIL++-M" in message
    assert "sdxl-inpaint-0.1" in message


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
