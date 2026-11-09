"""Tests for `infra/ci/gates/regression_gate.py` — stdlib `unittest`, no model, no network.

Run them with:

    python -m unittest discover -s infra/ci/gates/tests -t infra/ci/gates/tests -v

Every test injects a fake `measure()` callable, so none of them imports the eval harness or
loads ONNX weights. What is being tested is the *decision* — which exit code a given pair of
(baseline, current) dicts produces — because that is the part that turns a green CI run into a
false claim. The four cases the ticket names are all here: no baseline ⇒ 2, a synthetic
regression ⇒ 1, within tolerance ⇒ 0, an UNMEASURED metric ⇒ 2.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

GATES = Path(__file__).resolve().parents[1]
# ADR-010: regression_gate.py now imports evals.mask_provenance at module scope. It is loaded
# here by file path from an arbitrary cwd, so the repo root must be on sys.path BEFORE _load().
# (regression_gate.py also inserts it itself; this keeps the by-path test loading robust.)
if str(GATES.parents[2]) not in sys.path:
    sys.path.insert(0, str(GATES.parents[2]))


def _load(name: str):
    """Import a sibling script by path: `infra/ci/gates` is not an importable package."""
    spec = importlib.util.spec_from_file_location(name, GATES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rg = _load("regression_gate")


def entry(
    value: float,
    *,
    n: int = 10,
    set_name: str = "unit",
    tolerance: float = 0.01,
    provenance: str = "hand masks, drawn by a human",
) -> rg.Entry:
    return rg.Entry(
        value=value, n=n, set_name=set_name, provenance=provenance, tolerance=tolerance
    )


def measure_of(entries: dict, problems: list[str] | None = None):
    """A stand-in for `regression_gate.load_measurements`."""
    return lambda: (dict(entries), list(problems or []))


def run(argv: list[str], measure) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = rg.main(argv, measure=measure)
    return code, buffer.getvalue()


def write_baseline(
    path: Path,
    metrics: dict[str, dict[str, object]],
    *,
    schema_version: int = 1,
    generated_at: str = "2026-11-06T00:00:00+00:00",
    git_sha: str = "0123456789ab",
    sets: dict[str, object] | None = None,
    mask_sources: dict[str, object] | None = None,
) -> Path:
    """A well-formed baseline by default: `sets`/`mask_sources` cover exactly `metrics`.

    `sets` and `mask_sources` are overridable so the coverage check can be tested from the
    outside — a hand-edit that deletes a metric row but leaves those maps behind is the exact
    false green this check exists to catch.
    """
    payload = {
        "schema_version": schema_version,
        "generated_at": generated_at,
        "git_sha": git_sha,
        "sets": (
            {name: str(record["set"]) for name, record in metrics.items()}
            if sets is None
            else sets
        ),
        "mask_sources": (
            {name: [] for name in metrics} if mask_sources is None else mask_sources
        ),
        "metrics": metrics,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def record(
    value: float,
    *,
    n: int = 10,
    set_name: str = "unit",
    tolerance: float = 0.01,
    provenance: str = "hand masks, drawn by a human",
) -> dict[str, object]:
    return {
        "value": value,
        "n": n,
        "set": set_name,
        "provenance": provenance,
        "tolerance": tolerance,
    }


class TmpDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.baseline = self.tmp / "baseline.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()


class ShippedBaselineTest(unittest.TestCase):
    """The decision this ticket made, locked: no baseline is shipped with numbers in it."""

    def test_shipped_baseline_is_explicitly_empty(self) -> None:
        payload = json.loads(rg.DEFAULT_BASELINE.read_text())
        self.assertEqual(
            payload["metrics"],
            {},
            "infra/ci/gates/baseline.json must ship with \"metrics\": {}. Enshrining 0.9299 "
            "segmentation IoU (references are ai:/algorithmic:, not hand masks) or 0.9936 "
            "prettify SSIM (synthetic labels, destruction budget) would turn a self-referential "
            "number into release evidence. Create a baseline with --update-baseline instead.",
        )
        self.assertEqual(payload["schema_version"], rg.SCHEMA_VERSION)
        self.assertTrue(payload["notes"], "the empty baseline must explain why it is empty")

    def test_shipped_baseline_makes_the_gate_inconclusive(self) -> None:
        code, out = run(
            ["--baseline", str(rg.DEFAULT_BASELINE)],
            measure_of({"segmentation mean IoU": entry(0.9299, n=20)}),
        )
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("explicitly empty baseline", out)
        self.assertIn("--update-baseline", out)
        self.assertIn("NOT a pass", out)
        # the empty file is consistently empty: it must hit the empty-baseline reason, not the
        # coverage check that a hand-deleted row trips
        self.assertNotIn("does not describe the same metrics", out)


class NoBaselineTest(TmpDirCase):
    def test_missing_baseline_exits_2_not_0(self) -> None:
        missing = self.tmp / "nope.json"
        code, out = run(
            ["--baseline", str(missing)], measure_of({"a metric": entry(0.9)})
        )
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("baseline file does not exist", out)
        self.assertIn("GATE INCONCLUSIVE", out)

    def test_missing_baseline_never_reports_pass(self) -> None:
        code, out = run(["--baseline", str(self.tmp / "nope.json")], measure_of({}))
        self.assertNotEqual(code, rg.EXIT_PASS)
        self.assertNotIn("GATE PASS", out)

    def test_empty_baseline_file_exits_2(self) -> None:
        write_baseline(self.baseline, {})
        code, out = run(["--baseline", str(self.baseline)], measure_of({"x": entry(1.0)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("nothing to compare", out)

    def test_measured_metric_absent_from_baseline_is_informational_only(self) -> None:
        write_baseline(self.baseline, {"known": record(0.90)})
        code, out = run(
            ["--baseline", str(self.baseline)],
            measure_of({"known": entry(0.90), "brand new": entry(0.5)}),
        )
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertIn("NOT IN BASELINE", out)
        self.assertIn("brand new", out)


class ComparisonTest(TmpDirCase):
    def test_regression_exits_1(self) -> None:
        write_baseline(self.baseline, {"segmentation mean IoU": record(0.9500, tolerance=0.0100)})
        code, out = run(
            ["--baseline", str(self.baseline)],
            measure_of({"segmentation mean IoU": entry(0.9300)}),
        )
        self.assertEqual(code, rg.EXIT_REGRESSION)
        self.assertIn("REGRESSION", out)
        self.assertIn("0.9400", out)  # the threshold it fell below: 0.95 - 0.01

    def test_dip_inside_tolerance_exits_0(self) -> None:
        write_baseline(self.baseline, {"m": record(0.9500, tolerance=0.0100)})
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.9450)}))
        self.assertEqual(code, rg.EXIT_PASS)
        # The table shows the dip and the tolerance it sat inside, in its own columns.
        self.assertIn("-0.0050", out)
        self.assertIn("0.0100", out)
        self.assertNotIn("REGRESSION", out)

    def test_exactly_at_tolerance_is_not_a_regression(self) -> None:
        write_baseline(self.baseline, {"m": record(0.9500, tolerance=0.0100)})
        code, _ = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.9400)}))
        self.assertEqual(code, rg.EXIT_PASS)

    def test_held_value_exits_0(self) -> None:
        write_baseline(self.baseline, {"m": record(0.80)})
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.80)}))
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertIn("1 metric(s) compared", out)

    def test_improvement_exits_0(self) -> None:
        write_baseline(self.baseline, {"m": record(0.80)})
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.91)}))
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertIn("+0.1100", out)
        self.assertNotIn("REGRESSION", out)

    def test_per_metric_tolerance_is_honoured(self) -> None:
        write_baseline(
            self.baseline,
            {"slow": record(0.50, tolerance=0.05), "tight": record(0.50, tolerance=0.01)},
        )
        code, _ = run(
            ["--baseline", str(self.baseline)],
            measure_of({"slow": entry(0.47), "tight": entry(0.495)}),
        )
        self.assertEqual(code, rg.EXIT_PASS)

    def test_cli_tolerance_override_applies_when_baseline_has_none(self) -> None:
        payload = {
            "schema_version": 1,
            "generated_at": "2026-11-06T00:00:00+00:00",
            "git_sha": "0123456789ab",
            "sets": {"m": "unit"},
            "mask_sources": {"m": []},
            "metrics": {"m": {"value": 0.5, "n": 3, "set": "unit", "provenance": "hand"}},
        }
        self.baseline.write_text(json.dumps(payload))
        code, _ = run(
            ["--baseline", str(self.baseline), "--tolerance", "0.05"],
            measure_of({"m": entry(0.48)}),
        )
        self.assertEqual(code, rg.EXIT_PASS)


class UnmeasuredTest(TmpDirCase):
    def test_one_unmeasured_metric_exits_2(self) -> None:
        write_baseline(
            self.baseline,
            {"measured": record(0.90), "gone": record(0.70)},
        )
        code, out = run(
            ["--baseline", str(self.baseline)],
            measure_of(
                {"measured": entry(0.90)},
                ["gone: UNMEASURED — no masked set on disk"],
            ),
        )
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("gone", out)
        self.assertIn("no masked set on disk", out)
        self.assertNotIn("GATE PASS", out)

    def test_nothing_measured_exits_2_even_with_a_full_baseline(self) -> None:
        write_baseline(self.baseline, {"a": record(0.1), "b": record(0.2)})
        code, out = run(["--baseline", str(self.baseline)], measure_of({}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("0 metric(s) compared", out)

    def test_regression_outranks_unmeasured(self) -> None:
        write_baseline(self.baseline, {"dropped": record(0.9), "regressed": record(0.9)})
        code, _ = run(
            ["--baseline", str(self.baseline)],
            measure_of({"regressed": entry(0.10)}),
        )
        self.assertEqual(code, rg.EXIT_REGRESSION)


class BaselineCoverageTest(TmpDirCase):
    """A hand-edited baseline must not be able to gate *fewer* metrics than it describes.

    `sets` and `mask_sources` enumerate the same metric names as `metrics`. Deleting a row from
    `metrics` alone leaves the mismatch visible, so the gate refuses the file instead of
    comparing one metric fewer and printing PASS — the same false green `compared == 0` removes,
    reached from the other side.
    """

    TWO = {
        "segmentation mean IoU": record(0.9299, n=20),
        "prettify SSIM (inside mask)": record(0.9936, n=9),
    }

    def test_deleted_metric_row_is_refused(self) -> None:
        write_baseline(self.baseline, self.TWO)
        payload = json.loads(self.baseline.read_text())
        del payload["metrics"]["prettify SSIM (inside mask)"]  # sets/mask_sources still list it
        self.baseline.write_text(json.dumps(payload, indent=2) + "\n")

        code, out = run(
            ["--baseline", str(self.baseline)],
            measure_of(
                {
                    "segmentation mean IoU": entry(0.9299),
                    "prettify SSIM (inside mask)": entry(0.9936),
                }
            ),
        )
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertNotIn("GATE PASS", out)
        self.assertIn("does not describe the same metrics", out)
        self.assertIn("prettify SSIM (inside mask)", out)
        self.assertIn("--update-baseline", out)
        self.assertIn("baseline REJECTED", out)
        # a rejected file is not "no baseline": the create-a-baseline hint must not appear,
        # because `--update-baseline` answers that file with a refusal
        self.assertNotIn("create a baseline on purpose", out)
        self.assertNotIn("no baselined metric", out)

    def test_extra_key_in_sets_is_refused(self) -> None:
        metrics = {"m": record(0.5)}
        write_baseline(
            self.baseline,
            metrics,
            sets={"m": "unit", "ghost metric": "unit"},
        )
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.5)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("ghost metric", out)

    def test_mask_sources_must_cover_the_same_metrics(self) -> None:
        write_baseline(
            self.baseline,
            {"m": record(0.5)},
            mask_sources={},
        )
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.5)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("mask_sources", out)

    def test_missing_sets_map_is_refused_with_the_fix(self) -> None:
        self.baseline.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": "2026-11-06T00:00:00+00:00",
                    "git_sha": "0123456789ab",
                    "metrics": {"m": record(0.5)},
                }
            )
        )
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.5)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("sets", out)
        self.assertIn("--update-baseline", out)

    def test_matching_coverage_compares_normally(self) -> None:
        write_baseline(self.baseline, self.TWO)
        code, out = run(
            ["--baseline", str(self.baseline)],
            measure_of(
                {
                    "segmentation mean IoU": entry(0.9299),
                    "prettify SSIM (inside mask)": entry(0.9936),
                }
            ),
        )
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertIn("2 metric(s) compared", out)

    def test_matching_coverage_still_reports_a_regression(self) -> None:
        write_baseline(self.baseline, self.TWO)
        code, _ = run(
            ["--baseline", str(self.baseline)],
            measure_of(
                {
                    "segmentation mean IoU": entry(0.10),
                    "prettify SSIM (inside mask)": entry(0.9936),
                }
            ),
        )
        self.assertEqual(code, rg.EXIT_REGRESSION)


class MalformedBaselineTest(TmpDirCase):
    def test_wrong_schema_version_exits_2(self) -> None:
        write_baseline(self.baseline, {"m": record(0.5)}, schema_version=99)
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.5)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("schema_version", out)

    def test_invalid_json_exits_2(self) -> None:
        self.baseline.write_text("{not json")
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.5)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("not valid JSON", out)

    def test_metric_missing_provenance_exits_2(self) -> None:
        payload = {
            "schema_version": 1,
            "generated_at": "2026-11-06T00:00:00+00:00",
            "git_sha": "0123456789ab",
            "sets": {"m": "unit"},
            "mask_sources": {"m": []},
            "metrics": {"m": {"value": 0.5, "n": 1, "set": "unit"}},
        }
        self.baseline.write_text(json.dumps(payload))
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.5)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("provenance", out)

    def test_negative_tolerance_exits_2(self) -> None:
        write_baseline(self.baseline, {"m": record(0.5, tolerance=-0.1)})
        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.5)}))
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("negative", out)


class UpdateBaselineTest(TmpDirCase):
    def test_refuses_when_nothing_was_measured(self) -> None:
        code, out = run(
            ["--baseline", str(self.baseline), "--update-baseline"], measure_of({})
        )
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("measured nothing", out)
        self.assertFalse(self.baseline.exists())

    def test_refuses_machine_references_by_default(self) -> None:
        measure = measure_of(
            {
                "segmentation mean IoU": entry(
                    0.9299,
                    n=20,
                    set_name="golden",
                    provenance=(
                        "backend u2net on cpu, mask source: ai:google-ai-mode-nano-banana, "
                        "algorithmic:matting; references are not hand-drawn"
                    ),
                )
            }
        )
        code, out = run(["--baseline", str(self.baseline), "--update-baseline"], measure)
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("not hand-made", out)
        self.assertFalse(self.baseline.exists(), "a refused update must not leave a file behind")

    def test_writes_when_machine_references_are_acknowledged(self) -> None:
        measure = measure_of(
            {
                "segmentation mean IoU": entry(
                    0.9299,
                    n=20,
                    set_name="golden",
                    provenance=(
                        "backend u2net on cpu, mask source: ai:google-ai-mode-nano-banana, "
                        "algorithmic:matting; references are not hand-drawn"
                    ),
                )
            }
        )
        code, out = run(
            [
                "--baseline",
                str(self.baseline),
                "--update-baseline",
                "--allow-machine-references",
                "--now",
                "2026-11-06T12:00:00+00:00",
            ],
            measure,
        )
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertTrue(self.baseline.exists())
        payload = json.loads(self.baseline.read_text())
        self.assertEqual(payload["schema_version"], rg.SCHEMA_VERSION)
        self.assertEqual(payload["generated_at"], "2026-11-06T12:00:00+00:00")
        self.assertTrue(payload["git_sha"])
        self.assertEqual(payload["sets"], {"segmentation mean IoU": "golden"})
        self.assertEqual(
            payload["mask_sources"],
            {
                "segmentation mean IoU": [
                    "ai:google-ai-mode-nano-banana",
                    "algorithmic:matting",
                ]
            },
        )
        self.assertAlmostEqual(payload["metrics"]["segmentation mean IoU"]["value"], 0.9299)
        self.assertEqual(payload["metrics"]["segmentation mean IoU"]["n"], 20)

    def test_written_baseline_is_then_used_for_a_real_comparison(self) -> None:
        write_measure = measure_of({"m": entry(0.75, set_name="hand", n=5)})
        code, _ = run(["--baseline", str(self.baseline), "--update-baseline"], write_measure)
        self.assertEqual(code, rg.EXIT_PASS)

        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.74)}))
        self.assertEqual(code, rg.EXIT_PASS, "0.01 dip with the default 0.01 tolerance")
        self.assertIn("1 metric(s) compared", out)

        code, out = run(["--baseline", str(self.baseline)], measure_of({"m": entry(0.60)}))
        self.assertEqual(code, rg.EXIT_REGRESSION)

    def test_refuses_to_drop_a_baselined_metric(self) -> None:
        write_baseline(self.baseline, {"kept": record(0.9), "transient": record(0.5)})
        measure = measure_of({"kept": entry(0.9)})
        code, out = run(["--baseline", str(self.baseline), "--update-baseline"], measure)
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("could not measure", out)
        self.assertIn("transient", out)

        code, out = run(
            [
                "--baseline",
                str(self.baseline),
                "--update-baseline",
                "--allow-dropped-metrics",
            ],
            measure,
        )
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertEqual(list(json.loads(self.baseline.read_text())["metrics"]), ["kept"])

    def test_refuses_to_rebuild_over_a_baseline_that_failed_validation(self) -> None:
        write_baseline(self.baseline, {"kept": record(0.9), "dropped": record(0.5)})
        payload = json.loads(self.baseline.read_text())
        del payload["metrics"]["dropped"]  # sets/mask_sources still list it -> rejected file
        tampered = json.dumps(payload, indent=2) + "\n"
        self.baseline.write_text(tampered)

        code, out = run(
            [
                "--baseline",
                str(self.baseline),
                "--update-baseline",
                "--allow-machine-references",
            ],
            measure_of({"kept": entry(0.9)}),
        )
        self.assertEqual(code, rg.EXIT_INCONCLUSIVE)
        self.assertIn("failed validation", out)
        self.assertIn("--allow-dropped-metrics", out)
        self.assertEqual(self.baseline.read_text(), tampered, "a refusal must not rewrite the file")

    def test_update_fills_the_shipped_empty_placeholder(self) -> None:
        # the delivered placeholder is empty-but-valid, which must stay a one-command path;
        # the file is copied into a temp dir so the repo's baseline.json is never written here
        shipped = rg.DEFAULT_BASELINE.read_text()
        self.baseline.write_text(shipped)
        self.assertIn('"metrics": {}', shipped)

        code, out = run(
            [
                "--baseline",
                str(self.baseline),
                "--update-baseline",
                "--allow-machine-references",
            ],
            measure_of({"m": entry(0.75, set_name="hand", n=5)}),
        )
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertIn("BASELINE WRITTEN", out)
        self.assertEqual(list(json.loads(self.baseline.read_text())["metrics"]), ["m"])

    def test_reports_git_sha_timestamp_and_mask_sources_in_its_output(self) -> None:
        measure = measure_of({"m": entry(0.6, set_name="fixtures/prettify", n=9)})
        code, out = run(
            [
                "--baseline",
                str(self.baseline),
                "--update-baseline",
                "--now",
                "2026-01-02T03:04:05+00:00",
            ],
            measure,
        )
        self.assertEqual(code, rg.EXIT_PASS)
        self.assertIn("generated_at=2026-01-02T03:04:05+00:00", out)
        self.assertIn("git_sha=", out)
        self.assertIn("BASELINE WRITTEN", out)


class HelperTest(unittest.TestCase):
    def test_mask_sources_are_derived_from_the_provenance_note(self) -> None:
        measured = entry(
            0.9,
            provenance=(
                "backend u2net on cpu, mask source: ai:google, algorithmic:matting; "
                "median 0.9"
            ),
        )
        self.assertEqual(rg.mask_sources(measured), ["ai:google", "algorithmic:matting"])

    def test_mask_sources_are_empty_when_the_note_names_none(self) -> None:
        self.assertEqual(rg.mask_sources(entry(0.9, provenance="hand masks, drawn by a human")), [])

    def test_machine_reference_detection(self) -> None:
        self.assertTrue(
            rg.has_machine_references(
                entry(0.9, provenance="references are not hand-drawn (algorithmic:matting)")
            )
        )
        self.assertTrue(rg.has_machine_references(entry(0.9, set_name="golden-provisional")))
        self.assertFalse(rg.has_machine_references(entry(0.9, set_name="hand-masks")))

    def test_resolve_git_sha_is_a_sha_or_the_word_unknown(self) -> None:
        sha = rg.resolve_git_sha()
        self.assertTrue(sha == "unknown" or len(sha) == 12, sha)

    def test_markers_are_the_shared_module_object(self) -> None:
        """ADR-010: no local marker tuple; the gate and the classifier cannot drift apart."""
        from evals import mask_provenance as mp

        self.assertIs(rg.MACHINE_REFERENCE_MARKERS, mp.MACHINE_REFERENCE_MARKERS)
        self.assertIn("ai:", rg.MACHINE_REFERENCE_MARKERS)
        self.assertIn("ai-assisted:", rg.MACHINE_REFERENCE_MARKERS)

    def test_ai_assisted_tier_is_not_silently_baselined(self) -> None:
        """D3: `ai:` does not match `ai-assisted:` — the new marker is load-bearing."""
        from evals import mask_provenance as mp

        value = "ai-assisted:sam2+point-reviewed"
        self.assertTrue(mp.is_machine_reference(value))
        measured = entry(0.93, provenance=f"mask source: {value}; median 0.98")
        self.assertTrue(rg.has_machine_references(measured))
        self.assertEqual(rg.mask_sources(measured), [value])


if __name__ == "__main__":
    unittest.main()
