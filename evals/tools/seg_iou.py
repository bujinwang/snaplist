"""E1-T4 (first half) — segmentation IoU against a masked set, reported, not gated.

    python -m evals.tools.seg_iou --golden-dir evals/golden-provisional
    python -m evals.tools.seg_iou --golden-dir evals/golden --fail-under 0.92

Why this is a separate tool and not a row in `evals/run.py`: the harness owns suites and
thresholds (E0-T2), and a metric that appears there has to be a gate. This one has a real
denominator for the first time — but that denominator is the **provisional** set, so it
prints numbers and provenance instead of pretending to be a release gate. Wiring the number
into `evals/run.py` belongs to the commit that also has Bert's hand masks, at which point
`--fail-under` becomes the default rather than a flag.

What it measures
----------------
For every manifest entry **that has a mask**: run `segment()` on the photo, binarise both the
predicted mask and the reference mask at 128, and take `intersection / union`. Predictions and
references are both 8-bit single channel, so the comparison is like for like.

Which entries it can measure
----------------------------
`mask_path` is `null` on the 20 entries of `evals/golden/` that have never been hand-masked
(E0-T1: 40 photos, 20 masks, the rest `null` by contract). An IoU needs a reference, so those
entries are skipped and *named in the report*, never scored against a guessed file name: a
missing mask means "there is no reference for this photo", not "the mask is at
`masks/<id>.png`". Deriving the path from the id would invent a denominator out of a naming
convention, and a denominator nobody agreed on turns a wrong mean into a number that looks
measured. The mean is over the masked entries, and every report states how many that is.

What it refuses to hide
-----------------------
Every report states, in this order: which set it ran on, how many entries, how the masks were
made, which backend and device produced the predictions, the model file's sha256, and the git
SHA. A number without those is not reproducible, and an IoU measured against algorithmic
masks is not the DoD number — the report says both, in prose, above the table.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from evals import git_sha
from evals import mask_provenance as mp
from evals.mask_provenance import ReviewState
from packages.vision.runtime import probe
from packages.vision.segment import segment

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN = ROOT / "evals" / "golden-provisional"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "seglab.md"

#: Sceptical default when no register state is supplied to `render()`. ADR-010: a renderer
#: must never read the register itself; it only consumes this immutable summary.
DEFAULT_REVIEW_STATE = ReviewState(0, 20, False, "UNSET")


def current_review_state() -> ReviewState:
    """The register's present-tense state, or the sceptical default if it cannot be read.

    ADR-010: `main()` is the ONLY injection point — it passes this summary to `render()`.
    `evals/run.py::_measure_segmentation()` never calls `render()` and never injects a state;
    it writes the three-tier caveat straight into `Measurement.note`. Other `render()` callers
    (the 5-positional-argument test) fall back to `DEFAULT_REVIEW_STATE`. Any failure here
    also falls back, so a report stays printable on a broken checkout.
    """
    try:
        from evals.tools import validate_point_reviews as validator

        return validator.validate_structure(validator.load_register()).as_state()
    except Exception:  # noqa: BLE001 - a broken register must not stop a report
        return DEFAULT_REVIEW_STATE


def _git_sha() -> str:
    """Kept as a thin alias so the report code reads the same as it always has."""
    return git_sha()


def _iou(predicted: np.ndarray, reference: np.ndarray) -> float:
    """Intersection over union of two binary masks. Returns 1.0 for two empty masks."""
    pred = predicted >= 128
    ref = reference >= 128
    union = int((pred | ref).sum())
    if union == 0:
        return 1.0
    return float((pred & ref).sum()) / union


def _split_by_mask(manifest: list[dict]) -> tuple[list[dict], list[str]]:
    """Split a manifest into the entries an IoU is defined for, and the ids of the rest.

    The rule is exactly `validate_golden.py`'s: a usable `mask_path` is a non-empty string.
    Everything else — `null`, a missing key, an empty or whitespace string — is "this photo has
    no reference mask", and the second list is what the report prints so the denominator is
    never a mystery.

    Args:
        manifest: The parsed `manifest.json`, a JSON array of entry objects.

    Returns:
        `(scorable, unscored)`: the entries with a non-empty `mask_path`, and the ids of those
        without one (in manifest order, `"<no id>"` for an entry that has no `id` at all).
    """
    scorable: list[dict] = []
    unscored: list[str] = []
    for entry in manifest:
        if not isinstance(entry, dict):
            unscored.append("<not an object>")
            continue
        mask_rel = entry.get("mask_path")
        if isinstance(mask_rel, str) and mask_rel.strip():
            scorable.append(entry)
        else:
            unscored.append(str(entry.get("id", "<no id>")))
    return scorable, unscored


def run(golden_dir: Path) -> tuple[list[dict], dict]:
    """Score every entry that has a reference mask and summarise the run's provenance.

    Args:
        golden_dir: Directory holding `manifest.json`, `photos/` and `masks/`.

    Returns:
        `(rows, meta)`: one row per scored entry, and the provenance block the report prints.

    Raises:
        ValueError: the manifest is not a JSON array, or no entry carries a mask (a mean over
            zero fixtures is not a measurement, and printing `0.0` for it would be a lie of
            exactly the kind this harness exists to avoid).
        FileNotFoundError: an entry names a mask that is not on disk.
    """
    manifest_path = golden_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, list):
        raise ValueError(
            f"{manifest_path} must be a JSON array of entries, not {type(manifest).__name__}"
        )

    scorable, unscored = _split_by_mask(manifest)
    if not scorable:
        raise ValueError(
            f"no entry in {manifest_path} carries a mask_path, so there is no reference to score "
            f"against ({len(manifest)} entries, all without a mask)"
        )

    runtime = probe()
    rows: list[dict] = []
    model_sha = "unknown"
    for entry in scorable:
        entry_id = str(entry.get("id", "<no id>"))
        mask_rel = str(entry["mask_path"]).strip()
        photo_path = golden_dir / entry["path"]
        mask_path = golden_dir / mask_rel
        if not mask_path.is_file():
            raise FileNotFoundError(
                f"{entry_id}: mask_path {mask_rel!r} is in the manifest but {mask_path} "
                "does not exist"
            )
        result = segment(photo_path.read_bytes(), runtime.backend)
        model_sha = result.meta.notes.get("model_sha256", model_sha)
        with Image.open(io.BytesIO(result.mask_png)) as opened:
            predicted = np.asarray(opened.convert("L"))
        with Image.open(mask_path) as opened:
            reference = np.asarray(opened.convert("L"))
        if predicted.shape != reference.shape:
            # Broadcasting two mask sizes would either crash deep inside numpy or, worse,
            # compare the wrong pixels. Both masks are resized to the photo by the pipeline;
            # a mismatch means one of them is not what the manifest claims.
            raise ValueError(
                f"{entry_id}: prediction is {predicted.shape} but the reference mask "
                f"{mask_rel!r} is {reference.shape} — IoU is only defined on two masks of the "
                "same size"
            )
        rows.append(
            {
                "id": entry_id,
                "category": entry["category"],
                "iou": round(_iou(predicted, reference), 4),
                "seconds": round(result.meta.seconds or 0.0, 3),
                "pred_coverage": round(float((predicted >= 128).mean()), 4),
                "ref_coverage": round(float((reference >= 128).mean()), 4),
                "mask_source": entry.get("mask_source", "unspecified"),
                "mask": mask_rel,
            }
        )
    meta = {
        "backend": str(runtime.backend),
        "device": runtime.device,
        "model_sha256": model_sha,
        "runtime_reason": runtime.reason,
        "n": len(rows),
        "manifest_entries": len(manifest),
        "unscored": unscored,
    }
    return rows, meta


def summarise(rows: list[dict]) -> dict:
    ious = [row["iou"] for row in rows]
    by_category: dict[str, list[float]] = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row["iou"])
    return {
        "mean": round(statistics.fmean(ious), 4) if ious else 0.0,
        "median": round(statistics.median(ious), 4) if ious else 0.0,
        "min": round(min(ious), 4) if ious else 0.0,
        "max": round(max(ious), 4) if ious else 0.0,
        "by_category": {
            name: round(statistics.fmean(values), 4) for name, values in sorted(by_category.items())
        },
        "seconds_total": round(sum(row["seconds"] for row in rows), 2),
        "seconds_mean": round(statistics.fmean([row["seconds"] for row in rows]), 3)
        if rows
        else 0.0,
    }


def _is_hand_made(source: str) -> bool:
    """Whether a `mask_source` claims a human drew the mask (ADR-010 tier HAND).

    Delegates to `evals.mask_provenance.is_hand_made` so this report, `evals/run.py`,
    `validate_golden.py` and the regression gate can never disagree. The `hand` prefix keeps
    its old meaning; the AI-assisted and machine tiers are deliberately NOT hand.
    """
    return mp.is_hand_made(source)


def _source_counts(rows: list[dict]) -> Counter:
    """How many scored entries each `mask_source` accounts for."""
    return Counter(str(row.get("mask_source", "unspecified")) for row in rows)


def _describe_sources(counts: Counter, names: list[str]) -> str:
    """`name` and its entry count, for every source in `names`, in the order given."""
    return ", ".join(f"`{name}` ({counts[name]})" for name in names)


def render(
    rows: list[dict],
    meta: dict,
    stats: dict,
    golden_dir: Path,
    threshold: float,
    *,
    review_state: ReviewState | None = None,
) -> str:
    """Render the report. `review_state` is the ONLY evidence input: no register, no judging.

    The five positional arguments are unchanged for existing callers; when `review_state` is
    omitted the sceptical default (`0/20`, U1 UNMET, threshold UNSET) applies.
    """
    state = review_state or DEFAULT_REVIEW_STATE
    counts = _source_counts(rows)
    sources = sorted(counts)
    hand_sources = [
        name for name in sources
        if mp.classify(name).tier is mp.MaskSourceTier.HAND
    ]
    ai_assisted_sources = [
        name for name in sources
        if mp.classify(name).tier is mp.MaskSourceTier.AI_ASSISTED_REVIEWED
    ]
    machine_sources = [
        name for name in sources
        if mp.classify(name).tier is mp.MaskSourceTier.MACHINE
    ]
    manifest_entries = int(meta.get("manifest_entries", len(rows)))
    unscored = [str(entry_id) for entry_id in meta.get("unscored", [])]
    lines = [
        "# Segmentation report — E1-T4",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · git `{_git_sha()}` · "
        f"backend `{meta['backend']}` on `{meta['device']}` · model sha256 "
        f"`{(meta['model_sha256'] or 'unknown')[:16]}…`",
        "",
        f"Set: `{golden_dir.relative_to(ROOT) if golden_dir.is_relative_to(ROOT) else golden_dir}`"
        f" — **{manifest_entries} entries, n = {meta['n']} with a mask**.",
        "",
    ]
    # What the references are made of is a property of the *masks*, not of the directory name:
    # a set can drift from algorithmic to hand-drawn one file at a time, and a report that
    # decided its honesty from a path substring would keep saying "provisional" afterwards — or,
    # as `evals/golden` did until this line existed, say nothing at all.
    tier_parts: list[str] = []
    if hand_sources:
        tier_parts.append(f"hand-drawn: {_describe_sources(counts, hand_sources)}")
    if ai_assisted_sources:
        tier_parts.append(
            f"AI-assisted point-reviewed: {_describe_sources(counts, ai_assisted_sources)}"
        )
    if machine_sources:
        tier_parts.append(f"machine-made: {_describe_sources(counts, machine_sources)}")
    if not tier_parts:
        mask_line = "no scored entries"
    elif len(tier_parts) > 1:
        mask_line = "; ".join(tier_parts) + " — **mixed set**"
    elif machine_sources:
        mask_line = f"{tier_parts[0]} — **not hand-drawn**"
    elif hand_sources:
        mask_line = f"{tier_parts[0]} — **hand-drawn**"
    else:
        mask_line = f"{tier_parts[0]} — **AI-assisted point-reviewed**"
    lines += [f"Masks: {mask_line}.", ""]

    if "provisional" in golden_dir.name:
        lines += [
            "> **This is the provisional set, not the E0-T1 golden set.** The masks were made by",
            f"> background-colour arithmetic (`{', '.join(sources)}`), not by hand, and the photos",
            "> were chosen because a colour mask could isolate them. The number below answers",
            "> \"does the pipeline work\", not \"is the model good enough\". The DoD gate",
            f"> (`mean IoU ≥ {threshold}`) is quoted for orientation only; it belongs to the",
            "> golden set with 20 masks that satisfy ADR-010.",
            "",
        ]
    elif machine_sources:
        lines += [
            f"> **The reference masks are not hand-drawn:** "
            f"{_describe_sources(counts, machine_sources)}.",
            "> A machine-made reference only measures agreement with a machine, so the number",
            "> below is a pipeline check, **not the E1-T4 DoD number**: the hand-mask gate",
            f"> (`mean IoU ≥ {threshold}`) stays open until these photos carry hand-drawn masks,",
            "> and the comparison at the end of this report is orientation only.",
            "",
        ]
    elif ai_assisted_sources or hand_sources:
        described = _describe_sources(counts, ai_assisted_sources or hand_sources)
        tier_word = "AI-assisted point-reviewed" if ai_assisted_sources else "hand-drawn"
        lines += [
            f"> **Reference masks are {tier_word}:** {described}.",
            "> The relaxed-DoD decision is the \"source field + evidence record\" two-condition",
            "> check in `python -m evals.tools.validate_point_reviews` (ADR-010). For this run the",
            f"> register is {state.reviewed}/{state.slots} point-reviewed (U1 UNMET), so the",
            "> hand-mask gate stays open. ADR-010 does not pre-adjudicate the IoU semantics after",
            "> the 20 masks are re-made; that is a follow-up ticket.",
            "",
        ]
    if unscored:
        # The denominator is the number the reader cannot check for themselves, so it is stated
        # twice: as a count above, and as the list of entries that are not in it.
        lines += [
            f"> **{len(unscored)} entries carry no mask (`mask_path: null`) and are not scored:**",
            f"> {', '.join(f'`{entry_id}`' for entry_id in unscored)}.",
            "> E0-T1's shape is 40 photos and 20 masks, so `null` on half a manifest is the",
            "> contract, not a broken file: it means no reference exists for that photo, and",
            "> deriving a path from the id would silently move the mean. The figures below cover",
            f"> the {meta['n']} entries that do carry a mask; the `Masks:` line above says what",
            "> those references are made of.",
            "",
        ]
    lines += [
        "| id | category | IoU | pred coverage | ref coverage | seconds | mask |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['category']} | {row['iou']:.4f} | "
            f"{row['pred_coverage']:.3f} | {row['ref_coverage']:.3f} | {row['seconds']:.2f} | "
            f"`{row['mask']}` |"
        )
    lines += [
        "",
        "## Summary",
        "",
        f"All figures below are over the {meta['n']} masked entries.",
        "",
        "| metric | value |",
        "|---|---|",
        f"| segmentation mean IoU | **{stats['mean']:.4f}** |",
        f"| median IoU | {stats['median']:.4f} |",
        f"| worst / best | {stats['min']:.4f} / {stats['max']:.4f} |",
        f"| masked entries scored (n) | {meta['n']} |",
        f"| entries without a mask (not scored) | {len(unscored)} |",
        f"| mean per-image segment time | {stats['seconds_mean']:.3f} s |",
        f"| total segment time | {stats['seconds_total']:.2f} s |",
        "",
        "| category | mean IoU |",
        "|---|---|",
    ]
    for name, value in stats["by_category"].items():
        lines.append(f"| {name} | {value:.4f} |")
    comparison = "≥" if stats["mean"] >= threshold else "<"

    def machine_verdict() -> str:
        """The machine-tier sentence, byte-for-byte unchanged (ADR-010 / Bert ruling 5)."""
        return (
            f"Against the E0-T1 threshold of {threshold}: {stats['mean']:.4f} {comparison} "
            f"{threshold} on non-hand references — **not a PASS**, and not the E1-T4 DoD "
            "number; the hand-mask gate stays open."
        )

    if not state.u1_met:
        # The **not a PASS** verdict is grounded in the PRESENT-TENSE register state, not in
        # the mask-source tier. ADR-010 deliberately does not pre-adjudicate the future.
        lines += [
            "",
            f"Register state: {state.reviewed}/{state.slots} point-reviewed; U1 is UNMET; "
            f"edit-distance threshold is {state.threshold_status}. The **not a PASS** verdict "
            "below follows from this present-tense register state, not from the mask-source tier.",
        ]
        if machine_sources:
            verdict = machine_verdict()
        elif ai_assisted_sources:
            verdict = (
                f"Against the E0-T1 threshold of {threshold}: {stats['mean']:.4f} "
                f"{comparison} {threshold} on AI-assisted point-reviewed references — "
                "**not a PASS** (see register state above). The relaxed-DoD decision is the "
                "\"source field + evidence record\" two-condition check in "
                "python -m evals.tools.validate_point_reviews (ADR-010); the hand-mask gate "
                "stays open for this run."
            )
        else:
            verdict = (
                f"Against the E0-T1 threshold of {threshold}: {stats['mean']:.4f} "
                f"{comparison} {threshold} on hand-drawn references — **not a PASS** "
                "(see register state above). The relaxed-DoD decision is likewise the "
                "two-condition check in validate_point_reviews; the hand-mask gate stays open "
                "for this run."
            )
    elif machine_sources:
        # Original `:330-334` branch, unchanged: a machine-made reference cannot clear the
        # hand-mask line, only approach it.
        verdict = machine_verdict()
    else:
        # Reachable on purpose: ADR-010 does not pre-adjudicate the IoU semantics of a fully
        # registered set. A follow-up ticket owns that decision.
        outcome = "PASS" if stats["mean"] >= threshold else "BELOW"
        verdict = (
            f"Against the E0-T1 threshold of {threshold}: **{outcome}** "
            f"({stats['mean']:.4f} {comparison} {threshold}) — informational on this set, a gate "
            "only on the golden set. DoD semantics for a fully registered set are defined by a "
            "follow-up ticket; ADR-010 does not pre-adjudicate them."
        )
    lines += ["", verdict, ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.tools.seg_iou")
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit 1 when the mean IoU is below this. Omitted by default: this reports, "
        "it does not gate.",
    )
    parser.add_argument("--threshold", type=float, default=0.92, help="DoD line to quote")
    args = parser.parse_args(argv)

    if not (args.golden_dir / "manifest.json").is_file():
        print(f"no manifest.json in {args.golden_dir}", file=sys.stderr)
        return 2

    try:
        rows, meta = run(args.golden_dir)
    except (ValueError, FileNotFoundError) as exc:
        # A set that cannot be scored is a tool failure, not a zero: exit 2 with the reason
        # rather than printing a report whose mean came from no fixtures at all.
        print(f"cannot measure {args.golden_dir}: {exc}", file=sys.stderr)
        return 2
    stats = summarise(rows)
    # ADR-010: main() owns the register read; render() receives only the immutable summary.
    text = render(
        rows,
        meta,
        stats,
        args.golden_dir,
        args.threshold,
        review_state=current_review_state(),
    )
    print(text)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text)
    print(f"\nwrote {args.report}")

    if args.fail_under is not None and stats["mean"] < args.fail_under:
        print(
            f"FAIL: mean IoU {stats['mean']:.4f} < {args.fail_under:.2f}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
