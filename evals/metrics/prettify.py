"""E2-T2 — the `prettify SSIM (inside mask)` measurement, plus the mask audit that backs it.

    python -m evals.metrics.prettify                      # table to stdout
    python -m evals.metrics.prettify --fixture-dir …      # another fixture set

Consumed by `evals/run.py::measurements()`, which prints the number in
`evals/reports/vision.md`; the *report file* for this epic (`evals/reports/prettify.md`) and
the manual hallucination rubric belong to E2-T3 and are not written here.

## Read this before quoting the number

**SSIM here measures how much the stage destroyed, not how well it cleaned.** The two images
compared are:

    a = the input flat-lay          (evals/fixtures/prettify/flat/<id>.png)
    b = the prettify() output for that same input, mode=CLASSICAL
    region = the garment, alpha >= 128

So `SSIM = 1.0000` means *nothing changed* — a pass-through scores a perfect 1.0 — and a lower
number means the fill moved more pixels further. It is a **destruction budget**, and the DoD's
`>= 0.80` is a ceiling on how much damage the non-generative fill may do, not a floor on
quality. Nothing in this file measures "are the creases gone": that needs the manual rubric
(E2-T3), which is why the rubric row stays `UNMEASURED` in the report with that reason attached.

## The mask audit travels with the number

A pretty SSIM next to a mask that had eaten the logo would be a green light for the exact
failure the DoD forbids, so every row also reports, from the *same* mask the fill consumed
(`packages.vision.prettify.wrinkle_mask_for`, not a re-implementation):

* `protect_overlap_px` — final wrinkle-mask pixels inside the case's ground-truth
  `protect_box`. Must be 0 on all 5 logo fixtures; it is asserted in
  `packages/vision/tests/test_prettify.py` and reported here so a regression is visible in the
  artifact and not only in a red test.
* `mask_coverage` vs `coverage_cap` — the rail on how much of the garment may be repainted.
* `crease_hit` — of the garment interior inside the case's ground-truth `crease_box`, the
  fraction the mask actually selected. This is the only "did it fire at all" evidence the
  material supports: catalogue photography is pressed, so crease *recall* cannot be measured
  on it (see `evals/fixtures/prettify/README.md`).

## SSIM lives next door, and is re-exported from here

`scikit-image` is not a dependency (`docs/DEPS.md`) and adding it for one metric would put a
15 MB wheel behind a number the harness must be able to print on a cold machine. The Wang et
al. implementation — 11×11 uniform window, `C1 = (0.01·L)²`, `C2 = (0.03·L)²`, integral-image
window means — now lives in `evals/metrics/ssim.py`, because `evals/metrics/rectify.py` needs
the same `ssim_map` for ADR-011's alpha-weighted region and the two metrics must not drift
apart. Every name this module used to define (`SSIM_*`, `ssim_map`, `ssim_inside_mask`,
`_luminance`) is re-exported below unchanged, so callers and
`evals/tests/test_prettify_metric.py` import exactly what they always did. **The move changed
no number**: `mean SSIM (n=9) = 0.9936` before the extraction and after it.

The region rule in *this* file stays boolean (`alpha >= 128`) on purpose. ADR-011 §2.2 argues
that the rectify number must be alpha-*weighted* instead; the two rules differ because they
make different claims, and `ssim.py` keeps both functions side by side so that claim is
checkable rather than tribal.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from evals.metrics.ssim import (
    SSIM_DATA_RANGE,
    SSIM_K1,
    SSIM_K2,
    SSIM_WINDOW,
    _luminance,
    ssim_inside_mask,
    ssim_map,
)
from packages.vision.prettify import (
    WRINKLE_COVERAGE_CAP,
    PrettifyBackendUnavailable,
    prettify,
    wrinkle_mask_for,
)
from packages.vision.types import PrettifyMode

# The seven names above are **re-exports**, not imports: `evals/metrics/ssim.py` was extracted
# out of this module, and callers (including `evals/tests/test_prettify_metric.py`, whose import
# block must keep working byte-for-byte) still write `from evals.metrics.prettify import
# ssim_inside_mask`. `__all__` is what tells ruff these imports are the point of the module
# rather than dead code, and it is why no `as`-alias dance is needed.
__all__ = [
    "DEFAULT_FIXTURE_DIR",
    "SSIM_DATA_RANGE",
    "SSIM_K1",
    "SSIM_K2",
    "SSIM_WINDOW",
    "_luminance",
    "main",
    "render",
    "run",
    "ssim_inside_mask",
    "ssim_map",
    "summarise",
]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE_DIR = ROOT / "evals" / "fixtures" / "prettify"


# ------------------------------------------------------------------------------- helpers
def _decode(payload: bytes, what: str) -> np.ndarray:
    with Image.open(io.BytesIO(payload)) as opened:
        mode = "RGB" if what == "rgb" else "L"
        return np.asarray(opened.convert(mode))


def _box_region(box: list[int], shape: tuple[int, int]) -> np.ndarray:
    """A ground-truth rectangle as a boolean region, clipped to the frame."""
    height, width = shape
    x, y, box_w, box_h = (int(value) for value in box)
    region = np.zeros((height, width), dtype=bool)
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + box_w, width), min(y + box_h, height)
    if x1 > x0 and y1 > y0:
        region[y0:y1, x0:x1] = True
    return region


# ---------------------------------------------------------------------------------- run


def run(fixture_dir: Path) -> tuple[list[dict], dict]:
    """Measure every fixture case. Returns per-case rows and the run's provenance.

    Raises on a broken fixture (missing file, no manifest) rather than skipping: a metric that
    quietly drops a case has a different denominator from the one it reports.
    """
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    cases = manifest["cases"] if isinstance(manifest, dict) else manifest
    rows: list[dict] = []
    rectify_seconds: list[tuple[str, float]] = []
    rectify_notes: list[str] = []

    for case in cases:
        started = time.perf_counter()
        flat_bytes = (fixture_dir / case["flat"]).read_bytes()
        alpha_bytes = (fixture_dir / case["alpha"]).read_bytes()
        protect_box = case.get("protect_box")
        protect = [tuple(protect_box)] if protect_box else None

        result = prettify(flat_bytes, alpha_bytes, mode=PrettifyMode.CLASSICAL, protect=protect)
        elapsed = time.perf_counter() - started
        audit = wrinkle_mask_for(flat_bytes, alpha_bytes, protect=protect)

        before = _decode(flat_bytes, "rgb")
        after = _decode(result.prettified_png, "rgb")
        alpha = _decode(alpha_bytes, "l")
        garment = alpha >= 128
        ssim = ssim_inside_mask(_luminance(before), _luminance(after), garment)
        # Mean |ΔL| in 8-bit levels, inside the mask the fill actually consumed. Reported
        # alongside SSIM on purpose: SSIM 0.99 with |ΔL| = 0.02 would mean "nothing happened",
        # and only the pair distinguishes a subtle correction from a no-op.
        delta = np.abs(_luminance(before) - _luminance(after))
        in_mask = audit.final_mask
        mean_delta_in_mask = (
            round(float(delta[in_mask].mean()), 3) if bool(in_mask.any()) else None
        )

        row = {
            "id": case["id"],
            "kind": case.get("kind", "unspecified"),
            "ssim": round(ssim, 4) if np.isfinite(ssim) else None,
            "mean_abs_delta_in_mask": mean_delta_in_mask,
            "mean_abs_delta_garment": round(float(delta[garment].mean()), 3),
            "seconds": round(elapsed, 3),
            "stage_seconds": round(float(result.meta.seconds or 0.0), 3),
            "backend": str(result.meta.backend),
            "device": result.meta.device,
            "mode": result.meta.notes.get("mode"),
            "mask_coverage": audit.notes["wrinkle_mask"]["coverage"],
            "candidate_coverage": audit.notes["wrinkle_mask"]["candidate_coverage"],
            "coverage_cap": WRINKLE_COVERAGE_CAP,
            "cap_bound": audit.notes["wrinkle_mask"]["cap_bound"],
            "garment_px": int(garment.sum()),
            "changed_px": int(result.meta.notes["fill"]["pixels_changed"]),
            "white_balance": audit.notes["white_balance"],
            "protect_overlap_px": None,
            "auto_overlap_px": None,
            "auto_ink_overlap_px": None,
            "protect_share": round(
                float((audit.protect_mask & garment).sum()) / max(int(garment.sum()), 1), 4
            ),
            "auto_protect_px": audit.notes["protect"]["auto_px"],
            "crease_hit": None,
        }
        if protect_box:
            region = _box_region(protect_box, garment.shape)
            row["protect_overlap_px"] = int((audit.final_mask & region).sum())
            # The honest number next to the 0 above: with **no caller box** — the real pipeline
            # path — how much of the label rectangle does the automatic detector miss?
            auto_only = wrinkle_mask_for(flat_bytes, alpha_bytes)
            row["auto_overlap_px"] = int((auto_only.final_mask & region).sum())
            if case.get("ink"):
                ink = _decode((fixture_dir / case["ink"]).read_bytes(), "l") > 127
                row["auto_ink_overlap_px"] = int((auto_only.final_mask & ink).sum())
        if case.get("crease_box"):
            region = _box_region(case["crease_box"], garment.shape) & audit.interior
            pool = int(region.sum())
            row["crease_hit"] = (
                round(float((audit.final_mask & region).sum()) / pool, 4) if pool else None
            )
        rectify_inputs = case.get("source", {}).get("rectify_inputs")
        if rectify_inputs:
            timing, reason = _time_rectify(rectify_inputs)
            if timing is not None:
                rectify_seconds.append(timing)
            elif reason:
                rectify_notes.append(f"{case['id']}: rectify timing unavailable — {reason}")
        rows.append(row)

    seconds_by_backend: dict[str, dict[str, float]] = {}
    for row in rows:
        bucket = seconds_by_backend.setdefault(row["backend"], {"n": 0.0, "total": 0.0})
        bucket["n"] += 1
        bucket["total"] += row["seconds"]

    rectify_by_backend: dict[str, dict[str, float]] = {}
    for backend, seconds in rectify_seconds:
        bucket = rectify_by_backend.setdefault(backend, {"n": 0.0, "total": 0.0})
        bucket["n"] += 1
        bucket["total"] += seconds

    meta = {
        "n": len(rows),
        "fixture_dir": str(fixture_dir),
        "modes": sorted({str(row["mode"]) for row in rows}),
        "backends": sorted({row["backend"] for row in rows}),
        "devices": sorted({row["device"] for row in rows}),
        "seconds_by_backend": {
            name: {
                "n": int(bucket["n"]),
                "seconds_total": round(bucket["total"], 3),
                "seconds_mean": round(bucket["total"] / bucket["n"], 3) if bucket["n"] else None,
            }
            for name, bucket in sorted(seconds_by_backend.items())
        },
        # PLAN E2-T2 asks for the wall clock of the **rectify+prettify path**, per backend.
        # The fixture is already flat-laid, so the rectify half is timed on the photo+mask this
        # fixture was derived from (recorded in `source.rectify_inputs` by build_fixtures.py).
        "rectify_seconds_by_backend": {
            name: {
                "n": int(bucket["n"]),
                "seconds_total": round(bucket["total"], 3),
                "seconds_mean": round(bucket["total"] / bucket["n"], 3) if bucket["n"] else None,
            }
            for name, bucket in sorted(rectify_by_backend.items())
        },
        "honesty": manifest.get("honesty", "") if isinstance(manifest, dict) else "",
        # Present only when a rectify timing could not be taken. Surfaced, never swallowed: a
        # number that quietly disappears is the failure mode this harness exists to prevent.
        "rectify_notes": rectify_notes,
    }
    return rows, meta


def _time_rectify(inputs: dict) -> tuple[tuple[str, float] | None, str | None]:
    """Time `rectify()` on the source photo + alpha this fixture was built from.

    Returns `((backend, seconds), None)` or `(None, reason)`. **It never raises**: the rectify
    timing is a supplementary number (PLAN E2-T2 asks for the path's wall clock per backend),
    and a manifest entry pointing at a photo that is not on this machine must cost that one
    number — not the prettify SSIM row that the whole report is about. QA caught this: the
    docstring promised it and the code raised `FileNotFoundError`, which turned the row into
    `UNMEASURED`.
    """
    try:
        from packages.vision.rectify import rectify  # local: keeps import cost off --help

        photo = (ROOT / inputs["photo"]).read_bytes()
        alpha = (ROOT / inputs["alpha"]).read_bytes()
        result = rectify(photo, alpha, inputs.get("category", "other"))
    except Exception as exc:  # noqa: BLE001 - any failure here is "this number is unavailable"
        return None, f"{type(exc).__name__}: {exc}"
    return (str(result.meta.backend), float(result.meta.seconds or 0.0)), None


def summarise(rows: list[dict]) -> dict:
    """Aggregates. `ssim` here is a destruction budget — see the module docstring."""
    values = [row["ssim"] for row in rows if row["ssim"] is not None]
    overlaps = [row["protect_overlap_px"] for row in rows if row["protect_overlap_px"] is not None]
    # `auto_overlap_px` is the number that keeps the 0 above honest: with no caller box the
    # automatic detector does not reach every pixel of the label rectangle, and that residual
    # must be visible in the artifact rather than only in a test.
    auto_overlaps = [row["auto_overlap_px"] for row in rows if row["auto_overlap_px"] is not None]
    auto_inks = [
        row["auto_ink_overlap_px"] for row in rows if row["auto_ink_overlap_px"] is not None
    ]
    hits = [row["crease_hit"] for row in rows if row["crease_hit"] is not None]
    # `is not None`, not truthiness: a case whose mean |ΔL| is exactly 0.0 is a *measurement*
    # (the fill changed nothing there), and dropping it changes the denominator while the
    # report keeps quoting the old n. This is the same rule `run()`'s docstring states.
    deltas = [
        row["mean_abs_delta_in_mask"] for row in rows if row["mean_abs_delta_in_mask"] is not None
    ]
    seconds = [row["seconds"] for row in rows]
    shares = [row["protect_share"] for row in rows if row.get("protect_share") is not None]
    return {
        "mean": round(float(np.mean(values)), 4) if values else None,
        "median": round(float(np.median(values)), 4) if values else None,
        "min": round(float(np.min(values)), 4) if values else None,
        "max": round(float(np.max(values)), 4) if values else None,
        "n_ssim": len(values),
        "mean_abs_delta_in_mask": round(float(np.mean(deltas)), 3) if deltas else None,
        "n_delta": len(deltas),
        "mask_coverage_max": max((row["mask_coverage"] for row in rows), default=0.0),
        "coverage_cap": WRINKLE_COVERAGE_CAP,
        "protect_overlap_total": int(sum(overlaps)),
        "protect_cases": len(overlaps),
        "protect_clean": bool(overlaps) and all(value == 0 for value in overlaps),
        "auto_overlap_total": int(sum(auto_overlaps)) if auto_overlaps else None,
        "auto_overlap_max": max(auto_overlaps) if auto_overlaps else None,
        "auto_ink_overlap_total": int(sum(auto_inks)) if auto_inks else None,
        "auto_cases": len(auto_overlaps),
        "protect_share_mean": round(float(np.mean(shares)), 4) if shares else None,
        "protect_share_max": round(float(np.max(shares)), 4) if shares else None,
        "crease_hit_mean": round(float(np.mean(hits)), 4) if hits else None,
        "crease_cases": len(hits),
        "seconds_total": round(sum(seconds), 3),
        "seconds_mean": round(sum(seconds) / len(seconds), 3) if seconds else None,
        "cap_bound_cases": sum(1 for row in rows if row["cap_bound"]),
    }


def render(rows: list[dict], meta: dict, stats: dict, threshold: float = 0.80) -> str:
    """Plain text table. The report file for this epic is E2-T3's; this prints to stdout."""
    backend_line = "; ".join(
        f"{name}: n={info['n']} total={info['seconds_total']:.2f}s "
        f"mean={info['seconds_mean']:.3f}s"
        for name, info in meta["seconds_by_backend"].items()
    )
    # PLAN E2-T2 asks for the wall clock of the rectify+prettify path, per backend, so the
    # report prints both halves rather than the prettify half alone.
    rectify_line = "; ".join(
        f"{name}: n={info['n']} total={info['seconds_total']:.2f}s "
        f"mean={info['seconds_mean']:.3f}s"
        for name, info in meta.get("rectify_seconds_by_backend", {}).items()
    )
    lines = [
        "# prettify measurement — E2-T2",
        "",
        f"fixtures: n={meta['n']} · mode(s): {', '.join(meta['modes'])} · "
        f"canvas backend(s): {', '.join(meta['backends'])} on {', '.join(meta['devices'])}",
        f"wall clock, prettify, by backend — {backend_line}",
        f"wall clock, rectify (the other half of the path), by backend — {rectify_line or 'n/a'}",
        "",
    ]
    if meta.get("rectify_notes"):
        lines += [f"rectify timing incomplete — {note}" for note in meta["rectify_notes"]]
        lines.append("")
    lines += [
        "SSIM compares the **prettify output against its own input**, over the garment mask "
        "(alpha >= 128): 1.0 means nothing changed, below 1.0 means pixels moved. It is a "
        "destruction budget, not a cleaning score.",
        "",
        "`protect overlap px` is with the caller box passed (0 = the DoD line). "
        "`auto only px` is the same count with **no caller box** — the honest number for the "
        "real pipeline path, where nobody hands the stage a rectangle.",
        "",
        "| id | kind | SSIM (destruction) | mean |ΔL| in mask | mask cov | cap "
        "| protect overlap px | auto only px | crease hit | changed px | seconds |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        ssim = "UNMEASURED" if row["ssim"] is None else f"{row['ssim']:.4f}"
        delta = (
            "n/a"
            if row["mean_abs_delta_in_mask"] is None
            else f"{row['mean_abs_delta_in_mask']:.2f}"
        )
        overlap = "n/a" if row["protect_overlap_px"] is None else str(row["protect_overlap_px"])
        auto = "n/a" if row["auto_overlap_px"] is None else str(row["auto_overlap_px"])
        hit = "n/a" if row["crease_hit"] is None else f"{row['crease_hit']:.3f}"
        lines.append(
            f"| {row['id']} | {row['kind']} | {ssim} | {delta} | {row['mask_coverage']:.3f} | "
            f"{row['coverage_cap']:.2f} | {overlap} | {auto} | {hit} | {row['changed_px']} | "
            f"{row['seconds']:.3f} |"
        )
    lines += [
        "",
        "| summary | value |",
        "|---|---|",
        f"| mean SSIM (n={stats['n_ssim']}) | {stats['mean']} |",
        f"| median / worst / best | {stats['median']} / {stats['min']} / {stats['max']} |",
        f"| mean |ΔL| inside the mask (8-bit levels, n={stats['n_delta']}) | "
        f"{stats['mean_abs_delta_in_mask']} |",
        f"| mask coverage max (cap {stats['coverage_cap']}) | {stats['mask_coverage_max']} |",
        f"| cases where the cap bound | {stats['cap_bound_cases']} |",
        f"| protection-overlap total over {stats['protect_cases']} logo cases (caller box) | "
        f"{stats['protect_overlap_total']} px |",
        f"| automatic-detector-only overlap over {stats['auto_cases']} logo cases | "
        f"{stats['auto_overlap_total']} px (worst {stats['auto_overlap_max']}) |",
        f"| ...of which printed-ink pixels (ground truth `ink/*.png`) | "
        f"{stats['auto_ink_overlap_total']} px |",
        f"| protection region share of garment (mean / max) | "
        f"{stats['protect_share_mean']} / {stats['protect_share_max']} |",
        f"| mean crease hit over {stats['crease_cases']} crease cases | "
        f"{stats['crease_hit_mean']} |",
        f"| wall clock total / mean | {stats['seconds_total']:.2f} s / "
        f"{stats['seconds_mean']:.3f} s |",
        "",
        f"DoD threshold for this row: SSIM >= {threshold:.2f} (a floor on SSIM = a ceiling on "
        "destruction).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.metrics.prettify")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--threshold", type=float, default=0.80, help="DoD line to quote")
    args = parser.parse_args(argv)

    if not (args.fixture_dir / "manifest.json").is_file():
        print(f"no manifest.json in {args.fixture_dir}", file=sys.stderr)
        return 2
    try:
        rows, meta = run(args.fixture_dir)
    except PrettifyBackendUnavailable as exc:
        print(f"PRETTIFY UNAVAILABLE: {exc}", file=sys.stderr)
        return 2
    stats = summarise(rows)
    print(render(rows, meta, stats, args.threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
