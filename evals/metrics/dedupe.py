"""E3-T4 — a THIN report wrapper around `packages.vision.dedupe.run_fixture()`.

    python -m evals.metrics.dedupe                    # writes evals/reports/dedupe.md
    python -m evals.metrics.dedupe --fixture-dir … --out …

**The comparison logic is NOT reimplemented here.** `run_fixture()` is the single source of
truth: it scores every pair through the very same `_score_candidate()` that `find_duplicate()`
uses, prints one verdict per pair, and returns the summary numbers. This module only captures
that stdout, parses the printed rows into a markdown table, and returns `run_fixture()`'s dict
unchanged. Reimplementing the thresholds here would create a second gate that could silently
drift away from the shipped one — the exact failure mode this harness exists to prevent.

Zero network, zero credentials, zero model weights: the fixture images ship with the repo.
`evals/run.py::measurements()` calls `run()` below for the `dedupe false-merge rate` row.

Denominator: `false_merge_rate = n_false_merges / n_distinct`, **not** `/ n_pairs`. A false
merge can only happen on a genuinely distinct pair, so dividing by all 20 pairs would quietly
halve the rate. `run_fixture()` already implements that; this report states it out loud.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import sys
from pathlib import Path

from packages.vision.dedupe import COLOUR_MIN, FALSE_MERGE_MAX, STRUCTURE_MIN, run_fixture

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = ROOT / "evals" / "fixtures" / "dedupe"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "dedupe.md"


def _load_pairs(fixture_dir: Path) -> list[dict]:
    """Read the manifest for canonical pair ids/labels. Scoring still comes from run_fixture."""
    path = fixture_dir / "manifest.json"
    if not path.is_file():
        raise ValueError(f"no manifest.json in {fixture_dir}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    pairs = manifest.get("pairs") if isinstance(manifest, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise ValueError(f"{path} has no non-empty 'pairs' list")
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict) or not isinstance(pair.get("id"), str):
            raise ValueError(f"{path} pair {index} has no string 'id'")
    return pairs


def _parse_printed_rows(printed: str, pairs: list[dict]) -> list[dict]:
    """Turn run_fixture()'s printed table into report rows. No scoring, no thresholds."""
    known = {str(pair["id"]) for pair in pairs}
    rows: list[dict] = []
    for line in printed.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 8 or parts[0] not in known:
            continue  # header, blank line, or one of run_fixture()'s summary lines
        pair_id, label, kind, verdict, layer, structure, colour, note = parts
        rows.append(
            {
                "id": pair_id,
                "label": label,
                "kind": kind,
                "verdict": verdict,  # "duplicate" when matched, "distinct" otherwise
                "layer": layer,
                "structure": structure,
                "colour": colour,
                "note": note.strip(),
                "false_merge": verdict == "duplicate" and label == "distinct",
                "duplicate_hit": verdict == "duplicate" and label == "duplicate",
                "duplicate_missed": verdict == "distinct" and label == "duplicate",
                "distinct_correct": verdict == "distinct" and label == "distinct",
            }
        )
    if len(rows) != len(pairs):
        raise ValueError(
            f"run_fixture() printed {len(rows)} pair rows but the manifest has {len(pairs)} "
            "pairs — the report would be missing a verdict"
        )
    return rows


def run(fixture_dir: Path) -> tuple[list[dict], dict]:
    """Score every pair by calling `run_fixture()` once; return parsed rows and provenance."""
    pairs = _load_pairs(fixture_dir)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            summary = run_fixture(fixture_dir)
        except SystemExit as exc:  # run_fixture() aborts on a broken manifest
            raise ValueError(f"run_fixture() refused the fixture: {exc}") from exc
    printed = buffer.getvalue()
    rows = _parse_printed_rows(printed, pairs)
    meta = {
        "fixture_dir": str(fixture_dir),
        "n_pairs": int(summary["n_pairs"]),
        "n_distinct": int(summary["n_distinct"]),
        "n_duplicate": int(summary["n_duplicate"]),
        "n_false_merges": int(summary["n_false_merges"]),
        "printed": printed,
        "summary": summary,
        "thresholds": {
            "structure_min": STRUCTURE_MIN,
            "colour_min": COLOUR_MIN,
            "false_merge_max": FALSE_MERGE_MAX,
        },
    }
    return rows, meta


def summarise(rows: list[dict], meta: dict) -> dict:
    """Return run_fixture()'s numbers, cross-checked against the parsed per-pair verdicts."""
    summary = meta["summary"]
    rate = float(summary["false_merge_rate"])
    n_distinct = meta["n_distinct"]
    from_rows = sum(1 for row in rows if row["false_merge"])
    if from_rows != meta["n_false_merges"]:
        raise ValueError(
            f"parsed rows report {from_rows} false merges but run_fixture() returned "
            f"{meta['n_false_merges']}"
        )
    if n_distinct and math.isfinite(rate):
        expected = from_rows / n_distinct
        if not math.isclose(rate, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(
                f"false_merge_rate {rate} does not equal {from_rows}/{n_distinct} = {expected}"
            )
    finite = math.isfinite(rate)
    return {
        **summary,
        "false_merge_rate": rate,
        "n_false_merges_from_rows": from_rows,
        "gate_max": FALSE_MERGE_MAX,
        "meets_gate": finite and rate <= FALSE_MERGE_MAX,
        "n_duplicate_hits": sum(1 for row in rows if row["duplicate_hit"]),
        "n_duplicate_missed": sum(1 for row in rows if row["duplicate_missed"]),
        "n_distinct_correct": sum(1 for row in rows if row["distinct_correct"]),
    }


def render(rows: list[dict], meta: dict, stats: dict) -> str:
    """The E3-T4 artifact: every pair verdict, the denominator, and the raw run_fixture output."""
    rate = stats["false_merge_rate"]
    n_false = meta["n_false_merges"]
    n_distinct = meta["n_distinct"]
    n_pairs = meta["n_pairs"]
    thresholds = meta["thresholds"]
    verdict = "MEETS" if stats["meets_gate"] else "DOES NOT MEET"
    lines = [
        "# E3-T4 — dedupe false-merge rate",
        "",
        f"- fixture: `{meta['fixture_dir']}` (shipped with the repo, no network, no weights)",
        "- scorer: `packages.vision.dedupe.run_fixture()` — the same `_score_candidate()` "
        "policy that `find_duplicate()` uses; NOT reimplemented in this module",
        f"- n_pairs = {n_pairs} · n_duplicate = {meta['n_duplicate']} · "
        f"n_distinct = {n_distinct}",
        f"- thresholds: structure >= {thresholds['structure_min']:.2f}, "
        f"colour >= {thresholds['colour_min']:.2f}, gate "
        f"false_merge_rate <= {thresholds['false_merge_max']:.2f}",
        "",
        "## The result, and its denominator",
        "",
        f"false_merge_rate = {n_false} / {n_distinct} = {rate:.4f} (raw {rate!r}) "
        f"— {verdict} the <= {thresholds['false_merge_max']:.2f} target.",
        "",
        "The denominator is **n_distinct**, the number of genuinely distinct pairs, NOT "
        "n_pairs. A false merge can only occur on a distinct pair; dividing by all "
        f"{n_pairs} pairs would report {n_false / n_pairs:.4f} here, and would halve any "
        "non-zero rate. `run_fixture()` implements the correct denominator; this report "
        "does not change it.",
        "",
        f"Per-pair classification from the same run: {stats['n_duplicate_hits']} duplicate "
        f"pairs detected, {stats['n_duplicate_missed']} duplicate pairs missed, "
        f"{stats['n_distinct_correct']} distinct pairs correctly left alone, "
        f"{n_false} distinct pairs falsely merged.",
        "",
        "## Every pair verdict (verbatim, from run_fixture's printed table)",
        "",
        "| # | id | label | kind | verdict | layer | structure | colour | note |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, 1):
        note = row["note"].replace("|", "\\|")
        lines.append(
            f"| {index} | {row['id']} | {row['label']} | {row['kind']} | "
            f"{row['verdict']} | {row['layer']} | {row['structure']} | "
            f"{row['colour']} | {note} |"
        )
    lines += [
        "",
        "## Raw `run_fixture()` output (nothing filtered out)",
        "",
        "```text",
        meta["printed"].rstrip(),
        "```",
        "",
        "## Caveats that travel with the number",
        "",
        "- `kind=exact` pairs are decided by sha256 of the uploaded bytes. Their "
        "`similarity = 1.0` is the layer's DEFINITION, not a measured capability, and no "
        "perceptual threshold can change their verdict.",
        "- `kind=near` positives are same-source transforms (rescale, re-encode, crop, "
        "exposure), so near recall on this fixture is an UPPER BOUND, and the perceptual "
        "thresholds were chosen in-sample on these 20 pairs.",
        "- The colour gate is load-bearing for recoloured uploads but is NOT observable on "
        "this fixture: every distinct pair is already stopped by structure. It is defended "
        "by unit tests in `packages/vision/tests`, not by these 20 pairs.",
        "- `label=distinct` means two different physical objects, never two photos of one "
        "item. A second photograph of the same garment is a known design boundary and is "
        "not detected by v1.",
        "- `evals/reports/dedupe.md` is a gitignored generated artifact. The "
        "`dedupe false-merge rate` row in `python -m evals.run --suite vision` is computed "
        "from the same `run_fixture()` call, over the same shipped fixtures, on every run — "
        "unlike the four E3-T3 attribute rows, it needs no local frozen photos.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.metrics.dedupe")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    try:
        rows, meta = run(args.fixture_dir)
        stats = summarise(rows, meta)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(
            f"dedupe metric cannot measure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    text = render(rows, meta, stats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(
        f"wrote {args.out} — n_pairs={meta['n_pairs']} n_distinct={meta['n_distinct']} "
        f"false_merge_rate={stats['false_merge_rate']:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
