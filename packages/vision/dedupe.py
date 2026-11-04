"""STAGE 5 — near-duplicate detection. Owner: CV role (E3, ticket E3-T4).

Stops a seller re-uploading the same photo of the same black tee from creating item #4.
Cheap, deterministic, **zero network calls**: no weights to download, no API round-trip, no
model at all — `hashlib` + numpy + Pillow, all of which the project already has
(`docs/DEPS.md` untouched).

    find_duplicate(user_id: str, image: bytes, candidates: Sequence[str]) -> DedupeResult

`DedupeResult.match_item_id` is `None` when nothing is close enough, and
`DedupeResult.similarity` is `None` (**UNMEASURED**) when no candidate could be measured —
never `0.0` standing in for "we did not look".

Two layers, one key
-------------------
`apps/api/requests.py::CreateItemRequest.source_sha256` already declares the dedupe key: the
sha256 of the uploaded image bytes, 64 lowercase hex, nullable, accepted from the client since
before this stage existed. So dedupe is **two layers over the same key**, and this module does
not invent a second one:

1. **Exact layer — `sha256(image)` equals the candidate's digest.** Then it is the same bytes:
   `similarity = 1.0`, no threshold, no false-merge risk. A 1.0 here is the *definition* of the
   layer, not a measured capability; do not quote it as accuracy. This layer is not decoration:
   a **flat, textureless frame** (care-label close-up, plain backdrop, grey flat-lay) has no
   structural direction, so the perceptual layer answers "no match" for two frames that look
   identical. Without the digest layer those uploads would never be de-duplicated at all.
2. **Perceptual layer — the same photograph re-encoded, rescaled, re-framed or re-exposed.**
   This is the layer the thresholds and the false-merge rate describe, because this is the layer
   that can be wrong.

Where the candidate digests come from: **the candidate file's own bytes**. `candidates` are
paths (see below), so the digest is `sha256` of the bytes on disk — which is exactly the
`source_sha256` the client declared for that item *provided the API stores the uploaded bytes
verbatim*. That is true of the direct-upload path in `apps/api/requests.py` today, and it is the
seam to re-check when E5-T4 lands: **if ingest ever re-encodes before storing, the exact layer
silently degrades into the perceptual one, with no error.** Storing `source_sha256` on the item
row and hashing the upload only once would be the better shape, but that needs a column and a
signature this ticket may not add — `find_duplicate`'s parameters are frozen by
`test_public_signatures_are_stable`.

What `candidates` means (decided here, because the stub did not say)
--------------------------------------------------------------------
Each element is a **filesystem path to an image file** (JPEG/PNG/WebP/GIF — anything Pillow
decodes), and `match_item_id` is that same string, returned verbatim, when it matches. The
caller keeps the path -> item-id mapping; this module never invents an id and never takes a
database dependency.

    paths = [row.image_path for row in previous_items_of(user_id)]
    result = find_duplicate(user_id, new_upload_bytes, paths)
    if result.match_item_id is not None:
        duplicate_of_item = path_to_item_id[result.match_item_id]

Precedence and failure behaviour, stated so nobody has to guess:

* **exact beats perceptual.** The loop returns on the first byte-identical candidate, even if an
  earlier candidate already passed the perceptual gates: identical bytes are stronger evidence
  than any score. Within a layer the earliest candidate in the sequence wins, so the result
  never depends on set/dict iteration order.
* **a candidate that is not a path** (an int, `None`, a list) raises `TypeError` before anything
  is examined. Failing open there would be the quiet route to a duplicate: the candidate would be
  skipped and the upload reported as new. `os.PathLike` is accepted and normalised, because a
  `pathlib.Path` is the most natural thing a caller writes and it used to hit exactly that hole.
* **unreadable candidate** (missing file, permission denied, not an image, truncated JPEG)
  -> logged as a warning, **skipped**, and *not* merged. A candidate we cannot measure must
  never produce a merge, and must not abort the other candidates either. Note that the exact
  layer needs no decode, so an unreadable-but-identical file would still match; undecodable
  bytes only block the perceptual layer.
* **all candidates unreadable** (or `candidates` empty) -> `DedupeResult(None, None)`.
* **the exact layer runs before any decoding.** A digest hit is a fact about bytes, not about
  pixels: an image Pillow cannot read is still the same upload if its bytes match, so it must not
  be lost to a decode failure. This is also the cheapest path — a plain re-upload never touches
  Pillow at all — and it is the reason a flat, textureless frame (a care-label close-up, a plain
  backdrop, a grey flat-lay) can be de-duplicated at all: the perceptual layer has no structural
  direction to compare there and will report "no match" for two frames that look alike.
* **the query image itself is undecodable** -> `ValueError`, raised only after the digest layer
  has found nothing. That is caller error: the job handed us bytes it could not read, and silently
  returning "no duplicate" would hide a broken worker. Empty bytes are the same error, raised
  first, because zero bytes are never a valid upload.
* `similarity` is **1.0 for an exact match** and the best structural score otherwise, so a
  reader must not compare a 1.0 (definition) with a 0.95 (measurement) as if both were measured.
  A high value with `match_item_id is None` means the colour gate rejected the match; the CLI
  prints both columns plus the layer for exactly that reason.

Why the perceptual layer is not CLIP
------------------------------------
A CLIP-style embedding would answer "same *kind* of garment", which is the wrong question: two
different tan riding breeches are the same kind of garment. Worse, CLIP means downloading a
~350 MB checkpoint on first call or shipping it in the image — a network call inside a
zero-network stage (BRIEF) and a dependency this ticket is not allowed to add
(`docs/DEPS.md`). It is also non-deterministic across BLAS builds, and this result is going to
sit in a fixture gate.

So v1 answers a narrower question honestly: *is this the same photograph, re-encoded /
rescaled / re-framed / re-exposed?* That is the duplicate a seller actually creates — the
camera-roll re-upload, the screenshot, the "I'll just re-send it" — not a second studio
session. It follows that a duplicate found by a *different photograph of the same item* is
**missed**, and `README.md` in the fixture directory says so in the same words.

What the perceptual layer compares
----------------------------------
1. Decode, convert to RGB, downsample so the long edge is <= 384 px.
2. **Structure**: the frame is resampled onto a 16x16x3 grid, each channel's mean is removed
   (brightness and white balance cancel), and the result is L2-normalised. Two frames are
   compared by the cosine of those vectors, maximised over a small pyramid of isotropic zoom
   levels on *both* sides (`ZOOM_LEVELS`) — 25 alignments, which is what makes a 12 % white
   border or a 90 % centre crop land back on the same grid. The pyramid is symmetric, so the
   score is symmetric: swapping the two images cannot change the decision.
3. **Colour**: a 12x4 hue x saturation histogram, weighted by saturation x value, so the
   neutral background of a catalogue shot contributes nothing, and filled with **soft
   (interpolated) bins** so a few degrees of hue drift does not shatter the intersection.
   The score is histogram intersection (`sum(min(a,b))`). It is a *gate*, not part of the
   number reported as `similarity`: two colourways of one garment must never merge, but the
   structural score alone cannot see a hue shift, because it is computed on mean-centred RGB.

A frame with no chroma at all (a greyscale photo, a blank wall) has an empty colour
histogram; there the gate is **uninformative and passes**, and the structural threshold
carries the whole decision. That is deliberate: neutral garments are most of a menswear
catalogue.

Thresholds and where they come from
-----------------------------------
`STRUCTURE_MIN = 0.90`, `COLOUR_MIN = 0.55`, measured on `evals/fixtures/dedupe/` (20 pairs:
3 exact, 7 near, 10 genuinely distinct items). Exact pairs are excluded from these numbers by
construction — they short-circuit at the digest.

| | structural | colour |
|---|---|---|
| weakest near duplicate (`near-05`: 90 % crop, shifted 3 %, +6 % brightness) | 0.915 | 0.965 |
| weakest near duplicate on colour (`near-04`: −12 % brightness, JPEG q55) | 0.9999 | 0.946 |
| strongest distinct pair (`top-01` white jersey vs `top-02` white tee) | 0.602 | 0.109 |

**Structure 0.90** sits 0.30 above the strongest false pair and only 0.015 under the weakest
duplicate. The asymmetry is intentional: the recall margin is the one that is allowed to be
thin. `near-05` is a harsher transform than the others (the 3 % shift is a translation, which
the zoom pyramid does not model), so near-recall on the fixture is 1.00 *because* the threshold
is right at its edge — a harder crop would be missed, and that is the cheap error.

**Colour 0.55** sits 0.40 under the weakest duplicate. Five of the ten distinct pairs are also
under it on colour (0.11 – 0.49: `dist-03/06/07/08/09`); the other five are colour-similar
(0.72 – 0.85).

**On this fixture the colour gate is not observable, and saying otherwise would be false.** Set
`COLOUR_MIN = 0.0` and `false_merge_rate` stays `0.000`: every distinct pair is already stopped
by structure, whose maximum over the ten negatives is 0.6023 against a 0.90 threshold (mutation
M3 in `docs/HANDOFF-vision.md` turns exactly one test red, and it is a synthetic one). So the
fixture measures **the structure gate**; the colour gate is defended by unit tests only —
`test_colour_veto_blocks_a_same_frame_different_colourway` builds the case the fixture cannot
hold (same frame, hue rotated 60 degrees: structure 0.987, colour 0.006, merged if the gate is
removed) and `test_small_colour_cast_does_not_block_a_real_duplicate` guards the recall side. The
gate is real and it is load-bearing against a *recoloured upload*; it is simply not what any of
the twenty-pair numbers are testing. A fixture that could show it would need a same-composition,
different-colourway negative, which is a synthetic pair too — so the unit test is where it lives,
not a twenty-first fixture entry.

The 0.55 number itself comes from a hue-rotation ladder on `base/bottom-01.jpg`, each step
scored through this module: 5 deg -> 0.889, 15 deg -> 0.630, 20 deg -> 0.503, 25 deg -> 0.315,
60 deg -> 0.006. So the gate tolerates roughly 17 degrees of global colour cast (a different
phone, a different white balance) and rejects a genuinely different colourway. Without the
soft bins the same ladder read 5 deg -> 0.347, i.e. the gate would have vetoed honest
re-uploads; that measurement is why `_colour_histogram` interpolates.

**The perceptual threshold is in-sample.** It was chosen on these 20 pairs, and the near
positives are same-source transforms rather than a second capture, so near-recall on this
fixture is an upper bound (see `evals/fixtures/dedupe/README.md`). The false-merge number is
the honest one. The exact layer needs no such caveat and gets none: it is a digest comparison.

Measured limits, not fixed by tuning (each number reproduced on this repo's fixtures):
  * a *different photo of the same item* is not detected (see above). The best score between
    two different pairs of breeches in the fixture is 0.592 (`dist-03`), under the threshold by
    a margin no reasonable tuning closes — so this is a design boundary, not a near miss;
  * **mirrored copies are missed**: flipping `base/bottom-01.jpg` scores 0.844. `ZOOM_LEVELS`
    shifts scale, not chirality. Adding a flip level is a one-line change and is deliberately
    not done yet — a mirrored photograph of a garment is a second capture, and v1 does not
    claim to catch those;
  * an **aspect-distorted** (squashed or stretched) re-upload *is* matched — 1.0000 at a 60 %
    horizontal squash — because the grid is normalised to the frame, not to the content. That
    is the right answer for the product and the reason there is **no aspect-ratio gate**: the
    obvious alternative, cropping to a content bounding box first, was measured and rejected.
    A uniform-border trim put a 15 %-padded boot copy at 0.396 (the boot's own background is
    trimmed in one image and not the other), i.e. it turned a real duplicate into a miss to buy
    a constraint the zoom pyramid already provides. That pair was dropped in the exact/near
    rebalance; the measurement is what matters, not the fixture id;
  * a photo that is almost entirely uniform has no structural direction, so the perceptual layer
    treats it as "no match" rather than as a perfect match — failing open there would merge two
    blank images. `test_flat_frame_with_one_small_mark_is_not_a_match` pins it, and
    `test_identical_flat_frame_is_an_exact_match` pins the digest layer's opposite answer for the
    byte-identical case;
  * the exact layer cannot see through a **re-encode**: two uploads of one photo with different
    bytes fall to the perceptual layer, which is why that layer exists at all.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

__all__ = ["DedupeResult", "find_duplicate", "STRUCTURE_MIN", "COLOUR_MIN", "EXACT_SIMILARITY"]

logger = logging.getLogger(__name__)

# --- tuning constants (see the module docstring for the measurements behind each one) ------
STRUCTURE_MIN: float = 0.90
COLOUR_MIN: float = 0.55
GRID: int = 16
WORKING_SIDE: int = 384
#: Isotropic zoom factors applied to each side before the grids are compared. 1/1.30 .. 1.30
#: covers a 12 % white border (content shrinks to 0.81) and a 90 % centre crop (content grows
#: to 1.11) with room to spare; 25 alignments in total, chosen for cost rather than elegance.
ZOOM_LEVELS: tuple[float, ...] = (1.0, 1.14, 1.30, 0.88, 0.77)
HUE_BINS: int = 12
SAT_BINS: int = 4
#: Below this total saturation weight an image has no usable colour information and the
#: colour gate abstains instead of rejecting. 1e-9 is "numerically zero", not a judgement.
_MIN_COLOUR_WEIGHT: float = 1e-9
_NORM_EPSILON: float = 1e-9

#: Similarity reported for a digest hit. A definition, not a measurement — see the module
#: docstring. Named so a reader can see that 1.0 is not the top of a measured scale.
EXACT_SIMILARITY: float = 1.0

#: DoD for the fixture gate (`docs/PLAN.md` E3-T4: false-merge rate <= 0.05).
FALSE_MERGE_MAX: float = 0.05

#: Which layer produced a decision. Reported by the CLI so an operator can tell a digest hit
#: (free, certain) from a perceptual match (thresholded, could be wrong).
LAYER_EXACT: str = "exact"
LAYER_NEAR: str = "near"


@dataclass(slots=True)
class DedupeResult:
    """Outcome of one `find_duplicate()` call.

    Attributes:
        match_item_id: the candidate string that matched, or `None` when nothing did.
        similarity: the score of the candidate named in `match_item_id`, in `[0, 1]` — `1.0` for
            a digest hit (a definition, not a measurement), otherwise that candidate's structural
            score. **When there is no match it is the closest miss**: the best structural score
            among the candidates that could be measured, so "0.87 against a 0.90 gate" is
            readable. `None` when no candidate was measurable at all; `None` means UNMEASURED and
            must not be read as `0.0`. It never reports one candidate's id next to another
            candidate's number.

    **`similarity == 1.0` is ambiguous and this struct cannot disambiguate it.** It means *either*
    "same bytes" (digest layer, certain) *or* "the structural score happened to top out" (perceptual
    layer, thresholded, and one bad comparison away from being wrong); a perceptual score of
    exactly 1.0 is reachable, and any 4-decimal log line makes the two look identical. The struct
    keeps two fields on purpose — adding a `kind` would change a shape this ticket may not change
    — so **a caller that needs to know which happened must compare `sha256` itself**:

        exact = result.match_item_id is not None and (
            sha256(image) == sha256(Path(result.match_item_id).read_bytes())
        )

    Treating the 1.0 as "definitely the same bytes" without that check is the one reading of this
    field that is not supported.

    A high value with `match_item_id is None` means the colour gate rejected the match — the CLI
    prints the colour column and the layer for that reason.
    """

    match_item_id: str | None = None
    similarity: float | None = None


@dataclass(slots=True)
class _Fingerprint:
    """Everything the perceptual layer keeps about one image. Derived data only, no pixels."""

    grids: tuple[np.ndarray, ...]  # one unit-norm GRID*GRID*3 vector per zoom level
    colour_hist: np.ndarray  # unit-sum (HUE_BINS*SAT_BINS,) histogram, zeros if achromatic


@dataclass(slots=True)
class _Verdict:
    """Decision for one (query, candidate) pair, plus the numbers behind it, for the report."""

    matched: bool
    structure: float | None  # None when the exact layer decided without measuring
    colour: float | None
    layer: str | None  # LAYER_EXACT / LAYER_NEAR / None


def _as_candidate_path(candidate: object) -> str:
    """Normalise one candidate to a `str` path, or raise `TypeError` if it is not a path at all.

    `os.PathLike` is accepted because a `pathlib.Path` is the most natural thing a caller can
    write, and refusing it used to fail *open*: the candidate was skipped with a warning and the
    upload was reported as new. That is the quiet path to a seller owning the same photo twice, so
    a wrong type is now a loud `TypeError` before any candidate is examined — the same standard the
    query bytes get (`ValueError`). A path that merely does not exist is still a data condition,
    not a bug, and is still skipped with a warning.
    """
    try:
        path = os.fspath(candidate)  # type: ignore[arg-type]
    except TypeError:
        raise TypeError(
            "dedupe.find_duplicate(): every candidate must be a str or os.PathLike path, "
            f"got {type(candidate).__name__}: {candidate!r}"
        ) from None
    if isinstance(path, bytes):
        return path.decode("utf-8", "surrogateescape")
    return path


def _digest(data: bytes) -> str:
    """The dedupe key: sha256 of the image bytes, lowercase hex.

    Same shape and same value as `apps/api/requests.py::CreateItemRequest.source_sha256` for
    the item this file came from, on the assumption that ingest stores the uploaded bytes
    unchanged. `hashlib` is stdlib, so this adds no dependency.
    """
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes, what: str) -> Image.Image:
    """Decode image bytes to RGB, or raise `ValueError` naming the culprit."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()  # force the decode: a truncated file fails here, not three lines later
            return opened.convert("RGB")
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises a zoo of types for unreadable bytes
        raise ValueError(f"dedupe(): could not decode {what} ({exc})") from exc


def _working_array(image: Image.Image) -> np.ndarray:
    """Downsample to <= WORKING_SIDE on the long edge and return float64 RGB in `[0, 1]`."""
    working = image.copy()
    working.thumbnail((WORKING_SIDE, WORKING_SIDE), Image.LANCZOS)
    return np.asarray(working, dtype=np.float64) / 255.0


def _grid_vector(array: np.ndarray, zoom: float) -> np.ndarray:
    """Mean-centred, unit-norm GRID x GRID x 3 rendering of the central `1 / zoom` of the frame.

    `zoom > 1` crops the centre and blows it up, which is what re-aligns a photo that was
    uploaded with a border around it; `zoom < 1` cannot add pixels, so the opposite direction
    is covered by the other side of the pair being zoomed instead.
    """
    height, width = array.shape[0], array.shape[1]
    if zoom != 1.0:
        crop_h = max(8, int(round(height / zoom)))
        crop_w = max(8, int(round(width / zoom)))
        top = max(0, (height - crop_h) // 2)
        left = max(0, (width - crop_w) // 2)
        array = array[top : top + crop_h, left : left + crop_w]

    eight_bit = Image.fromarray(np.round(array * 255.0).astype(np.uint8))
    grid = np.asarray(eight_bit.resize((GRID, GRID), Image.BILINEAR), dtype=np.float64) / 255.0
    centred = grid - grid.mean(axis=(0, 1), keepdims=True)  # brightness / white balance cancel
    norm = float(np.sqrt(np.sum(centred * centred)))
    if norm <= _NORM_EPSILON:
        # A uniform frame has no direction. Match nothing rather than everything.
        return np.zeros(GRID * GRID * 3, dtype=np.float64)
    return (centred / norm).reshape(-1)


def _colour_histogram(array: np.ndarray) -> np.ndarray:
    """Saturation-weighted hue x saturation histogram; all zeros when the frame is neutral.

    Both dimensions are filled with **linear interpolation between neighbouring bins** instead
    of hard bin edges. Hard edges make the intersection collapse under a couple of degrees of
    hue rotation — measured on `base/bottom-01.jpg`, a 5 degree global shift took the score
    from 1.00 to 0.35, i.e. the gate would have vetoed an honest re-upload from a phone with a
    slightly different white balance. With soft bins the same 5 degree shift keeps most of the
    overlap while a 60 degree shift (a different colourway) still collapses to ~0.0.
    """
    max_channel = array.max(axis=2)
    min_channel = array.min(axis=2)
    span = max_channel - min_channel
    safe_span = np.where(span > 1e-6, span, 1.0)
    safe_max = np.where(max_channel > 1e-6, max_channel, 1.0)
    saturation = np.where(max_channel > 1e-6, span / safe_max, 0.0)

    red, green, blue = array[..., 0], array[..., 1], array[..., 2]
    hue = np.zeros_like(max_channel)
    is_red = max_channel == red
    hue = np.where(is_red, ((green - blue) / safe_span) % 6.0, hue)
    is_green = (max_channel == green) & ~is_red
    hue = np.where(is_green, (blue - red) / safe_span + 2.0, hue)
    is_blue = (max_channel == blue) & ~is_red & ~is_green
    hue = np.where(is_blue, (red - green) / safe_span + 4.0, hue)
    hue = (hue / 6.0) % 1.0

    weights = (saturation * max_channel).reshape(-1)
    histogram = np.zeros(HUE_BINS * SAT_BINS, dtype=np.float64)

    # Hue is circular, so its interpolation wraps; saturation is not, so it is clamped.
    hue_position = (hue.reshape(-1) * HUE_BINS) % HUE_BINS
    hue_lower = np.floor(hue_position).astype(np.int64)
    hue_frac = hue_position - hue_lower
    sat_position = np.clip(saturation.reshape(-1) * SAT_BINS, 0.0, SAT_BINS - 1e-9)
    sat_lower = np.floor(sat_position).astype(np.int64)
    sat_frac = sat_position - sat_lower
    sat_upper = np.minimum(sat_lower + 1, SAT_BINS - 1)

    for hue_index, hue_weight in (
        (hue_lower, 1.0 - hue_frac),
        ((hue_lower + 1) % HUE_BINS, hue_frac),
    ):
        for sat_index, sat_weight in ((sat_lower, 1.0 - sat_frac), (sat_upper, sat_frac)):
            contribution = weights * hue_weight * sat_weight
            np.add.at(histogram, hue_index * SAT_BINS + sat_index, contribution)

    total = float(histogram.sum())
    if total <= _MIN_COLOUR_WEIGHT:
        return histogram  # achromatic: the caller treats this as "gate abstains"
    return histogram / total


def _fingerprint(data: bytes, what: str) -> _Fingerprint:
    """Build the comparable fingerprint for one encoded image."""
    array = _working_array(_decode(data, what))
    return _Fingerprint(
        grids=tuple(_grid_vector(array, zoom) for zoom in ZOOM_LEVELS),
        colour_hist=_colour_histogram(array),
    )


def _structure_similarity(left: _Fingerprint, right: _Fingerprint) -> float:
    """Best cosine over the symmetric zoom pyramid. Deterministic: fixed order, first max wins."""
    best = -1.0
    for left_grid in left.grids:
        for right_grid in right.grids:
            score = float(np.dot(left_grid, right_grid))
            if score > best:
                best = score
    return float(min(1.0, max(0.0, best)))


def _colour_similarity(left: _Fingerprint, right: _Fingerprint) -> float:
    """Histogram intersection, or 1.0 when either side carries no colour information."""
    if (
        left.colour_hist.sum() <= _MIN_COLOUR_WEIGHT
        or right.colour_hist.sum() <= _MIN_COLOUR_WEIGHT
    ):
        return 1.0  # gate abstains — see the module docstring
    return float(np.minimum(left.colour_hist, right.colour_hist).sum())


def _score_candidate(
    query_digest: str,
    query_fingerprint: _Fingerprint,
    candidate_bytes: bytes,
    candidate_digest: str,
) -> _Verdict:
    """Decision for one candidate, exact layer first. The single place the policy lives.

    `find_duplicate()` and the fixture report both call this, so the printed verdict cannot
    drift from the API's behaviour.
    """
    if candidate_digest == query_digest:
        # Same bytes. No thresholds are consulted, so no threshold change can break this.
        return _Verdict(matched=True, structure=None, colour=None, layer=LAYER_EXACT)

    candidate_fingerprint = _fingerprint(candidate_bytes, "the candidate image")
    structure = _structure_similarity(query_fingerprint, candidate_fingerprint)
    colour = _colour_similarity(query_fingerprint, candidate_fingerprint)
    matched = structure >= STRUCTURE_MIN and colour >= COLOUR_MIN
    return _Verdict(
        matched=matched,
        structure=structure,
        colour=colour,
        layer=LAYER_NEAR if matched else None,
    )


def find_duplicate(user_id: str, image: bytes, candidates: Sequence[str] = ()) -> DedupeResult:
    """Look for `image` among `candidates`; return the match, or `None` fields.

    Two layers over one key: an exact sha256 hit returns immediately with
    `similarity=EXACT_SIMILARITY`; otherwise the perceptual score decides.

    Args:
        user_id: the seller's id. Not used for the comparison — there is no persisted index in
            v1, so candidates come from the caller — but it is stamped on every skip warning so
            a broken worker can be traced to a user without a second query.
        image: encoded bytes of the upload being checked. Empty bytes raise `ValueError`
            immediately. Undecodable bytes raise `ValueError` **only if no candidate matches them
            byte for byte** — the digest layer runs before any decoding, so a re-upload whose
            bytes are identical is still reported as the duplicate it is, even if Pillow cannot
            read it.
        candidates: filesystem paths to the seller's previously stored images (see the module
            docstring), as `str` or `os.PathLike`. A `pathlib.Path` is normalised to its fspath
            string, and that string is what `match_item_id` returns; an `str` comes back verbatim.
            Anything that is not a path at all raises `TypeError` — before any candidate is
            examined, because skipping it would report a real duplicate as a new item. A path that
            cannot be *read* (missing, permissions, not an image) is a data condition, not a bug:
            skipped with a warning, never merged.

    Returns:
        `DedupeResult(match_item_id, similarity)` — see the class docstring for the exact
        meaning of each field, including when `similarity` is `None` and why `1.0` is a
        definition rather than a measurement.
    """
    if not image:
        raise ValueError("dedupe.find_duplicate(): image bytes are empty")

    # Resolve candidate types before any work: a wrong type is a programming error and must be
    # loud, not a skipped candidate that makes the upload look new.
    candidate_paths = [_as_candidate_path(candidate) for candidate in candidates]
    query_digest = _digest(image)

    # ---- step 0: the exact layer, strictly BEFORE any decoding ------------------------------
    # Nothing below this block may run first: an image we cannot decode is still, factually,
    # the same upload if its bytes match. Hashing is cheaper than decoding too, so the common
    # re-upload case never touches Pillow at all.
    unreadable: set[str] = set()
    for candidate in candidate_paths:
        try:
            data = Path(candidate).read_bytes()
        except OSError as exc:
            unreadable.add(candidate)
            logger.warning(
                "dedupe: user=%s candidate %r could not be read (%s) — skipped",
                user_id,
                candidate,
                exc,
            )
            continue
        if _digest(data) == query_digest:
            # Same bytes: definitive, and it beats any perceptual score by construction.
            return DedupeResult(match_item_id=candidate, similarity=EXACT_SIMILARITY)

    # ---- step 1: the perceptual layer ------------------------------------------------------
    # Only now does decoding become unavoidable, and only now can an undecodable upload be an
    # error: there is no digest hit to hide behind.
    query_fingerprint = _fingerprint(image, "the query image")

    best_similarity: float | None = None
    matched_id: str | None = None
    matched_similarity: float | None = None

    for candidate in candidate_paths:
        if candidate in unreadable:
            continue  # already reported, and a file that vanished will not reappear
        try:
            data = Path(candidate).read_bytes()
        except OSError as exc:
            logger.warning(
                "dedupe: user=%s candidate %r could not be read on the second pass (%s) — skipped",
                user_id,
                candidate,
                exc,
            )
            continue

        try:
            verdict = _score_candidate(query_digest, query_fingerprint, data, _digest(data))
        except ValueError as exc:
            logger.warning(
                "dedupe: user=%s candidate %r is not a decodable image (%s) — skipped",
                user_id,
                candidate,
                exc,
            )
            continue

        structure = verdict.structure
        if structure is not None and (best_similarity is None or structure > best_similarity):
            best_similarity = structure

        if verdict.matched:
            # Strict `>` keeps the earliest candidate on a tie: deterministic by construction.
            if matched_similarity is None or (structure or 0.0) > matched_similarity:
                matched_similarity = structure or 0.0
                matched_id = candidate

    # When there is a match, `similarity` is *that candidate's* score, never the best score seen
    # among candidates that were rejected: a caller logging the pair together must not be handed
    # one candidate's id with another candidate's number. Only the no-match case reports the
    # closest miss, because that is the only thing left to report.
    if matched_id is not None:
        return DedupeResult(match_item_id=matched_id, similarity=matched_similarity)
    return DedupeResult(match_item_id=None, similarity=best_similarity)


# --------------------------------------------------------------------------- fixture report
# `python -m packages.vision.dedupe --fixture evals/fixtures/dedupe` is the E3-T4 acceptance
# command: read the fixture's manifest, score every pair, print one line per pair plus counts.
# It never writes a report file — a file would be overwritten by the next run, and an old
# green number is worse than no number.


def _load_manifest(fixture_dir: Path) -> dict[str, object]:
    """Read and lightly validate `manifest.json` from a fixture directory."""
    manifest_path = fixture_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"dedupe: no manifest.json in {fixture_dir}")
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("pairs"), list):
        raise SystemExit(f"dedupe: {manifest_path} has no 'pairs' list")
    return manifest


def _score_pair(
    fixture_dir: Path, pair: dict[str, object], query_bytes: bytes, query_fingerprint: _Fingerprint
) -> tuple[_Verdict, str]:
    """Score one manifest pair through the same policy function the API uses.

    Returns (verdict, candidate_digest). Raises `SystemExit` on a manifest that points at a
    file which is not there: a fixture with a hole in it must not report a number.
    """
    b_path = fixture_dir / str(pair["b_path"])
    if not b_path.is_file():
        raise SystemExit(f"dedupe: manifest points at a missing file: {b_path}")
    candidate_bytes = b_path.read_bytes()
    candidate_digest = _digest(candidate_bytes)
    verdict = _score_candidate(
        _digest(query_bytes), query_fingerprint, candidate_bytes, candidate_digest
    )
    return verdict, candidate_digest


def run_fixture(fixture_dir: Path) -> dict[str, float]:
    """Score every pair in `fixture_dir`, print the table, return the summary numbers."""
    manifest = _load_manifest(fixture_dir)
    raw_pairs = manifest["pairs"]
    assert isinstance(raw_pairs, list)

    n_pairs = 0
    n_distinct = 0
    n_duplicate = 0
    n_exact = 0
    n_near = 0
    n_exact_detected = 0
    n_near_detected = 0
    n_false_merges = 0
    n_digest_decided = 0

    print(
        f"{'pair':10s} {'label':10s} {'kind':6s} {'verdict':10s} {'layer':6s} "
        f"{'structure':>9s} {'colour':>7s}  note"
    )
    for raw_pair in raw_pairs:
        pair = dict(raw_pair)  # type: ignore[arg-type]
        label = str(pair["label"])
        kind = pair.get("kind")
        a_path = fixture_dir / str(pair["a_path"])
        try:
            query_bytes = a_path.read_bytes()
        except OSError as exc:
            raise SystemExit(f"dedupe: cannot read {a_path}: {exc}") from exc
        query_fingerprint = _fingerprint(query_bytes, str(a_path))

        verdict, candidate_digest = _score_pair(fixture_dir, pair, query_bytes, query_fingerprint)
        bytes_identical = candidate_digest == _digest(query_bytes)

        is_false_merge = verdict.matched and label == "distinct"
        is_hit = verdict.matched and label == "duplicate"

        n_pairs += 1
        if label == "distinct":
            n_distinct += 1
        else:
            n_duplicate += 1
            if kind == "exact":
                n_exact += 1
                n_exact_detected += int(is_hit)
            else:
                n_near += 1
                n_near_detected += int(is_hit)
        if is_false_merge:
            n_false_merges += 1
        if verdict.layer == LAYER_EXACT:
            n_digest_decided += 1

        if is_false_merge:
            note = "<<< FALSE MERGE"
        elif is_hit:
            note = "ok"
        elif label == "duplicate":
            note = "missed"
        else:
            note = "ok (distinct)"
        # A manifest that claims "near" but ships identical bytes would leave the perceptual
        # layer untested for that pair; say so in the report instead of hiding it.
        if kind == "near" and bytes_identical:
            note += " <<< manifest says near, bytes are identical"

        structure_text = "exact" if verdict.structure is None else f"{verdict.structure:.4f}"
        colour_text = "-" if verdict.colour is None else f"{verdict.colour:.4f}"
        doc_verdict = "duplicate" if verdict.matched else "distinct"
        print(
            f"{str(pair['id']):10s} {label:10s} {str(kind):6s} {doc_verdict:10s} "
            f"{str(verdict.layer or '-'):6s} {structure_text:>9s} {colour_text:>7s}  {note}"
        )

    # The denominator is the number of *distinct* pairs: a false merge can only happen there,
    # and dividing by all 20 would quietly halve the rate.
    false_merge_rate = float(n_false_merges) / n_distinct if n_distinct else float("nan")
    near_recall = float(n_near_detected) / n_near if n_near else float("nan")
    exact_recall = float(n_exact_detected) / n_exact if n_exact else float("nan")

    print("")
    print(
        f"exact: n={n_exact} detected={n_exact_detected} recall={exact_recall:.2f} "
        f"(a 1.0 here is the layer's definition, not a measured capability)"
    )
    print(
        f"near : n={n_near} detected={n_near_detected} recall={near_recall:.2f} "
        f"(upper bound: positives are same-source transforms)"
    )
    print(
        f"digest_decided={n_digest_decided} thresholds: structure>={STRUCTURE_MIN} "
        f"colour>={COLOUR_MIN} gate: false_merge_rate<={FALSE_MERGE_MAX}"
    )
    print(f"n_pairs={n_pairs} n_distinct={n_distinct} false_merge_rate={false_merge_rate:.3f}")

    return {
        "n_pairs": float(n_pairs),
        "n_distinct": float(n_distinct),
        "n_duplicate": float(n_duplicate),
        "n_exact": float(n_exact),
        "n_near": float(n_near),
        "n_false_merges": float(n_false_merges),
        "false_merge_rate": false_merge_rate,
        "near_recall": near_recall,
        "exact_recall": exact_recall,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Acceptance entry point for E3-T4. Reports; it does not gate the exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m packages.vision.dedupe",
        description="Score the dedupe fixture and print the false-merge rate (PLAN E3-T4).",
    )
    parser.add_argument(
        "--fixture",
        default="evals/fixtures/dedupe",
        help="fixture directory containing manifest.json (default: evals/fixtures/dedupe)",
    )
    args = parser.parse_args(argv)

    fixture_dir = Path(args.fixture)
    if not fixture_dir.is_dir():
        print(f"dedupe: fixture directory not found: {fixture_dir}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    summary = run_fixture(fixture_dir)
    if summary["n_distinct"] and summary["false_merge_rate"] > FALSE_MERGE_MAX:
        print(
            f"GATE FAILED: false_merge_rate={summary['false_merge_rate']:.3f} > {FALSE_MERGE_MAX}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
