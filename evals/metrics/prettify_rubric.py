"""E2-T3 — the `prettify hallucinated-text rubric` row: the platform a human scores on.

    python -m evals.metrics.prettify_rubric        # print the rubric half on its own
    python -m evals.metrics.prettify               # writes evals/reports/prettify.md (both halves)

## What this module is, and what it deliberately is not

It is a **validator and a summariser over a file a person fills in**. It is not a scorer. "Did the
prettify step invent text that was not in the photograph?" is a human judgement; no model in this
repo is allowed to stand in for it, and a plausible-looking 4 is worse than `UNMEASURED`.

The numbers live in `evals/fixtures/prettify/rubric_scores.json`, which is **committed**. That is
the load-bearing decision of this ticket: `evals/reports/*` is gitignored, so anything stored
there is absent from a clean clone and the row could never be reproduced. A committed scores file
means the metric is reproducible on any machine, and `git log` on it is the audit trail of who
scored what and when.

## The gate

`evals/run.py` targets `>= 4 / 5` for this row. On a 1-5 scale the natural reading is the **mean**
of the five per-case scores, and that is what `RUBRIC_PASS_MEAN` implements. The other defensible
reading — "at least 4 of the 5 cases are acceptable" — would need a per-case pass threshold that
the DoD does not define, so it is NOT implemented and NOT silently mixed in: exactly one verdict is
produced. The report prints every per-case score, so a reader who prefers the other reading can
compute it by eye without changing any code.

## Why the five logo cases, and only those

Only the `logo-*` fixtures have printed text (`protect_box`), so only they can exhibit a
hallucinated logo. The `crease-*` cases are scored for SSIM and nothing else. Scoring a crease
case on "hallucinated text" would dilute the mean with cases that cannot fail, which is the same
flattery this project keeps removing from its metrics.

## What it cannot prove

* **It cannot tell whether the protect region was invaded.** That is a pixel property, pinned by
  `packages/vision/tests/test_prettify.py` (`protect_overlap_px == 0` on all five) and reported in
  the SSIM half of `evals/reports/prettify.md`. A scorer can give a 5 while the mask grazed the
  logo.
* **It cannot detect a *missing* logo.** The rubric asks whether text was invented, not whether it
  survived; a fill that erases a logo entirely can still score 5.
* **It is five judgements by one person on one day.** `scorer` and `scored_at` are recorded because
  a number without provenance is a number nobody can audit — but they are not inter-rater
  reliability. One scorer is one scorer.
* **It cannot go red from code.** No change to `packages/vision/` moves this number; only a person
  can. That is why the harness treats "no scores" as `UNMEASURED` rather than as a pass or a fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = ROOT / "evals" / "fixtures" / "prettify"
DEFAULT_SCORES = DEFAULT_FIXTURE_DIR / "rubric_scores.json"
DEFAULT_MANIFEST = DEFAULT_FIXTURE_DIR / "manifest.json"

#: The whole gate. `>= 4 / 5` read as a MEAN over per-case 1-5 scores. Changing the reading (e.g.
#: "at least N cases pass") is a one-line change here plus a per-case threshold that the DoD does
#: not currently define — do not add a second verdict beside this one.
RUBRIC_PASS_MEAN = 4.0
SCORE_MIN = 1
SCORE_MAX = 5
#: Only cases that carry printed text can hallucinate it.
SCORED_KIND = "logo"
#: A score without these three is a number without a source, and this project has paid for those
#: before. All three must be non-empty strings before the row is allowed to report a number.
PROVENANCE_FIELDS = ("rubric_version", "scorer", "scored_at")


# --------------------------------------------------------------------------------- helpers


def _load_json(path: Path, what: str) -> Any:
    """Read one JSON file, raising a ValueError that names it instead of a bare OSError."""
    if not path.is_file():
        raise ValueError(f"{what} file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} file is not valid JSON: {exc}") from exc


def _is_int(value: object) -> bool:
    """A bool is not a score. `isinstance(True, int)` is True in Python, which is a trap here."""
    return isinstance(value, int) and not isinstance(value, bool)


def _cases(manifest: Any) -> list[dict]:
    """The manifest's case list, whichever shape the file uses."""
    if isinstance(manifest, dict):
        cases = manifest.get("cases")
    else:
        cases = manifest
    if not isinstance(cases, list) or not cases:
        raise ValueError("fixture manifest has no 'cases' list to score against")
    return cases


def scored_cases(manifest_path: Path) -> list[dict]:
    """The cases this rubric is scored on: the ones that carry printed text.

    Returns `[{"id", "flat", "protect_box"}, …]` in manifest order. Raises when the manifest
    yields none — an empty denominator must never look like a pass.
    """
    cases = _cases(_load_json(manifest_path, "fixture manifest"))
    selected: list[dict] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"fixture manifest case {index} is not an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"fixture manifest case {index} has no string 'id'")
        if case.get("kind") != SCORED_KIND:
            continue
        selected.append(
            {
                "id": case_id,
                "flat": case.get("flat"),
                "protect_box": case.get("protect_box"),
            }
        )
    if not selected:
        raise ValueError(
            f"fixture manifest has no {SCORED_KIND!r} cases; this rubric scores the cases that "
            "carry printed text and cannot measure anything without them"
        )
    return selected


def _validate_provenance(payload: dict) -> dict[str, str]:
    """All three provenance fields must be filled. Empty provenance is a missing measurement."""
    provenance: dict[str, str] = {}
    empty: list[str] = []
    for field in PROVENANCE_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            empty.append(field)
            continue
        provenance[field] = value.strip()
    if empty:
        raise ValueError(
            "the scores are not attributable: "
            + ", ".join(repr(field) for field in empty)
            + " must be non-empty (a score with no scorer and no date is a number without a "
            "source, not a measurement)"
        )
    return provenance


def _collect_scores(payload: dict, expected_ids: list[str]) -> dict[str, int]:
    """Validate every score, then return `{id: score}`. Every branch names what is wrong."""
    entries = payload.get("scores")
    if not isinstance(entries, list):
        raise ValueError("scores file has no 'scores' list")
    if not entries:
        raise ValueError("scores file has an empty 'scores' list")

    known = set(expected_ids)
    by_id: dict[str, object] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"scores entry {index} is not an object")
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError(f"scores entry {index} has no string 'id'")
        if entry_id in by_id:
            raise ValueError(f"duplicate score id {entry_id!r}")
        by_id[entry_id] = entry.get("score")

    extra = sorted(set(by_id) - known)
    if extra:
        raise ValueError(
            f"scores contain ids that are not scored cases in the manifest: {extra} — this rubric "
            f"covers exactly the {len(expected_ids)} {SCORED_KIND!r} cases"
        )

    unscored: list[str] = []
    for case_id in expected_ids:
        if case_id not in by_id:
            unscored.append(case_id)
            continue
        score = by_id[case_id]
        if score is None:
            unscored.append(case_id)
            continue
        if not _is_int(score):
            raise ValueError(
                f"score for {case_id!r} is not an integer 1-5: {score!r}"
                " (a half mark or a string is not a judgement on this scale)"
            )
        if not SCORE_MIN <= int(score) <= SCORE_MAX:
            raise ValueError(
                f"score for {case_id!r} is {score}, outside the 1-5 scale"
            )
    if unscored:
        raise ValueError(
            f"{len(unscored)} of {len(expected_ids)} {SCORED_KIND} cases have no score yet: "
            + ", ".join(unscored)
        )

    return {case_id: int(by_id[case_id]) for case_id in expected_ids}  # type: ignore[arg-type]


# -------------------------------------------------------------------------------------- run


def run(scores_path: Path, manifest_path: Path) -> tuple[list[dict], dict]:
    """Join the human scores to the scored cases. `ValueError` on anything incomplete or illegal.

    The order of the checks is deliberate: **missing scores are reported before missing
    provenance**. On the committed template both are empty, and "5 of 5 logo cases have no score
    yet" is the message that tells a reader what to do; "scorer is empty" would be true but less
    useful. Either way the row becomes UNMEASURED — a missing score is never a pass.
    """
    cases = scored_cases(manifest_path)
    expected_ids = [case["id"] for case in cases]
    payload = _load_json(scores_path, "rubric scores")
    if not isinstance(payload, dict):
        raise ValueError("scores file must be a JSON object")

    scores = _collect_scores(payload, expected_ids)
    provenance = _validate_provenance(payload)

    rows = [
        {
            "id": case["id"],
            "score": scores[case["id"]],
            "flat": case["flat"],
            "protect_box": case["protect_box"],
        }
        for case in cases
    ]
    meta = {
        "scores_path": str(scores_path),
        "manifest_path": str(manifest_path),
        "n": len(rows),
        **provenance,
    }
    return rows, meta


def summarise(rows: list[dict]) -> dict:
    """Mean of the per-case scores, and the one verdict the gate defines.

    `n` is the number of scored cases; `run()` guarantees every one of them has a score, so the
    denominator cannot silently shrink.
    """
    if not rows:
        raise ValueError("no scored cases to summarise")
    values = [int(row["score"]) for row in rows]
    mean = sum(values) / len(values)
    return {
        "n": len(values),
        "mean": mean,
        "min": min(values),
        "max": max(values),
        "mean_at_least_two_decimal": round(mean, 2),
        "threshold": RUBRIC_PASS_MEAN,
        "passes": mean >= RUBRIC_PASS_MEAN,
        "shortfall": max(RUBRIC_PASS_MEAN - mean, 0.0),
    }


# ----------------------------------------------------------------------------------- render


def render(
    rows: list[dict],
    meta: dict,
    stats: dict,
    *,
    images: dict[str, Path | str] | None = None,
) -> str:
    """The rubric half of `evals/reports/prettify.md`: the scores, the gate, the caveat.

    `images` maps case id to the prettified output a scorer is meant to look at. It is optional so
    this can be rendered without running the stage (tests, and the harness row, which only needs
    the number).
    """
    verdict = "MEETS" if stats["passes"] else "DOES NOT MEET"
    lines = [
        "## `prettify hallucinated-text rubric` — human scores (E2-T3)",
        "",
        f"- scored cases: n = {stats['n']} (the `{SCORED_KIND}` fixtures — the only ones that "
        "carry printed text, so the only ones that can hallucinate it)",
        f"- scorer: `{meta['scorer']}` · scored at: `{meta['scored_at']}` · "
        f"rubric version: `{meta['rubric_version']}`",
        f"- scores file: `{meta['scores_path']}` (committed, so this number is reproducible on a "
        "clean clone)",
        "",
        f"**Verdict: {verdict}.** mean = {stats['mean']:.4f} against >= "
        f"{stats['threshold']:.1f} per case on a 1-5 scale"
        + (
            ""
            if stats["passes"]
            else f"; the shortfall is {stats['shortfall']:.4f} and is not rounded away"
        )
        + ".",
        "",
        f"The gate is the MEAN (`RUBRIC_PASS_MEAN = {RUBRIC_PASS_MEAN}`). `>= 4 / 5` can also be "
        'read as "at least 4 of the 5 cases are acceptable", which would need a per-case pass '
        "threshold the DoD does not define; it is not implemented, and no second verdict is "
        "printed. Every per-case score is listed below, so that reading can be computed by eye.",
        "",
        "### What the scores are about, and what they cannot prove",
        "",
        "Each score answers one question: **did the prettify step invent text or a logo that is "
        "not in the photograph?** 1 = text appeared out of nowhere; 5 = nothing was invented.",
        "",
        "- It does **not** measure whether the protect region was invaded — that is a pixel "
        "property, pinned by `packages/vision/tests/test_prettify.py` (`protect_overlap_px == 0` "
        "on all five) and reported in the SSIM half above. A 5 here is compatible with a mask "
        "that grazed the logo.",
        "- It does **not** detect a **missing** logo. Erasing printed text completely can still "
        "score 5: the rubric asks whether anything was invented, not whether everything survived.",
        "- It is one person's judgement on one day. `scorer` and `scored_at` are recorded so the "
        "number can be audited, not because they establish inter-rater reliability.",
        "",
        "### Per-case scores",
        "",
        "| case | score | prettified output scored | protect box (px, x/y/w/h) |",
        "|---|---|---|---|",
    ]
    for row in rows:
        image = ""
        if images and row["id"] in images:
            image = f"`{images[row['id']]}`"
        box = row.get("protect_box")
        lines.append(
            f"| {row['id']} | {row['score']} | {image or '—'} | "
            f"{box if box is not None else '—'} |"
        )
    lines += [
        "",
        "| summary | value |",
        "|---|---|",
        f"| mean (n={stats['n']}) | {stats['mean']:.4f} |",
        f"| min / max | {stats['min']} / {stats['max']} |",
        f"| gate | mean >= {stats['threshold']:.1f} |",
        f"| verdict | {verdict} |",
        "",
        "Until every case above carries an integer 1-5 **and** `rubric_version` / `scorer` / "
        "`scored_at` are filled in, this row reports `UNMEASURED` with the missing ids named. "
        "`eval` never guesses: an unscored case is not a pass, and it is not a zero.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.metrics.prettify_rubric")
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)

    try:
        rows, meta = run(args.scores, args.manifest)
        stats = summarise(rows)
    except (OSError, ValueError) as exc:
        print(f"RUBRIC UNMEASURED — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(render(rows, meta, stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
