"""Tests for `evals/metrics/prettify_rubric.py` — the human-rubric validator.

The rule this file defends: **a missing score is never a pass and never a zero.** Every path that
could turn "nobody has scored this yet" into a number must raise instead, naming what is missing.
That is the whole point of the ticket — the row is `UNMEASURED` until a human writes five
integers, and the harness must not be able to invent them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.metrics import prettify_rubric

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "prettify"
MANIFEST = FIXTURE_DIR / "manifest.json"
TEMPLATE = FIXTURE_DIR / "rubric_scores.json"

LOGO_IDS = [
    "logo-01-tee-chest",
    "logo-02-breeches-waistband",
    "logo-03-sneaker-side",
    "logo-04-belt-stamp",
    "logo-05-jacket-emblem",
]


def write_scores(
    tmp_path: Path,
    *,
    scores: dict[str, object],
    rubric_version: str = "v1.0",
    scorer: str = "test-scorer",
    scored_at: str = "2026-11-13T00:00:00Z",
) -> Path:
    """Write a scores file. `scores` maps case id to whatever the test wants stored."""
    payload = {
        "rubric_version": rubric_version,
        "scorer": scorer,
        "scored_at": scored_at,
        "scores": [{"id": case_id, "score": value} for case_id, value in scores.items()],
    }
    path = tmp_path / "rubric_scores.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def all_scored(value: object) -> dict[str, object]:
    return {case_id: value for case_id in LOGO_IDS}


# ------------------------------------------------------------------ the set of scored cases
def test_the_scored_cases_are_exactly_the_five_logo_fixtures() -> None:
    """Crease cases cannot hallucinate text, so scoring them would flatter the mean."""
    cases = prettify_rubric.scored_cases(MANIFEST)
    assert [case["id"] for case in cases] == LOGO_IDS
    assert all(case["protect_box"] for case in cases), "logo cases carry printed text"
    assert [case["id"] for case in cases if case["id"].startswith("crease")] == []


def test_a_complete_file_yields_the_scores_in_manifest_order(tmp_path) -> None:
    path = write_scores(tmp_path, scores=all_scored(4))
    rows, meta = prettify_rubric.run(path, MANIFEST)
    assert [row["id"] for row in rows] == LOGO_IDS
    assert [row["score"] for row in rows] == [4, 4, 4, 4, 4]
    assert meta["scorer"] == "test-scorer"
    assert meta["n"] == 5


# ------------------------------------------------------------------------ missing / malformed
def test_the_committed_template_is_unmeasured_and_names_every_missing_id() -> None:
    """The shipped state: five nulls. The reason must say WHICH ids, and how many."""
    with pytest.raises(ValueError) as caught:
        prettify_rubric.run(TEMPLATE, MANIFEST)
    message = str(caught.value)
    assert "5 of 5" in message, message
    for case_id in LOGO_IDS:
        assert case_id in message, (case_id, message)


def test_one_missing_case_is_named_not_rounded_away(tmp_path) -> None:
    partial = all_scored(4)
    del partial["logo-03-sneaker-side"]
    path = write_scores(tmp_path, scores=partial)
    with pytest.raises(ValueError) as caught:
        prettify_rubric.run(path, MANIFEST)
    message = str(caught.value)
    assert "1 of 5" in message, message
    assert "logo-03-sneaker-side" in message
    assert "logo-01-tee-chest" not in message, "a scored case must not be listed as missing"


def test_a_null_score_counts_as_missing_even_when_the_row_exists(tmp_path) -> None:
    """`score: null` is the template's state — a present key with no judgement in it."""
    scores = all_scored(4)
    scores["logo-04-belt-stamp"] = None
    path = write_scores(tmp_path, scores=scores)
    with pytest.raises(ValueError, match="1 of 5"):
        prettify_rubric.run(path, MANIFEST)


def test_an_id_that_is_not_a_scored_case_is_rejected(tmp_path) -> None:
    """A crease case cannot be scored here, and an unknown id is not silently ignored."""
    scores = all_scored(4)
    scores["crease-01-breeches-leg"] = 5
    path = write_scores(tmp_path, scores=scores)
    with pytest.raises(ValueError) as caught:
        prettify_rubric.run(path, MANIFEST)
    assert "crease-01-breeches-leg" in str(caught.value)
    assert "not scored cases" in str(caught.value)


def test_a_score_outside_one_to_five_is_rejected(tmp_path) -> None:
    for value in (0, 6, -1):
        path = write_scores(tmp_path, scores={**all_scored(4), "logo-01-tee-chest": value})
        with pytest.raises(ValueError, match="outside the 1-5 scale"):
            prettify_rubric.run(path, MANIFEST)


def test_a_non_integer_score_is_rejected(tmp_path) -> None:
    """A half mark, a string digit and a bool are all refused: this scale is 1..5 integers."""
    for value in (4.5, "4", True):
        path = write_scores(tmp_path, scores={**all_scored(4), "logo-02-breeches-waistband": value})
        with pytest.raises(ValueError, match="not an integer 1-5"):
            prettify_rubric.run(path, MANIFEST)


def test_complete_scores_without_provenance_are_rejected(tmp_path) -> None:
    """A number with no scorer and no date is a number without a source."""
    for field in prettify_rubric.PROVENANCE_FIELDS:
        kwargs = {"rubric_version": "v1.0", "scorer": "someone", "scored_at": "2026-11-13"}
        kwargs[field] = ""
        path = write_scores(tmp_path, scores=all_scored(4), **kwargs)
        with pytest.raises(ValueError) as caught:
            prettify_rubric.run(path, MANIFEST)
        message = str(caught.value)
        assert field in message, (field, message)
        assert "not attributable" in message


def test_a_missing_scores_file_is_named(tmp_path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        prettify_rubric.run(tmp_path / "nope.json", MANIFEST)


def test_invalid_json_is_named(tmp_path) -> None:
    path = tmp_path / "rubric_scores.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        prettify_rubric.run(path, MANIFEST)


# -------------------------------------------------------------------------------- the gate
def test_full_marks_pass_with_a_mean_of_five(tmp_path) -> None:
    path = write_scores(tmp_path, scores=all_scored(5))
    rows, _meta = prettify_rubric.run(path, MANIFEST)
    stats = prettify_rubric.summarise(rows)
    assert stats["mean"] == pytest.approx(5.0)
    assert stats["n"] == 5
    assert stats["passes"] is True


def test_the_gate_is_the_mean_and_it_can_fail(tmp_path) -> None:
    """4.0 exactly meets; 3.8 does not, and the shortfall is reported unrounded."""
    path = write_scores(tmp_path, scores=all_scored(4))
    rows, _meta = prettify_rubric.run(path, MANIFEST)
    assert prettify_rubric.summarise(rows)["passes"] is True

    scores = {case_id: 4 for case_id in LOGO_IDS}
    scores["logo-01-tee-chest"] = 3
    path = write_scores(tmp_path, scores=scores)
    rows, _meta = prettify_rubric.run(path, MANIFEST)
    stats = prettify_rubric.summarise(rows)
    assert stats["mean"] == pytest.approx(3.8)
    assert stats["passes"] is False
    assert stats["shortfall"] == pytest.approx(0.2)


def test_the_gate_constant_is_the_one_the_report_quotes() -> None:
    """`>= 4 / 5` as a mean. Changing the reading is a one-line change, not two verdicts."""
    assert prettify_rubric.RUBRIC_PASS_MEAN == 4.0
    assert (prettify_rubric.SCORE_MIN, prettify_rubric.SCORE_MAX) == (1, 5)


def test_render_states_the_gate_and_every_per_case_score(tmp_path) -> None:
    """The report must let a reader apply the other reading of `>= 4 / 5` by eye."""
    path = write_scores(tmp_path, scores=all_scored(4))
    rows, meta = prettify_rubric.run(path, MANIFEST)
    report = prettify_rubric.render(rows, meta, prettify_rubric.summarise(rows))
    assert "human scores (E2-T3)" in report
    assert "MEETS" in report
    for case_id in LOGO_IDS:
        assert case_id in report, case_id
    assert "RUBRIC_PASS_MEAN = 4.0" in report
    assert "one person's judgement on one day" in report
    assert "inter-rater reliability" in report
    assert "does **not** detect a **missing** logo" in report


# ------------------------------------------------------------------- wiring into the harness
def test_the_harness_row_is_unmeasured_and_its_note_names_the_missing_ids() -> None:
    """With the committed template the row must stay UNMEASURED, and the note must say what.

    'UNMEASURED' alone would be honest but useless: the reader needs to know it is five specific
    cases, not an infrastructure problem.
    """
    from evals import run as harness

    measured, notes = harness.measurements()
    assert "prettify hallucinated-text rubric" not in measured
    rubric_notes = [note for note in notes if "hallucinated-text rubric" in note]
    assert rubric_notes, notes
    note = rubric_notes[0]
    assert "UNMEASURED" in note
    assert "5 of 5" in note
    for case_id in LOGO_IDS:
        assert case_id in note, (case_id, note)


def test_the_committed_template_is_a_template_not_a_score() -> None:
    """Guard the shipped file: every score null, every provenance field empty.

    This is the one test that would catch somebody "fixing" the UNMEASURED row by typing a
    plausible 4 into the repository.
    """
    payload = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert payload["rubric_version"] == ""
    assert payload["scorer"] == ""
    assert payload["scored_at"] == ""
    assert [entry["id"] for entry in payload["scores"]] == LOGO_IDS
    assert all(entry["score"] is None for entry in payload["scores"])
