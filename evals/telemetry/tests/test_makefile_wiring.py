# ruff: noqa: F811
# `tmp_path` is imported to register the shim-safe pytest fixture defined in fixtures.py; each
# test's parameter of the same name is the injected value, not a redefinition.
"""Makefile wiring for the E0-T5 telemetry gate.

E0-T5 was delivered under a whitelist that forbade touching `Makefile` and `evals/run.py`, so
`make evals` / `make evals-strict` still invoked the vision harness (`evals.run`), not the gate.
That gap was named residual **R4** (docs/QA.md §10) and is closed by the follow-up ticket that
wires both targets to `python -m evals.telemetry`:

  make evals        -> python -m evals.telemetry --since 7d --gate --allow-inconclusive
  make evals-strict -> python -m evals.telemetry --since 7d --gate

This module proves that wiring with a REAL subprocess (never by grepping Makefile text):
`make -n evals-strict` resolves to a recipe containing `evals.telemetry` and not `evals.run`,
and the gate itself returns exit 0 on a complete 30-item baseline. The baseline run pins `--now`
so the assertion is date-independent — the 7-day window is half-open, and an unpinned run would
drift out of window on a later date.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from evals.telemetry.tests import fixtures as fx
from evals.telemetry.tests.fixtures import tmp_path  # noqa: F401 - pytest fixture


def test_make_evals_strict_is_wired_to_the_telemetry_gate(tmp_path: Path) -> None:
    """Real subprocess proof: `make evals-strict` resolves to, and runs, the telemetry gate."""
    # 1) Wiring: the resolved recipe invokes the telemetry module, not the vision harness.
    dry = subprocess.run(
        ["make", "-n", "evals-strict"],
        cwd=fx.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "NO_COLOR": "1"},
        check=True,
    )
    assert "evals.telemetry" in dry.stdout
    assert "evals.run" not in dry.stdout

    # 2) Behaviour: the wired gate passes on a complete baseline (pinned now, date-independent).
    path = tmp_path / "dev.db"
    db = fx.make_db(path)
    fx.populate_baseline(db)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert "SnapList telemetry gate" in result.stdout


def test_the_delivered_gate_is_the_telemetry_module_not_the_vision_harness(
    tmp_path: Path,
) -> None:
    """`python -m evals.telemetry` is the only E0-T5 gate evidence."""
    path = tmp_path / "dev.db"
    db = fx.make_db(path)
    fx.populate_baseline(db)
    fx.close(db)
    result = fx.run_cli(path, "--since", "7d", "--gate")
    assert result.returncode == 0
    assert result.stdout.startswith("SnapList telemetry gate — E0-T5")
    assert "VERDICTS: PASS=8" in result.stdout
    assert "# eval report" not in result.stdout, "the vision harness is not gate evidence"
