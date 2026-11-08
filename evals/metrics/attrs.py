"""E3-T3 — the four attribute accuracies, computed from a frozen predictions artifact.

    python -m evals.metrics.attrs                         # writes evals/reports/attrs.md
    python -m evals.metrics.attrs --predictions … --manifest … --out …

This module is a PURE FUNCTION over two files on disk. It opens no socket, reads no credential
and instantiates no VLM. The 40 real provider calls are made ONCE by
`python -m evals.tools.collect_attrs`, which writes `evals/reports/attrs_predictions.json`.
`evals/run.py` consumes `summarise()` below to fill four rows of the vision table. Keeping
collection and computation separate is the whole point: the harness (`pytest`, `make test`,
`python -m evals.run`) must stay zero-network, and a number must never be recomputed from a live
endpoint just because somebody printed the report.

## Matching rules (frozen; mechanically checkable, identical in shape for all four fields)

Every string comparison normalises with `.strip().lower()` first. `n` is ALWAYS the number of
entries in `evals/golden/manifest.json` (40 in this checkout). A prediction whose `status` is
`failed` is a MISS for all four fields AND stays in the denominator; nothing is dropped, so a
broken batch can never flatter an accuracy.

* `category` — exact string equality with the ground truth. No synonym table, no near match.
* `colors` — **primary-colour hit**: the predicted list is non-empty AND its FIRST element is
  present anywhere in the ground-truth `colors` list. Both lists are primary-first, but the rule
  deliberately accepts a secondary ground-truth colour as a hit. That is the declared
  definition, not a tuning dial. The secondary statistic `colors_exact_list` (strict list
  equality: same order, same length, same normalised strings) is reported next to it, so the
  friendlier number cannot be quoted alone.
* `pattern` — exact string equality. The literal `unknown` never equals a concrete ground truth
  value (no manifest entry has `unknown`, but the rule is stated and enforced anyway).
* `material` — a NON-EMPTY intersection between the predicted list and the ground-truth list.
  An empty predicted list is a MISS. `empty_predictions` counts those separately.

## Why `material` cannot reach its 0.80 target under the current evidence policy

`apps/api/schemas.py::ItemAttributes` and `packages/vision/attrs_adapters/base.py` require
`material` to contain ONLY fibres with visual or care-label evidence; with no such evidence the
adapter must emit `[]`. The golden photos are catalogue shots with no legible care labels, so
`material=[]` is the CORRECT output for most of them, and an empty list can never intersect a
non-empty ground-truth material list. `>= 0.80` is therefore UNREACHABLE under this policy.
That is a consequence of the schema's evidence contract, NOT a bug and NOT a model defect.
**This ticket did not relax the evidence policy, widen the adapter, change the prompt, or edit
any ground truth to chase the threshold.** The verdict is reported as measured, with no rounding
up and no euphemism.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREDICTIONS = ROOT / "evals" / "reports" / "attrs_predictions.json"
DEFAULT_MANIFEST = ROOT / "evals" / "golden" / "manifest.json"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "attrs.md"

FIELDS: tuple[str, ...] = ("category", "colors", "pattern", "material")
#: The four keys are EXACTLY the `evals/run.py::TARGETS` names. Do not rename one side only.
METRIC_KEYS: dict[str, str] = {
    "attr category accuracy": "category",
    "attr colour accuracy": "colors",
    "attr pattern accuracy": "pattern",
    "attr material accuracy": "material",
}
FIELD_TO_KEY: dict[str, str] = {field: key for key, field in METRIC_KEYS.items()}
THRESHOLDS: dict[str, float] = {
    "attr category accuracy": 0.95,
    "attr colour accuracy": 0.90,
    "attr pattern accuracy": 0.85,
    "attr material accuracy": 0.80,
}
STATUSES: frozenset[str] = frozenset({"measured", "failed"})


# --------------------------------------------------------------------------------- helpers


def _load_json(path: Path, what: str) -> Any:
    """Read one JSON file, raising a ValueError that names it instead of a bare OSError."""
    if not path.is_file():
        raise ValueError(f"{what} file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{what} file is not valid JSON: {exc}") from exc


def _norm(value: object) -> str:
    """Normalise one scalar for exact comparison. `None` becomes the empty string, not "None"."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _norm_list(value: object) -> list[str]:
    """Normalise a list of strings. A non-list (or `None`) is an empty list, never a crash."""
    if not isinstance(value, list):
        return []
    return [_norm(item) for item in value]


def _fmt(value: object) -> str:
    """Unambiguous, machine-readable rendering for the confusion list (no prose rounding)."""
    return json.dumps(value, ensure_ascii=False)


def _md(value: object) -> str:
    """`_fmt` without Markdown-breaking pipes/newlines; plain strings stay unquoted."""
    text = value if isinstance(value, str) else _fmt(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce optional numeric metadata. A bool is not a number here."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return float(value)


def _empty_prediction(value: object) -> bool:
    """Whether the measured row emitted an EMPTY LIST (failed rows carry `None` instead)."""
    return isinstance(value, list) and len(value) == 0


def _validate_measured_record(rid: str, record: dict) -> None:
    """A `measured` row must carry all four predictions with the schema's own types."""
    for field in FIELDS:
        if field not in record:
            raise ValueError(f"measured prediction {rid!r} is missing {field!r}")
        value = record[field]
        if field in ("colors", "material"):
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValueError(
                    f"measured prediction {rid!r} field {field!r} must be a list of strings"
                )
        elif not isinstance(value, str):
            raise ValueError(f"measured prediction {rid!r} field {field!r} must be a string")


def _field_result(field: str, ground_truth: object, predicted: object) -> tuple[bool, str]:
    """One field's hit and, on a miss, the exact reason. All four rules live here together."""
    if field == "category":
        hit = bool(_norm(predicted)) and _norm(predicted) == _norm(ground_truth)
        reason = (
            ""
            if hit
            else f"exact string mismatch: predicted {_fmt(predicted)} vs "
            f"ground truth {_fmt(ground_truth)}"
        )
        return hit, reason
    if field == "colors":
        pred_list = _norm_list(predicted)
        gt_list = _norm_list(ground_truth)
        if not pred_list:
            return False, "predicted colors list is empty (or not a list): no primary colour"
        hit = pred_list[0] in gt_list
        reason = (
            ""
            if hit
            else f"primary predicted colour {_fmt(pred_list[0])} is absent from ground "
            f"truth {_fmt(gt_list)}"
        )
        return hit, reason
    if field == "pattern":
        value = _norm(predicted)
        if value == "unknown":
            return False, (
                "predicted pattern is 'unknown', which never equals a concrete ground truth"
            )
        hit = bool(value) and value == _norm(ground_truth)
        reason = (
            ""
            if hit
            else f"exact string mismatch: predicted {_fmt(predicted)} vs "
            f"ground truth {_fmt(ground_truth)}"
        )
        return hit, reason
    pred_list = _norm_list(predicted)
    gt_list = _norm_list(ground_truth)
    if not pred_list:
        return False, "predicted material list is empty (no visual/care-label fibre evidence)"
    hit = any(value in gt_list for value in pred_list)
    reason = (
        ""
        if hit
        else f"no non-empty intersection between predicted {_fmt(pred_list)} and "
        f"ground truth {_fmt(gt_list)}"
    )
    return hit, reason


# -------------------------------------------------------------------------------------- run


def run(predictions_path: Path, manifest_path: Path) -> tuple[list[dict], dict]:
    """Join the frozen predictions artifact to the golden manifest, one row per manifest entry.

    Raises `ValueError` on any structural inconsistency (bad JSON, wrong entry count, missing
    id, duplicate id, a `measured` row with a wrong field type). A corrupt artifact must make
    the harness print UNMEASURED with a reason, never a guessed subset of numbers.
    """
    manifest = _load_json(manifest_path, "golden manifest")
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("golden manifest must be a non-empty JSON array")
    for index, entry in enumerate(manifest):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError(f"golden manifest entry {index} has no string 'id'")
        for field in FIELDS:
            if field not in entry:
                raise ValueError(
                    f"golden manifest entry {entry['id']!r} lacks ground truth {field!r}"
                )

    payload = _load_json(predictions_path, "predictions")
    if not isinstance(payload, dict):
        raise ValueError("predictions artifact must be a JSON object")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ValueError("predictions artifact has no 'entries' list")
    n_entries = payload.get("n_entries")
    if isinstance(n_entries, bool) or not isinstance(n_entries, int):
        raise ValueError(f"predictions 'n_entries' is not an integer: {n_entries!r}")
    if n_entries != len(entries):
        raise ValueError(
            f"predictions 'n_entries' ({n_entries}) does not match len(entries) ({len(entries)})"
        )
    if n_entries != len(manifest):
        raise ValueError(
            f"predictions cover {n_entries} entries but the golden manifest has {len(manifest)}"
        )

    by_id: dict[str, dict] = {}
    for index, record in enumerate(entries):
        if not isinstance(record, dict):
            raise ValueError(f"predictions entry {index} is not an object")
        rid = record.get("id")
        if not isinstance(rid, str) or not rid:
            raise ValueError(f"predictions entry {index} has no string 'id'")
        if rid in by_id:
            raise ValueError(f"duplicate prediction id {rid!r}")
        status = record.get("status")
        if status not in STATUSES:
            raise ValueError(f"prediction {rid!r} has invalid status {status!r}")
        if status == "measured":
            _validate_measured_record(rid, record)
        else:
            for key in ("error_type", "error_message"):
                if key not in record:
                    raise ValueError(f"failed prediction {rid!r} is missing {key!r}")
        by_id[rid] = record

    manifest_ids = [str(entry["id"]) for entry in manifest]
    known = set(manifest_ids)
    extra = sorted(set(by_id) - known)
    if extra:
        raise ValueError(f"predictions contain ids absent from the golden manifest: {extra}")
    missing = [rid for rid in manifest_ids if rid not in by_id]
    if missing:
        raise ValueError(f"predictions are missing ids from the golden manifest: {missing}")

    rows: list[dict] = []
    for entry in manifest:
        rid = str(entry["id"])
        record = by_id[rid]
        status = str(record["status"])
        ground_truth = {field: entry[field] for field in FIELDS}
        predicted = (
            {field: record[field] for field in FIELDS}
            if status == "measured"
            else {field: None for field in FIELDS}
        )
        if status == "failed":
            failure = (
                f"prediction failed ({record.get('error_type')}): {record.get('error_message')}"
            )
            hits = {field: False for field in FIELDS}
            reasons = {field: failure for field in FIELDS}
        else:
            hits = {}
            reasons = {}
            for field in FIELDS:
                hit, reason = _field_result(field, ground_truth[field], predicted[field])
                hits[field] = hit
                reasons[field] = reason
        rows.append(
            {
                "id": rid,
                "status": status,
                "ground_truth": ground_truth,
                "predicted": predicted,
                "hits": hits,
                "reasons": reasons,
                "colors_exact_list": _norm_list(predicted["colors"])
                == _norm_list(ground_truth["colors"]),
                "material_empty_prediction": status == "measured"
                and _empty_prediction(predicted["material"]),
                "round_trip_seconds": _as_float(record.get("round_trip_seconds")),
                "prompt_tokens": int(_as_float(record.get("prompt_tokens"))),
                "completion_tokens": int(_as_float(record.get("completion_tokens"))),
                "attempts": int(_as_float(record.get("attempts"), 1.0)),
                "retry_count": int(_as_float(record.get("retry_count"))),
                "error_type": record.get("error_type"),
                "error_message": record.get("error_message"),
            }
        )

    n_measured = sum(1 for row in rows if row["status"] == "measured")
    n_failed = len(rows) - n_measured
    declared_measured = payload.get("n_measured")
    declared_failed = payload.get("n_failed")
    if isinstance(declared_measured, int) and declared_measured != n_measured:
        raise ValueError(
            f"predictions 'n_measured' ({declared_measured}) disagrees with the rows ({n_measured})"
        )
    if isinstance(declared_failed, int) and declared_failed != n_failed:
        raise ValueError(
            f"predictions 'n_failed' ({declared_failed}) disagrees with the rows ({n_failed})"
        )

    meta = {
        "predictions_path": str(predictions_path),
        "manifest_path": str(manifest_path),
        "generated_at": payload.get("generated_at"),
        "git_sha": payload.get("git_sha"),
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "n_entries": n_entries,
        "n_measured": n_measured,
        "n_failed": n_failed,
        "prompt_tokens_total": payload.get("prompt_tokens_total"),
        "completion_tokens_total": payload.get("completion_tokens_total"),
        "round_trip_seconds_total": payload.get("round_trip_seconds_total"),
        "retry_count_total": payload.get("retry_count_total"),
        "token_accounting": payload.get("token_accounting"),
    }
    return rows, meta


# -------------------------------------------------------------------------------- summarise


def summarise(rows: list[dict]) -> dict:
    """Four REAL floats (`hits/n`) plus hits, misses and empty predictions for every field.

    `n` is `len(rows)`, i.e. the whole golden manifest: failed entries are misses, never
    exclusions. The confusion list below lists EVERY miss verbatim; the secondary
    `colors_exact_list` misses are included too, tagged as a secondary statistic.
    """
    n = len(rows)
    if n == 0:
        raise ValueError("no rows to summarise")

    fields: dict[str, dict] = {}
    accuracies: dict[str, float] = {}
    for field in FIELDS:
        hits = sum(1 for row in rows if row["hits"][field])
        fields[field] = {
            "hits": hits,
            "misses": n - hits,
            "accuracy": hits / n,
            "empty_predictions": sum(
                1 for row in rows if _empty_prediction(row["predicted"][field])
            ),
        }
        accuracies[FIELD_TO_KEY[field]] = hits / n

    exact_hits = sum(1 for row in rows if row["colors_exact_list"])
    colors_exact_list = {
        "hits": exact_hits,
        "misses": n - exact_hits,
        "accuracy": exact_hits / n,
    }

    confusion: list[dict] = []
    for row in rows:
        for field in FIELDS:
            if row["hits"][field]:
                continue
            confusion.append(
                {
                    "id": row["id"],
                    "field": field,
                    "kind": "primary",
                    "ground_truth": row["ground_truth"][field],
                    "predicted": row["predicted"][field],
                    "reason": row["reasons"][field],
                }
            )
        if not row["colors_exact_list"]:
            confusion.append(
                {
                    "id": row["id"],
                    "field": "colors_exact_list",
                    "kind": "secondary statistic",
                    "ground_truth": row["ground_truth"]["colors"],
                    "predicted": row["predicted"]["colors"],
                    "reason": (
                        "predicted colour list is not exactly equal to the ground truth list"
                    ),
                }
            )

    return {
        "n": n,
        "n_measured": sum(1 for row in rows if row["status"] == "measured"),
        "n_failed": sum(1 for row in rows if row["status"] == "failed"),
        "fields": fields,
        "accuracies": accuracies,
        "colors_exact_list": colors_exact_list,
        "confusion": confusion,
        "prompt_tokens_total": sum(row["prompt_tokens"] for row in rows),
        "completion_tokens_total": sum(row["completion_tokens"] for row in rows),
        "round_trip_seconds_total": round(
            sum(row["round_trip_seconds"] for row in rows), 3
        ),
        "retry_count_total": sum(row["retry_count"] for row in rows),
        "material_empty_predictions": fields["material"]["empty_predictions"],
    }


# ----------------------------------------------------------------------------------- render


def render(rows: list[dict], meta: dict, stats: dict) -> str:
    """The E3-T3 artifact: rules, verdicts, every miss verbatim, and the material caveat."""
    n = stats["n"]
    prompt_tokens = stats["prompt_tokens_total"]
    completion_tokens = stats["completion_tokens_total"]
    lines = [
        "# E3-T3 — attribute accuracy on the frozen golden set",
        "",
        f"- predictions artifact: `{meta['predictions_path']}`",
        f"- golden manifest: `{meta['manifest_path']}`",
        f"- collected at (UTC): {meta['generated_at']}",
        f"- git sha at collection: `{meta['git_sha']}`",
        f"- provider / model: `{meta['provider']}` / `{meta['model']}`",
        f"- n = {n} — EVERY manifest entry; failed entries stay in the denominator",
        f"- n_measured = {stats['n_measured']} · n_failed = {stats['n_failed']}",
        f"- tokens: prompt = {prompt_tokens}, completion = {completion_tokens}, "
        f"total = {prompt_tokens + completion_tokens}",
        f"- provider seconds (sum of per-photo round trips) = "
        f"{stats['round_trip_seconds_total']} · adapter retry count = "
        f"{stats['retry_count_total']}",
        f"- token accounting: {meta['token_accounting']}",
        "",
        "## Matching rules (frozen, mechanically checkable)",
        "",
        "Strings are compared after `.strip().lower()`. `n` is the whole golden manifest.",
        "",
        "- `category`: exact string equality.",
        "- `colors`: **primary-colour hit** — predicted list non-empty AND its first element "
        "occurs in the ground-truth `colors` list. `colors_exact_list` (strict list equality) "
        "is reported alongside as a secondary statistic.",
        "- `pattern`: exact string equality; `unknown` never equals a concrete ground truth.",
        "- `material`: non-empty intersection of predicted and ground-truth lists; an empty "
        "predicted list is a MISS.",
        "- `status=failed`: MISS on all four fields, still counted in `n`.",
        "",
        "Every verdict below is computed from the RAW float `hits/n`, not from the "
        "four-decimal display. The exact fraction and the unrounded float are printed next to "
        "the display value: no metric is rounded up in prose.",
        "",
        "## Verdicts",
        "",
        "| metric | target | hits/n | accuracy (display) | accuracy (raw) | verdict |",
        "|---|---|---|---|---|---|",
    ]
    for key, field in METRIC_KEYS.items():
        accuracy = stats["accuracies"][key]
        hits = stats["fields"][field]["hits"]
        threshold = THRESHOLDS[key]
        meets = accuracy >= threshold
        lines.append(
            f"| {key} | >= {threshold:.2f} | {hits}/{n} | {accuracy:.4f} | "
            f"{accuracy!r} | {'MEETS' if meets else 'DOES NOT MEET'} |"
        )
    lines += [""]
    for key, field in METRIC_KEYS.items():
        accuracy = stats["accuracies"][key]
        hits = stats["fields"][field]["hits"]
        threshold = THRESHOLDS[key]
        meets = accuracy >= threshold
        if meets:
            verdict = "It MEETS the target."
        else:
            verdict = (
                f"It DOES NOT MEET the target; the shortfall is "
                f"{threshold - accuracy:.4f} and is not rounded away."
            )
        lines.append(
            f"- **{key}**: {stats['fields'][field]['hits']}/{n} = {accuracy:.4f} "
            f"against >= {threshold:.2f}. {verdict}"
        )

    lines += [
        "",
        "## Secondary statistics (the friendlier definitions are not quoted alone)",
        "",
        "| statistic | value |",
        "|---|---|",
        f"| `colors_exact_list` accuracy (strict list equality) | "
        f"{stats['colors_exact_list']['hits']}/{n} = "
        f"{stats['colors_exact_list']['accuracy']:.4f} |",
        f"| `material` empty predictions (measured rows with `material=[]`) | "
        f"{stats['material_empty_predictions']} |",
        f"| failed rows with no prediction at all (also misses on all four fields) | "
        f"{stats['n_failed']} |",
        "| per-field hits / misses / empty predictions | "
        + "; ".join(
            f"{field}: {stats['fields'][field]['hits']}/"
            f"{stats['fields'][field]['misses']}/{stats['fields'][field]['empty_predictions']}"
            for field in FIELDS
        )
        + " |",
        "",
        "## Why `material` cannot reach >= 0.80 under the current evidence policy",
        "",
        "`apps/api/schemas.py::ItemAttributes` and "
        "`packages/vision/attrs_adapters/base.py` require `material` to contain ONLY fibres "
        "with visual or care-label evidence; with no such evidence the adapter MUST emit `[]`. "
        "The golden photos are catalogue shots with no legible care labels, so `material=[]` is "
        "the CORRECT output for most of them. An empty list can never intersect a non-empty "
        "ground-truth material list, so the >= 0.80 target is UNREACHABLE under this policy. "
        "This is a consequence of the schema's evidence contract — it is NOT a bug and NOT a "
        "model defect. **This ticket did NOT relax the evidence policy, did NOT widen the "
        "adapter, did NOT change the VLM prompt, and did NOT edit any ground truth to reach "
        "the threshold.** The measured material accuracy above is reported exactly as it is, "
        "with no rounding up.",
        "",
        f"Measured evidence for that statement: {stats['material_empty_predictions']} of {n} "
        "rows emitted an empty material list, and every one of them is a miss under the "
        "intersection rule.",
        "",
        "## Per-entry results (all rows, so the denominator is visible)",
        "",
        "| id | status | category | colours | pattern | material | predicted | seconds | "
        "prompt/completion tokens |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        flags = " | ".join("hit" if row["hits"][field] else "MISS" for field in FIELDS)
        predicted = _md(
            {
                "category": row["predicted"]["category"],
                "colors": row["predicted"]["colors"],
                "pattern": row["predicted"]["pattern"],
                "material": row["predicted"]["material"],
            }
        )
        lines.append(
            f"| {row['id']} | {row['status']} | {flags} | {predicted} | "
            f"{row['round_trip_seconds']:.3f} | "
            f"{row['prompt_tokens']}/{row['completion_tokens']} |"
        )

    primary = sum(1 for item in stats["confusion"] if item["kind"] == "primary")
    secondary = len(stats["confusion"]) - primary
    lines += [
        "",
        "## Confusion list — every miss, verbatim",
        "",
        f"{primary} primary misses + {secondary} `colors_exact_list` secondary-statistic "
        f"misses = {len(stats['confusion'])} rows. Nothing is omitted, no value is rounded, "
        "and no miss is paraphrased.",
        "",
        "| # | id | field | kind | ground truth | predicted | reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(stats["confusion"], 1):
        lines.append(
            f"| {index} | {item['id']} | {item['field']} | {item['kind']} | "
            f"{_md(item['ground_truth'])} | {_md(item['predicted'])} | "
            f"{_md(item['reason'])} |"
        )

    lines += [
        "",
        "## Provenance and the clean-CI caveat",
        "",
        "These numbers come from the locally frozen `evals/golden` set: web-sourced photos, "
        "all rights reserved, deliberately NOT committed to git. `evals/reports/*` is "
        "gitignored, so a clean CI checkout has no `attrs_predictions.json`. There the four "
        "attribute rows in `python -m evals.run --suite vision` are UNMEASURED with an "
        "explicit reason — that is NOT a regression, it is the harness refusing to guess. "
        "Regenerate locally with `python -m evals.tools.collect_attrs` (40 real VLM calls) "
        "and then `python -m evals.metrics.attrs`. The harness itself never calls the network.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.metrics.attrs")
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    if not args.predictions.is_file():
        print(
            f"no predictions artifact at {args.predictions} — run "
            "`python -m evals.tools.collect_attrs` first",
            file=sys.stderr,
        )
        return 2
    try:
        rows, meta = run(args.predictions, args.manifest)
        stats = summarise(rows)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(
            f"attrs metric cannot measure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    text = render(rows, meta, stats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(
        f"wrote {args.out} — n={stats['n']} "
        f"n_failed={stats['n_failed']} "
        f"confusion_rows={len(stats['confusion'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
