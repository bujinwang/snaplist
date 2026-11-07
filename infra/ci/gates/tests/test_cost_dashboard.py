"""Tests for `infra/ci/gates/cost_dashboard.py` — stdlib `unittest`, no network.

Run them with:

    python -m unittest discover -s infra/ci/gates/tests -t infra/ci/gates/tests -v

The point of these tests is the honesty contract, not the formatting: a missing report must
produce a table full of `UNMEASURED` (never a number), a partial report must produce an
`UNMEASURED` **total** (a partial sum is not a per-item cost), and `PASS` must be unreachable
until every stage in the total carries a measured number.
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

GATES = Path(__file__).resolve().parents[1]


def _load(name: str):
    """Import a sibling script by path: `infra/ci/gates` is not an importable package."""
    spec = importlib.util.spec_from_file_location(name, GATES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cd = _load("cost_dashboard")

COMPLETE = """
# cost report — E1+E2+E3, measured 2026-11-06

| stage | $/item | n | notes |
|---|---|---|---|
| E1 segmentation | $0.0012 | 20 | u2net on cpu, billed rate |
| E2 rectification + prettify | $0.0008 | 20 | |
| E3 attributes + dedupe | `0.0004` | 20 | |
"""

MISSING_E2 = """
| stage | $/item | n |
|---|---|---|
| E1 | 0.0012 | 20 |
| E3 | 0.0004 | 20 |
"""

OVER_BUDGET = """
| stage | $/item | n |
|---|---|---|
| E1 | 0.0200 | 20 |
| E2 | 0.0200 | 20 |
| E3 | 0.0200 | 20 |
"""

UNMEASURED_CELL = """
| stage | $/item | n |
|---|---|---|
| E1 | 0.0012 | 20 |
| E2 | UNMEASURED | 20 |
| E3 | — | — |
"""

WITH_E4 = """
| stage | $/item | n |
|---|---|---|
| E1 | 0.0012 | 20 |
| E2 | 0.0008 | 20 |
| E3 | 0.0004 | 20 |
| E4 on-model (per render) | 0.0800 | 5 |
"""


class TmpDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.report = self.tmp / "cost.md"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_report(self, text: str) -> Path:
        self.report.write_text(text)
        return self.report


def run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = cd.main(argv)
    return code, buffer.getvalue()


class AbsentReportTest(TmpDirCase):
    def test_absent_report_renders_unmeasured_rows_and_exits_0(self) -> None:
        code, out = run(["--report", str(self.tmp / "absent.md")])
        self.assertEqual(code, cd.EXIT_OK)
        self.assertIn("UNMEASURED", out)
        self.assertIn("E1+E2+E3 total: UNMEASURED", out)
        self.assertIn("does not exist", out)
        self.assertIn("UNREACHABLE", out)
        self.assertNotIn("PASS —", out)

    def test_absent_report_exits_2_with_fail_on_unmeasured(self) -> None:
        code, out = run(["--report", str(self.tmp / "absent.md"), "--fail-on-unmeasured"])
        self.assertEqual(code, cd.EXIT_NOT_EVALUABLE)
        self.assertIn("COST BUDGET NOT EVALUATED", out)
        self.assertIn("NOT a pass", out)

    def test_every_stage_row_is_unmeasured(self) -> None:
        dash = cd.build_dashboard(self.tmp / "absent.md")
        self.assertEqual(dash.report_state, "ABSENT")
        self.assertEqual([row.stage.key for row in dash.rows], ["E1", "E2", "E3", "E4"])
        self.assertTrue(all(row.cost is None for row in dash.rows))
        self.assertIsNone(dash.total)
        self.assertEqual(dash.total_status, "UNMEASURED")
        self.assertTrue(dash.has_unmeasured)

    def test_present_but_empty_report_is_still_unmeasured(self) -> None:
        self.write_report("# cost report\n\nNothing has been billed yet.\n")
        dash = cd.build_dashboard(self.report)
        self.assertEqual(dash.report_state, "PRESENT")
        self.assertIsNone(dash.total)
        self.assertIn("no $/item table found", " ".join(dash.problems))


class CompleteReportTest(TmpDirCase):
    def test_complete_report_computes_total_and_verdict(self) -> None:
        report = self.write_report(COMPLETE)
        dash = cd.build_dashboard(report)
        self.assertAlmostEqual(dash.total or -1, 0.0024, places=6)
        self.assertEqual(dash.total_status, "MEASURED")
        self.assertIn("PASS", dash.verdict)
        self.assertIn("$0.0024/item", dash.verdict)
        self.assertIn("<= $0.03/item", dash.verdict)

    def test_complete_report_exits_0_even_with_fail_on_unmeasured(self) -> None:
        report = self.write_report(COMPLETE)
        code, out = run(["--report", str(report), "--fail-on-unmeasured"])
        self.assertEqual(code, cd.EXIT_OK)
        self.assertIn("PASS", out)
        self.assertNotIn("NOT EVALUATED", out)

    def test_sample_counts_are_carried_through(self) -> None:
        report = self.write_report(COMPLETE)
        dash = cd.build_dashboard(report)
        by_key = {row.stage.key: row for row in dash.rows}
        self.assertEqual(by_key["E1"].n, 20)
        table = " ".join(cd.table_lines(dash))
        self.assertIn("| 20 |", table)
        self.assertIn("| 20+ |", table, "the total row reports the smallest per-stage sample count")


class OverBudgetTest(TmpDirCase):
    def test_over_budget_verdict_is_fail_and_exit_1_with_flag(self) -> None:
        report = self.write_report(OVER_BUDGET)
        dash = cd.build_dashboard(report)
        self.assertAlmostEqual(dash.total or -1, 0.06, places=6)
        self.assertIn("FAIL", dash.verdict)
        self.assertTrue(dash.over_budget)

        code, out = run(["--report", str(report), "--fail-on-unmeasured"])
        self.assertEqual(code, cd.EXIT_OVER_BUDGET)
        self.assertIn("COST BUDGET BREACHED", out)
        self.assertIn("0.0600", out)

    def test_over_budget_report_mode_is_still_just_a_report(self) -> None:
        report = self.write_report(OVER_BUDGET)
        code, out = run(["--report", str(report)])
        self.assertEqual(code, cd.EXIT_OK)
        self.assertIn("FAIL", out)


class IncompleteReportTest(TmpDirCase):
    def test_missing_stage_leaves_the_total_unmeasured(self) -> None:
        report = self.write_report(MISSING_E2)
        dash = cd.build_dashboard(report)
        self.assertIsNone(dash.total)
        self.assertEqual(dash.total_status, "UNMEASURED")
        self.assertIn("E2", dash.total_reason)
        self.assertIn("partial sum is not a per-item cost", dash.total_reason)
        self.assertNotIn("PASS", dash.verdict)
        self.assertIn("UNREACHABLE", dash.verdict)

    def test_missing_stage_exits_2_with_fail_on_unmeasured(self) -> None:
        report = self.write_report(MISSING_E2)
        code, _ = run(["--report", str(report), "--fail-on-unmeasured"])
        self.assertEqual(code, cd.EXIT_NOT_EVALUABLE)

    def test_unparsable_cell_is_unmeasured_not_zero(self) -> None:
        report = self.write_report(UNMEASURED_CELL)
        dash = cd.build_dashboard(report)
        by_key = {row.stage.key: row for row in dash.rows}
        self.assertIsNone(by_key["E2"].cost)
        self.assertIsNone(by_key["E3"].cost)
        self.assertIsNone(dash.total)
        self.assertIn("not a number", by_key["E2"].reason)

    def test_measured_stages_keep_their_numbers_next_to_the_hole(self) -> None:
        report = self.write_report(UNMEASURED_CELL)
        dash = cd.build_dashboard(report)
        by_key = {row.stage.key: row for row in dash.rows}
        self.assertAlmostEqual(by_key["E1"].cost or -1, 0.0012, places=6)
        self.assertEqual(by_key["E1"].status, "MEASURED")
        self.assertEqual(by_key["E2"].status, "UNMEASURED")


class ParsingTest(TmpDirCase):
    def test_stage_names_and_keys_both_parse(self) -> None:
        self.assertEqual(cd.normalise_stage("E2"), "E2")
        self.assertEqual(cd.normalise_stage("`e3`"), "E3")
        self.assertEqual(cd.normalise_stage("E4 on-model render"), "E4")
        self.assertEqual(cd.normalise_stage("prettify"), "E2")
        self.assertEqual(cd.normalise_stage("rectify + prettify"), "E2")
        self.assertEqual(cd.normalise_stage("segmentation (E1)"), "E1")
        self.assertIsNone(cd.normalise_stage("misc"))

    def test_cost_cell_variants(self) -> None:
        self.assertAlmostEqual(cd.parse_cost("$0.0042") or -1, 0.0042, places=6)
        self.assertAlmostEqual(cd.parse_cost("`0.0042`") or -1, 0.0042, places=6)
        self.assertAlmostEqual(cd.parse_cost("0.0042 (±0.0003)") or -1, 0.0042, places=6)
        self.assertAlmostEqual(cd.parse_cost("$1,234.5678") or -1, 1234.5678, places=4)
        for not_a_number in ("", "—", "-", "n/a", "UNMEASURED", "tbd"):
            self.assertIsNone(cd.parse_cost(not_a_number), not_a_number)

    def test_e4_is_reported_but_excluded_from_the_total(self) -> None:
        report = self.write_report(WITH_E4)
        dash = cd.build_dashboard(report)
        by_key = {row.stage.key: row for row in dash.rows}
        self.assertAlmostEqual(by_key["E4"].cost or -1, 0.08, places=6)
        self.assertAlmostEqual(dash.total or -1, 0.0024, places=6)

    def test_a_table_without_a_cost_column_contributes_nothing(self) -> None:
        report = self.write_report(
            "| stage | seconds |\n|---|---|\n| E1 | 12.5 |\n"
        )
        dash = cd.build_dashboard(report)
        self.assertIsNone(dash.total)
        self.assertIn("no $/item table found", " ".join(dash.problems))

    def test_unknown_stage_row_is_ignored_with_a_note(self) -> None:
        report = self.write_report(
            "| stage | $/item | n |\n|---|---|---|\n| misc | 0.5 | 2 |\n"
        )
        dash = cd.build_dashboard(report)
        self.assertIn("ignored a row", " ".join(dash.problems))
        self.assertIsNone(dash.total)

    def test_conflicting_duplicate_rows_keep_the_first_and_record_it(self) -> None:
        report = self.write_report(
            "| stage | $/item | n |\n"
            "|---|---|---|\n"
            "| E1 | 0.0010 | 20 |\n"
            "| E1 | 0.0090 | 20 |\n"
        )
        parsed, problems = cd.parse_report(report.read_text())
        self.assertAlmostEqual(parsed["E1"].cost or -1, 0.0010, places=6)
        self.assertTrue(any("conflicting rows" in problem for problem in problems))

    def test_an_unmeasured_row_then_a_measured_row_keeps_the_measured_one(self) -> None:
        parsed, _ = cd.parse_report(
            "| stage | $/item | n |\n"
            "|---|---|---|\n"
            "| E1 | UNMEASURED | 20 |\n"
            "| E1 | 0.0010 | 20 |\n"
        )
        self.assertAlmostEqual(parsed["E1"].cost or -1, 0.0010, places=6)


class UpdateDocTest(TmpDirCase):
    def doc(self) -> Path:
        path = self.tmp / "QA.md"
        path.write_text(
            "# QA\n\n## 13. E0-T4 cost dashboard\n\n"
            f"{cd.DOC_START}\nold table\n{cd.DOC_END}\n\n"
            "## 14. Handoff state\n\nprose that must survive\n"
        )
        return path

    def test_update_doc_replaces_only_the_marked_block(self) -> None:
        doc = self.doc()
        report = self.write_report(COMPLETE)
        code, out = run(
            [
                "--report",
                str(report),
                "--doc",
                str(doc),
                "--update-doc",
                "--now",
                "2026-11-06T12:00:00+00:00",
            ]
        )
        self.assertEqual(code, cd.EXIT_OK)
        self.assertIn("§13 block replaced", out)
        body = doc.read_text()
        self.assertNotIn("old table", body)
        self.assertIn("| stage | what it covers | $/item | samples | status |", body)
        self.assertIn("2026-11-06T12:00:00+00:00", body)
        self.assertIn("## 14. Handoff state", body)
        self.assertIn("prose that must survive", body)
        self.assertEqual(body.count(cd.DOC_START), 1)
        self.assertEqual(body.count(cd.DOC_END), 1)

    def test_update_doc_on_absent_report_writes_the_unmeasured_table(self) -> None:
        doc = self.doc()
        code, _ = run(
            [
                "--report",
                str(self.tmp / "absent.md"),
                "--doc",
                str(doc),
                "--update-doc",
                "--now",
                "2026-11-06T12:00:00+00:00",
            ]
        )
        self.assertEqual(code, cd.EXIT_OK)
        body = doc.read_text()
        self.assertIn("UNMEASURED", body)
        self.assertIn("UNREACHABLE", body)
        self.assertIn("does not exist", body)

    def test_missing_markers_is_a_hard_refusal(self) -> None:
        path = self.tmp / "QA.md"
        original = "# QA\n\nno markers here\n"
        path.write_text(original)
        report = self.write_report(COMPLETE)
        code, out = run(
            ["--report", str(report), "--doc", str(path), "--update-doc"]
        )
        self.assertEqual(code, cd.EXIT_NOT_EVALUABLE)
        self.assertIn("refusing to write", out)
        self.assertEqual(path.read_text(), original, "a refusal must not touch the document")

    def test_absent_doc_is_a_hard_refusal(self) -> None:
        report = self.write_report(COMPLETE)
        code, out = run(
            [
                "--report",
                str(report),
                "--doc",
                str(self.tmp / "nope.md"),
                "--update-doc",
            ]
        )
        self.assertEqual(code, cd.EXIT_NOT_EVALUABLE)
        self.assertIn("does not exist", out)

    def test_without_update_doc_the_document_is_untouched(self) -> None:
        doc = self.doc()
        before = doc.read_text()
        report = self.write_report(COMPLETE)
        code, _ = run(["--report", str(report), "--doc", str(doc)])
        self.assertEqual(code, cd.EXIT_OK)
        self.assertEqual(doc.read_text(), before)


class RenderTest(TmpDirCase):
    def test_console_render_lists_the_reason_for_every_hole(self) -> None:
        report = self.write_report(UNMEASURED_CELL)
        dash = cd.build_dashboard(report)
        text = cd.render(dash)
        self.assertIn("Why the UNMEASURED cells are UNMEASURED:", text)
        self.assertIn("E2:", text)
        self.assertIn("E1+E2+E3 total: UNMEASURED", text)
        self.assertIn("verdict: UNMEASURED", text)

    def test_no_fabricated_number_appears_when_the_report_is_absent(self) -> None:
        dash = cd.build_dashboard(self.tmp / "absent.md")
        text = cd.render(dash)
        self.assertNotIn("PASS —", text)
        # The only dollar figure allowed in the output is the *target*, quoted from the ticket.
        self.assertEqual(
            set(re.findall(r"\$\d+(?:\.\d+)?", text)),
            {"$0.03"},
            "an absent report must not produce a cost number anywhere in the output",
        )


if __name__ == "__main__":
    unittest.main()
