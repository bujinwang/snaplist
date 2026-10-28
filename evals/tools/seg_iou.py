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
For every manifest entry: run `segment()` on the photo, binarise both the predicted mask and
the reference mask at 128, and take `intersection / union`. Predictions and references are
both 8-bit single channel, so the comparison is like for like.

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
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image

from packages.vision.runtime import probe
from packages.vision.segment import segment

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN = ROOT / "evals" / "golden-provisional"
DEFAULT_REPORT = ROOT / "evals" / "reports" / "seglab.md"


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _iou(predicted: np.ndarray, reference: np.ndarray) -> float:
    """Intersection over union of two binary masks. Returns 1.0 for two empty masks."""
    pred = predicted >= 128
    ref = reference >= 128
    union = int((pred | ref).sum())
    if union == 0:
        return 1.0
    return float((pred & ref).sum()) / union


def run(golden_dir: Path) -> tuple[list[dict], dict]:
    manifest = json.loads((golden_dir / "manifest.json").read_text())
    runtime = probe()
    rows: list[dict] = []
    model_sha = "unknown"
    for entry in manifest:
        photo_path = golden_dir / entry["path"]
        mask_path = golden_dir / entry["mask_path"]
        result = segment(photo_path.read_bytes(), runtime.backend)
        model_sha = result.meta.notes.get("model_sha256", model_sha)
        with Image.open(io.BytesIO(result.mask_png)) as opened:
            predicted = np.asarray(opened.convert("L"))
        with Image.open(mask_path) as opened:
            reference = np.asarray(opened.convert("L"))
        rows.append(
            {
                "id": entry["id"],
                "category": entry["category"],
                "iou": round(_iou(predicted, reference), 4),
                "seconds": round(result.meta.seconds or 0.0, 3),
                "pred_coverage": round(float((predicted >= 128).mean()), 4),
                "ref_coverage": round(float((reference >= 128).mean()), 4),
                "mask_source": entry.get("mask_source", "unspecified"),
            }
        )
    meta = {
        "backend": str(runtime.backend),
        "device": runtime.device,
        "model_sha256": model_sha,
        "runtime_reason": runtime.reason,
        "n": len(rows),
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


def render(rows: list[dict], meta: dict, stats: dict, golden_dir: Path, threshold: float) -> str:
    sources = sorted({row["mask_source"] for row in rows})
    lines = [
        "# Segmentation report — E1-T4",
        "",
        f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · git `{_git_sha()}` · "
        f"backend `{meta['backend']}` on `{meta['device']}` · model sha256 "
        f"`{(meta['model_sha256'] or 'unknown')[:16]}…`",
        "",
        f"Set: `{golden_dir.relative_to(ROOT) if golden_dir.is_relative_to(ROOT) else golden_dir}`"
        f" — **n = {meta['n']}**.",
        "",
    ]
    if "provisional" in golden_dir.name:
        lines += [
            "> **This is the provisional set, not the E0-T1 golden set.** The masks were made by",
            f"> background-colour arithmetic (`{', '.join(sources)}`), not by hand, and the photos",
            "> were chosen because a colour mask could isolate them. The number below answers",
            "> \"does the pipeline work\", not \"is the model good enough\". The DoD gate",
            f"> (`mean IoU ≥ {threshold}`) is quoted for orientation only; it belongs to the",
            "> golden set with 20 hand masks.",
            "",
        ]
    lines += [
        "| id | category | IoU | pred coverage | ref coverage | seconds |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['category']} | {row['iou']:.4f} | "
            f"{row['pred_coverage']:.3f} | {row['ref_coverage']:.3f} | {row['seconds']:.2f} |"
        )
    lines += [
        "",
        "## Summary",
        "",
        "| metric | value |",
        "|---|---|",
        f"| segmentation mean IoU | **{stats['mean']:.4f}** |",
        f"| median IoU | {stats['median']:.4f} |",
        f"| worst / best | {stats['min']:.4f} / {stats['max']:.4f} |",
        f"| mean per-image segment time | {stats['seconds_mean']:.3f} s |",
        f"| total segment time | {stats['seconds_total']:.2f} s |",
        "",
        "| category | mean IoU |",
        "|---|---|",
    ]
    for name, value in stats["by_category"].items():
        lines.append(f"| {name} | {value:.4f} |")
    verdict = "PASS" if stats["mean"] >= threshold else "BELOW"
    lines += [
        "",
        f"Against the E0-T1 threshold of {threshold}: **{verdict}** — informational on this set, "
        "a gate only on the golden set.",
        "",
    ]
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

    rows, meta = run(args.golden_dir)
    stats = summarise(rows)
    text = render(rows, meta, stats, args.golden_dir, args.threshold)
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
