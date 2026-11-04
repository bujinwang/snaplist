"""Tests for E3-T4 `find_duplicate()` (packages/vision/dedupe.py) and its 20-pair fixture.

`find_duplicate()` has **two layers over one key** — the key is `source_sha256`, which
`apps/api/requests.py::CreateItemRequest` has carried since before this stage existed. The DoD
lines and how each is proven:

* "false-merge rate on the 20-pair fixture <= 0.05" -> test_fixture_false_merge_rate_is_under_gate
  scores every pair through the public function and asserts the rate, printing `n_pairs`,
  `n_distinct`, `n_exact` and `n_near` so the denominators are never implied.
* "a false merge is the worse error"            -> test_hard_negative_breeches_are_not_merged
  plus the colour-veto test: both fail if the threshold or a gate is loosened, which is the
  mutation a reviewer should try.
* "the exact layer does not depend on the perceptual thresholds" -> the monkeypatch test below:
  with both thresholds pushed out of reach, byte-identical pairs must still match. Fixtures: the
  three `kind: exact` pairs.
* "the near layer actually has work to do"       -> test_near_pairs_are_not_byte_identical, which
  would catch a fixture that shipped identical bytes and labelled them `near`.
* "zero network calls"                          -> an autouse socket trap on every test.
* `similarity=None` means UNMEASURED            -> test_no_candidates_is_unmeasured_not_zero
  and test_only_corrupt_candidates_are_unmeasured_not_zero.
* "candidates are paths" is stated, not guessed -> test_missing_candidate_is_skipped... pins
  the exact behaviour the docstring promises for a candidate that cannot be read.

The fixture pairs are real licensed photos on disk (`evals/fixtures/dedupe/**`); the synthetic
images below are test scaffolding and never touch `evals/golden/`, which E0-T1 reserves for
real photographs.
"""

from __future__ import annotations

import hashlib
import io
import json
import socket
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageOps

import packages.vision.dedupe as dedupe_module
from packages.vision.dedupe import (
    COLOUR_MIN,
    EXACT_SIMILARITY,
    FALSE_MERGE_MAX,
    STRUCTURE_MIN,
    DedupeResult,
    find_duplicate,
    run_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "evals" / "fixtures" / "dedupe"
MANIFEST_PATH = FIXTURE_DIR / "manifest.json"
MAX_FIXTURE_BYTES = 6_000_000


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """find_duplicate() must never touch the network: no weights, no embedding API, nothing."""

    def _trap(*args: object, **kwargs: object) -> None:
        raise AssertionError("find_duplicate() attempted a network call")

    monkeypatch.setattr(socket, "socket", _trap)
    monkeypatch.setattr(socket, "create_connection", _trap)
    monkeypatch.setattr(socket, "getaddrinfo", _trap)
    yield


# --------------------------------------------------------------------------- helpers


def manifest() -> dict[str, object]:
    loaded = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def manifest_pairs() -> list[dict[str, object]]:
    pairs = manifest()["pairs"]
    assert isinstance(pairs, list)
    return [dict(pair) for pair in pairs]  # type: ignore[arg-type]


def fixture_pair(pair_id: str) -> tuple[Path, Path, dict[str, object]]:
    """(a_path, b_path, manifest entry) for one pair, by id."""
    for pair in manifest_pairs():
        if pair["id"] == pair_id:
            return (
                FIXTURE_DIR / str(pair["a_path"]),
                FIXTURE_DIR / str(pair["b_path"]),
                pair,
            )
    raise AssertionError(f"no pair {pair_id!r} in {MANIFEST_PATH}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def png_bytes(array: np.ndarray) -> bytes:
    """Encode an HxWx3 uint8 array as PNG bytes."""
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(image: Image.Image, quality: int = 85) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def png_with_compression(image: Image.Image, level: int) -> bytes:
    """PNG bytes at a given zlib level: identical pixels, different bytes."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=level)
    return buffer.getvalue()


def open_rgb(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        return opened.convert("RGB")


def write_bytes(tmp_path: Path, name: str, data: bytes) -> Path:
    target = tmp_path / name
    target.write_bytes(data)
    return target


def hue_shift(image: Image.Image, degrees: float) -> Image.Image:
    """Rotate the hue of every pixel by `degrees`. Structure untouched, chroma replaced.

    60 degrees is chosen from the ladder recorded in the module docstring: at that angle the
    structural score is still ~0.987 (the frames align perfectly) while the colour score has
    collapsed to ~0.006, so this test can only pass if the colour gate is doing its job.
    """
    array = np.asarray(image.convert("HSV")).astype(np.uint8).copy()
    array[..., 0] = (array[..., 0].astype(np.int16) + int(round(degrees / 360.0 * 255.0))) % 256
    channels = [Image.fromarray(np.ascontiguousarray(array[..., index])) for index in range(3)]
    return Image.merge("HSV", channels).convert("RGB")


# --------------------------------------------------------------------------- fixture gate


def test_fixture_false_merge_rate_is_under_gate(capsys: pytest.CaptureFixture[str]) -> None:
    """The gate itself: <= 0.05 false merges over the distinct pairs, with every count printed."""
    assert MANIFEST_PATH.is_file(), f"fixture is missing: {MANIFEST_PATH}"

    summary = run_fixture(FIXTURE_DIR)
    printed = capsys.readouterr().out

    n_pairs = int(summary["n_pairs"])
    n_distinct = int(summary["n_distinct"])
    n_exact = int(summary["n_exact"])
    n_near = int(summary["n_near"])
    print(
        f"fixture: n_pairs={n_pairs} n_distinct={n_distinct} n_exact={n_exact} n_near={n_near} "
        f"false_merges={int(summary['n_false_merges'])} "
        f"false_merge_rate={summary['false_merge_rate']:.3f} "
        f"exact_recall={summary['exact_recall']:.2f} near_recall={summary['near_recall']:.2f}"
    )

    assert n_pairs == 20, f"the ticket fixes the fixture at 20 pairs, found {n_pairs}"
    assert n_distinct >= 10, f"a denominator of {n_distinct} distinct pairs measures nothing"
    assert n_exact >= 1 and n_near >= 1, (
        "both layers need positives: an exact-only fixture never tests the threshold, a "
        "near-only fixture never tests the digest"
    )
    assert summary["false_merge_rate"] <= FALSE_MERGE_MAX, (
        f"false_merge_rate={summary['false_merge_rate']:.3f} > {FALSE_MERGE_MAX}; "
        f"per-pair table:\n{printed}"
    )
    # The summary line the acceptance command must end with, proven to be really printed.
    assert f"n_pairs={n_pairs} n_distinct={n_distinct} false_merge_rate=" in printed
    assert "manifest says near, bytes are identical" not in printed

    # NOT asserted here, on purpose: `exact_recall` and the `layer` column. They describe *which
    # mechanism answered*, not whether the answer was right, and this fixture cannot tell those
    # apart — with the digest layer deleted the perceptual layer still matches byte-identical
    # files at 1.0 (mutation M7 in the handoff). Asserting the label here would let a gate go red
    # for a reporting change while a real regression stayed green. The label has its own test,
    # test_report_attributes_exact_pairs_to_the_digest_layer.


def test_fixture_manifest_is_internally_consistent() -> None:
    """Every pair names two files that exist, a valid label/kind, and a reason for that label."""
    pairs = manifest_pairs()
    labels = [pair["label"] for pair in pairs]

    assert len(pairs) == 20
    assert labels.count("distinct") >= 10
    assert labels.count("duplicate") == 20 - labels.count("distinct")
    assert len({pair["id"] for pair in pairs}) == len(pairs), "duplicate pair ids"

    for pair in pairs:
        assert pair["label"] in ("duplicate", "distinct")
        assert (FIXTURE_DIR / str(pair["a_path"])).is_file(), pair["a_path"]
        assert (FIXTURE_DIR / str(pair["b_path"])).is_file(), pair["b_path"]
        # "why" is the whole point of a hard-negative set: a pair without a reason is a guess.
        assert len(str(pair["why"])) > 40, f"{pair['id']} has no real justification"
        assert str(pair["source"]).startswith("evals/golden-provisional/")
        assert pair["licence"]

    for pair in pairs:
        if pair["label"] == "distinct":
            # Two different physical objects: two different source photos, no transform.
            assert pair["a_path"] != pair["b_path"], f"{pair['id']} is not two images"
            assert pair["transform"] is None, "a distinct pair must not be a transform"
            assert pair["kind"] is None, "a distinct pair has no duplicate kind"
        else:
            assert pair["kind"] in ("exact", "near"), f"{pair['id']} has no kind"


def test_fixture_files_respect_the_size_budget() -> None:
    """<= 6 MB in total and <= 800 px on the long edge, so the fixture can live in the repo."""
    images = sorted(FIXTURE_DIR.glob("base/*.jpg"))
    images += sorted(FIXTURE_DIR.glob("exact/*.jpg"))
    images += sorted(FIXTURE_DIR.glob("variant/*.jpg"))
    assert images, "no fixture images found"

    total_bytes = sum(path.stat().st_size for path in images)
    assert total_bytes <= MAX_FIXTURE_BYTES, (
        f"fixture is {total_bytes / 1e6:.2f} MB, budget is {MAX_FIXTURE_BYTES / 1e6:.0f} MB"
    )

    oversized = [path.name for path in images if max(open_rgb(path).size) > 800]
    assert not oversized, f"long edge over 800 px: {oversized}"

    # No stale debris from an earlier recipe table: every image on disk is claimed by the
    # manifest, so the size budget measures the fixture and not the leftovers.
    claimed = {str(pair[key]) for pair in manifest_pairs() for key in ("a_path", "b_path")}
    unclaimed = [
        str(path.relative_to(FIXTURE_DIR)) for path in images
        if str(path.relative_to(FIXTURE_DIR)) not in claimed
    ]
    assert not unclaimed, f"images in the fixture directory that no pair uses: {unclaimed}"


# --------------------------------------------------------------------------- exact layer


def test_exact_pairs_are_byte_identical_and_matched_by_digest() -> None:
    """The exact layer: same sha256 -> similarity 1.0, no threshold consulted."""
    exact_pairs = [pair for pair in manifest_pairs() if pair["kind"] == "exact"]
    assert len(exact_pairs) >= 1

    for pair in exact_pairs:
        a_path = FIXTURE_DIR / str(pair["a_path"])
        b_path = FIXTURE_DIR / str(pair["b_path"])
        assert sha256(a_path) == sha256(b_path), f"{pair['id']} is not byte-identical"

        result = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)])
        assert result.match_item_id == str(b_path), pair["id"]
        assert result.similarity == EXACT_SIMILARITY, pair["id"]


def test_near_pairs_are_not_byte_identical() -> None:
    """A `near` pair that shipped identical bytes would leave the threshold untested."""
    near_pairs = [pair for pair in manifest_pairs() if pair["kind"] == "near"]
    assert len(near_pairs) >= 1

    for pair in near_pairs:
        a_path = FIXTURE_DIR / str(pair["a_path"])
        b_path = FIXTURE_DIR / str(pair["b_path"])
        assert sha256(a_path) != sha256(b_path), (
            f"{pair['id']} claims kind=near but is byte-identical: the perceptual layer is "
            "not being tested by this pair"
        )
        assert pair["transform"], f"{pair['id']} is near but says nothing about the transform"


def test_exact_layer_does_not_depend_on_the_perceptual_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Push the perceptual thresholds out of reach: exact pairs must still match, near must not.

    This is the property that makes the exact layer worth having — it is a digest comparison,
    so no amount of threshold tuning can make it either unsafe or blind.
    """
    monkeypatch.setattr(dedupe_module, "STRUCTURE_MIN", 1.01)
    monkeypatch.setattr(dedupe_module, "COLOUR_MIN", 1.01)

    exact_pairs = [pair for pair in manifest_pairs() if pair["kind"] == "exact"]
    for pair in exact_pairs:
        a_path = FIXTURE_DIR / str(pair["a_path"])
        b_path = FIXTURE_DIR / str(pair["b_path"])
        result = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)])
        assert result.match_item_id == str(b_path), (
            f"{pair['id']}: the exact layer stopped working when a perceptual threshold moved"
        )
        assert result.similarity == EXACT_SIMILARITY

    # The monkeypatch must actually be live, or this test proves nothing about the exact layer.
    near_a, near_b, _ = fixture_pair("near-02")
    assert find_duplicate("seller-1", near_a.read_bytes(), [str(near_b)]).match_item_id is None


def test_exact_match_wins_over_a_perceptual_match() -> None:
    """Identical bytes are stronger evidence than a passing score, wherever it sits in the list."""
    near_a, near_b, _ = fixture_pair("near-01")
    identical = near_a  # base/bottom-01.jpg is byte-identical to the query

    # Test premise: the first candidate really does pass the perceptual gates on its own.
    assert find_duplicate("seller-1", near_a.read_bytes(), [str(near_b)]).match_item_id is not None

    result = find_duplicate("seller-1", near_a.read_bytes(), [str(near_b), str(identical)])

    assert result.match_item_id == str(identical)
    assert result.similarity == EXACT_SIMILARITY


def test_report_attributes_exact_pairs_to_the_digest_layer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The report's `layer` column is reporting, not gating — and it is checked here, alone.

    A label assertion in the gate test would let a reporting regression redden the gate while the
    file it guards stayed broken. This test owns the label; `test_fixture_false_merge_rate_…` owns
    detection.
    """
    run_fixture(FIXTURE_DIR)
    printed = capsys.readouterr().out

    for pair in manifest_pairs():
        if pair["kind"] != "exact":
            continue
        line = next(row for row in printed.splitlines() if row.startswith(str(pair["id"])))
        columns = line.split()
        assert columns[4] == "exact", f"{pair['id']} should be decided by the digest layer: {line}"
        assert columns[5] == "exact", f"{pair['id']} should report no structural score: {line}"


def test_report_verdicts_agree_with_find_duplicate() -> None:
    """The printed report must not drift from what the API returns — both call one policy."""
    expected_false_merges = 0
    expected_near_hits = 0
    expected_exact_hits = 0

    for pair in manifest_pairs():
        a_path = FIXTURE_DIR / str(pair["a_path"])
        b_path = FIXTURE_DIR / str(pair["b_path"])
        matched = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)]).match_item_id
        if pair["label"] == "distinct":
            expected_false_merges += int(matched is not None)
        elif pair["kind"] == "exact":
            expected_exact_hits += int(matched is not None)
        else:
            expected_near_hits += int(matched is not None)

    summary = run_fixture(FIXTURE_DIR)
    assert int(summary["n_false_merges"]) == expected_false_merges
    assert summary["exact_recall"] == pytest.approx(expected_exact_hits / summary["n_exact"])
    assert summary["near_recall"] == pytest.approx(expected_near_hits / summary["n_near"])


# --------------------------------------------------------------------------- near layer


def test_same_source_transform_is_a_duplicate() -> None:
    """A padded copy of one photo is recognised as that photo — by the perceptual layer."""
    a_path, b_path, pair = fixture_pair("near-02")  # white border added
    assert pair["kind"] == "near"

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)])

    assert result.match_item_id == str(b_path)
    assert result.similarity is not None
    assert result.similarity >= STRUCTURE_MIN


def test_hard_negative_breeches_are_not_merged() -> None:
    """Two different pairs of tan breeches from the same series must never become one item."""
    a_path, b_path, pair = fixture_pair("dist-01")
    assert pair["label"] == "distinct"

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)])

    assert result.match_item_id is None, (
        "false merge: two different pairs of breeches collapsed into one listing"
    )
    # The score is still measured and reported — a None match is not a None measurement.
    assert result.similarity is not None
    assert result.similarity < STRUCTURE_MIN


def test_colour_veto_blocks_a_same_frame_different_colourway(tmp_path: Path) -> None:
    """Structure is identical (it is the same frame); the colour gate is the only thing left.

    This is the test that kills the "drop the colour constraint" mutation, and it is the
    reason the constraint exists: a seller uploading the same cut in two colourways is listing
    two items, and merging them deletes one of them.

    **The assertion below is about behaviour only — it never mentions `COLOUR_MIN`.** An earlier
    version ended with `assert COLOUR_MIN > 0.0`, which meant the mutation "set COLOUR_MIN to 0.0"
    turned this test red by definition rather than by observing a bad merge: a self-check wearing
    a test's clothes. The constant check now lives in `test_the_gates_are_not_disabled`, separately,
    so this test can only fail if the module actually merges two colourways.
    """
    a_path, _, _ = fixture_pair("near-01")
    shifted = hue_shift(open_rgb(a_path), 60.0)
    recoloured = write_bytes(tmp_path, "recoloured.jpg", jpeg_bytes(shifted))

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(recoloured)])

    # Premise, and the whole difficulty of the case: the structure score alone WOULD merge these.
    assert result.similarity is not None
    assert result.similarity >= STRUCTURE_MIN, "test premise: the frames must still align"
    # The behaviour: two colourways must not become one item.
    assert result.match_item_id is None, "colour gate did not fire on a hue-shifted copy"


def test_the_gates_are_not_disabled() -> None:
    """A self-check on the constants, kept separate so it cannot inflate a mutation's kill count.

    It is legitimate to assert these, but a mutation that sets a threshold to a disabling value
    will always trip this test regardless of what the code does — so it must not be the only reason
    a mutation is considered caught.
    """
    assert 0.0 < COLOUR_MIN <= 1.0
    assert 0.0 < STRUCTURE_MIN <= 1.0


def test_small_colour_cast_does_not_block_a_real_duplicate(tmp_path: Path) -> None:
    """A different phone / white balance rotates the hue a few degrees. This must still match.

    This is the test that keeps `COLOUR_MIN` from being a silent recall killer: with hard
    histogram bins a 5 degree shift scored 0.347 and the gate vetoed an honest re-upload.
    """
    a_path, _, _ = fixture_pair("near-01")
    drifted = write_bytes(tmp_path, "drifted.jpg", jpeg_bytes(hue_shift(open_rgb(a_path), 5.0), 88))

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(drifted)])

    assert result.match_item_id == str(drifted), (
        "a 5 degree colour cast is normal between phones; vetoing it loses real duplicates"
    )


# --------------------------------------------------------------------------- failure paths


def test_pathlike_candidates_are_accepted() -> None:
    """A `pathlib.Path` is the most natural way to pass a path, and it must not fail open.

    Before this, a Path candidate was skipped with a warning and the upload was reported as new —
    a real duplicate silently turned into a second item, which is the exact failure this stage
    exists to prevent.
    """
    a_path, b_path, _ = fixture_pair("near-02")

    result = find_duplicate("seller-1", a_path.read_bytes(), [b_path])  # Path, not str

    assert result.match_item_id == str(b_path), "a Path candidate must be used, not skipped"


def test_non_path_candidate_raises_type_error() -> None:
    """A candidate that is not a path at all is a caller bug: loud, before any work."""
    a_path, _, _ = fixture_pair("near-01")

    with pytest.raises(TypeError, match="str or os.PathLike"):
        find_duplicate("seller-1", a_path.read_bytes(), [42])  # type: ignore[list-item]


def test_similarity_always_describes_the_matched_candidate(tmp_path: Path) -> None:
    """`similarity` must belong to the candidate in `match_item_id`, not to a rejected one.

    Constructed so the two differ, and the wrong answer is the tempting one: a hue-rotated copy
    scores **higher** on structure (0.9869) but the colour gate rejects it, while a padded copy
    scores 0.9850 and is accepted. A "best score seen" implementation returns the padded path with
    the rotated copy's number.
    """
    a_path, _, _ = fixture_pair("near-01")
    original = open_rgb(a_path)
    rotated = jpeg_bytes(hue_shift(original, 60.0), 88)
    rejected = write_bytes(tmp_path, "rejected.jpg", rotated)
    pad = round(0.12 * max(original.size))
    padded = ImageOps.expand(original, border=pad, fill=(255, 255, 255))
    accepted = write_bytes(tmp_path, "accepted.jpg", jpeg_bytes(padded, 88))

    alone_rejected = find_duplicate("seller-1", a_path.read_bytes(), [str(rejected)])
    alone_accepted = find_duplicate("seller-1", a_path.read_bytes(), [str(accepted)])
    assert alone_rejected.match_item_id is None, "test premise: the rotated copy must be rejected"
    assert alone_accepted.match_item_id == str(accepted), "test premise: the plain copy must match"
    assert (alone_rejected.similarity or 0.0) > (alone_accepted.similarity or 0.0), (
        "test premise: the rejected candidate must score HIGHER, or this test proves nothing"
    )

    both = find_duplicate("seller-1", a_path.read_bytes(), [str(rejected), str(accepted)])

    assert both.match_item_id == str(accepted)
    assert both.similarity == alone_accepted.similarity, (
        "the reported score came from the rejected candidate, not from the one that was returned"
    )


def test_missing_candidate_is_skipped_and_does_not_merge(tmp_path: Path) -> None:
    """A candidate path that does not exist is skipped, and cannot produce a merge."""
    a_path, _, _ = fixture_pair("near-01")
    ghost = tmp_path / "no-such-file.jpg"

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(ghost)])

    assert isinstance(result, DedupeResult)
    assert result.match_item_id is None
    assert result.similarity is None, "nothing was measurable, so the score is UNMEASURED"


def test_corrupt_candidate_is_skipped_but_readable_neighbours_still_count(tmp_path: Path) -> None:
    """A truncated/garbage file is skipped; the good candidate in the same call still matches."""
    a_path, b_path, _ = fixture_pair("near-01")
    garbage = write_bytes(tmp_path, "garbage.jpg", b"\xff\xd8\xff\xe0not-really-a-jpeg")
    truncated = write_bytes(tmp_path, "truncated.jpg", b_path.read_bytes()[:60])

    # Garbage first, so a wrong implementation that aborts the loop would report no match.
    candidates = [str(garbage), str(truncated), str(b_path)]
    result = find_duplicate("seller-1", a_path.read_bytes(), candidates)

    assert result.match_item_id == str(b_path)
    assert result.similarity is not None and result.similarity >= STRUCTURE_MIN


def test_only_corrupt_candidates_are_unmeasured_not_zero(tmp_path: Path) -> None:
    """`similarity=None` means UNMEASURED. 0.0 would be a lie with a number attached."""
    a_path, _, _ = fixture_pair("near-01")
    garbage = write_bytes(tmp_path, "garbage.bin", b"not an image at all")

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(garbage)])

    assert result.match_item_id is None
    # `is None`, not `== 0.0`: UNMEASURED has to be distinguishable from "measured, scored zero",
    # and that is the whole point of the field being Optional.
    assert result.similarity is None


def test_no_candidates_is_unmeasured_not_zero() -> None:
    """The worker may call with an empty candidate list on a seller's first item."""
    a_path, _, _ = fixture_pair("near-01")

    result = find_duplicate("seller-1", a_path.read_bytes())

    assert result == DedupeResult(match_item_id=None, similarity=None)
    assert result.similarity is None


def test_empty_query_bytes_raise() -> None:
    """An empty upload is caller error, not "no duplicate" — it must not be swallowed."""
    with pytest.raises(ValueError, match="empty"):
        find_duplicate("seller-1", b"", ["whatever.jpg"])


def test_undecodable_query_bytes_raise() -> None:
    with pytest.raises(ValueError, match="decodable|decode"):
        find_duplicate("seller-1", b"definitely not an image", [])


def test_undecodable_query_raises_when_no_candidate_matches() -> None:
    """Undecodable upload + no digest hit = caller error, not "no duplicate"."""
    with pytest.raises(ValueError):
        find_duplicate("seller-1", b"\x00\x01\x02 not an image", [])


def test_undecodable_query_is_still_exact_matched_by_its_digest(tmp_path: Path) -> None:
    """The digest layer runs BEFORE any decoding, so a decode failure cannot lose a byte match.

    This is the ordering requirement: "same bytes" is a fact about bytes, not about pixels. A
    test that moved the digest check behind the decode would report this as a broken upload.
    """
    garbage = b"\x00\x01\x02 not an image at all"
    stored = write_bytes(tmp_path, "stored.bin", garbage)

    result = find_duplicate("seller-1", garbage, [str(stored)])

    assert result.match_item_id == str(stored)
    assert result.similarity == EXACT_SIMILARITY


# --------------------------------------------------------------------------- properties


def test_result_is_deterministic_across_repeated_calls() -> None:
    """Same bytes twice -> byte-identical fields. No set/dict order, no randomness."""
    a_path, b_path, _ = fixture_pair("near-05")

    first = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)])
    second = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)])

    assert first.similarity == second.similarity
    assert first.match_item_id == second.match_item_id


def test_similarity_is_symmetric() -> None:
    """Swapping the query and the candidate cannot change the decision or the score."""
    a_path, b_path, _ = fixture_pair("near-03")

    forward = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path)])
    backward = find_duplicate("seller-1", b_path.read_bytes(), [str(a_path)])

    assert forward.similarity == pytest.approx(backward.similarity, abs=1e-9)
    assert (forward.match_item_id is None) == (backward.match_item_id is None)


def test_best_candidate_wins() -> None:
    """A candidate that fails the gates cannot hide one that passes, whatever the ordering."""
    a_path, b_path, _ = fixture_pair("near-01")
    other = fixture_pair("dist-01")[1]

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(other), str(b_path)])
    assert result.match_item_id == str(b_path)

    # And with the winner first, it still wins.
    reversed_order = find_duplicate("seller-1", a_path.read_bytes(), [str(b_path), str(other)])
    assert reversed_order.match_item_id == str(b_path)


def test_ties_keep_the_earliest_candidate(tmp_path: Path) -> None:
    """A *real* tie, two different paths, same score — the earliest must win.

    Passing the same path twice would be a tautology: any implementation returns that path. These
    two files hold identical pixels but different bytes (different PNG compression levels), so the
    exact layer cannot short-circuit and both candidates score exactly the same number. An
    implementation that resolved ties to the *last* candidate fails this and nothing else does.
    """
    a_path, _, _ = fixture_pair("near-01")
    image = open_rgb(a_path)
    first = write_bytes(tmp_path, "tie-first.png", png_with_compression(image, 1))
    second = write_bytes(tmp_path, "tie-second.png", png_with_compression(image, 9))

    assert first != second
    assert sha256(first) != sha256(second), "identical bytes would let the digest layer answer"
    assert Image.open(first).tobytes() == Image.open(second).tobytes(), "pixels must be identical"

    # Each one matches on its own, so the pair really is a tie rather than two rejections.
    alone = find_duplicate("seller-1", a_path.read_bytes(), [str(first)])
    assert alone.match_item_id == str(first)

    tied = find_duplicate("seller-1", a_path.read_bytes(), [str(first), str(second)])
    assert tied.match_item_id == str(first), "a tie must keep the earliest candidate"

    swapped = find_duplicate("seller-1", a_path.read_bytes(), [str(second), str(first)])
    assert swapped.match_item_id == str(second), "the rule must follow the input order, not a hash"


def test_identical_flat_frame_is_an_exact_match(tmp_path: Path) -> None:
    """A flat, textureless upload (care label, plain backdrop) is deduped by its bytes.

    The perceptual layer cannot do this: a flat frame has no structural direction, so it reports
    "no match" against anything. Without the digest layer this upload would never be deduped at
    all, which is exactly the hole the exact layer exists to close.
    """
    flat = np.full((240, 320, 3), 255, dtype=np.uint8)
    flat_path = write_bytes(tmp_path, "flat.png", png_bytes(flat))

    result = find_duplicate("seller-1", png_bytes(flat), [str(flat_path)])

    assert result.match_item_id == str(flat_path)
    assert result.similarity == EXACT_SIMILARITY


def test_flat_frame_with_one_small_mark_is_not_a_match(tmp_path: Path) -> None:
    """Nearly identical to the eye, different bytes: must NOT merge.

    This is the test that says the exact layer is not a similarity heuristic in disguise. It
    also keeps the perceptual layer honest — two flat frames have no direction to compare, so
    it must answer "no" rather than falling open.
    """
    flat = np.full((240, 320, 3), 255, dtype=np.uint8)
    marked = flat.copy()
    marked[100:120, 140:180] = 32  # a small dark mark: different bytes, near-identical image
    marked_path = write_bytes(tmp_path, "marked.png", png_bytes(marked))

    result = find_duplicate("seller-1", png_bytes(flat), [str(marked_path)])

    assert result.match_item_id is None, "a near-identical flat frame was merged with the original"
    assert result.similarity is not None, "it was measured; the answer is just no"


def test_achromatic_frames_are_decided_on_structure_alone(tmp_path: Path) -> None:
    """A greyscale duplicate has no chroma to compare; the colour gate must abstain, not veto."""
    a_path, _, _ = fixture_pair("near-01")
    grey = open_rgb(a_path).convert("L").convert("RGB")
    grey_path = write_bytes(tmp_path, "grey.jpg", jpeg_bytes(grey, quality=88))

    result = find_duplicate("seller-1", a_path.read_bytes(), [str(grey_path)])

    assert result.match_item_id == str(grey_path), (
        "the colour gate rejected a greyscale copy instead of abstaining"
    )
