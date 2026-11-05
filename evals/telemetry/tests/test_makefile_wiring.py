# ruff: noqa: F811
# `tmp_path` is imported to register the shim-safe pytest fixture defined in fixtures.py;
# each test's parameter of the same name is the injected value, not a redefinition.
"""Makefile wiring is a NAMED RESIDUAL, outside E0-T5's allowed paths.

Bert ruling 2 forbids touching `Makefile` and `evals/run.py`. The two `make` targets still run
(`make evals` / `make evals-strict`) but their output is **vision harness output, not gate
evidence** — the transcripts are pasted into `docs/QA.md` with exactly that label. The gate
delivered by this ticket is `python -m evals.telemetry`.

The `xfail(strict=True)` test below is a TRIPWIRE, not a correctness assertion about Makefile
text: it asserts a real subprocess exit code. Today `make evals-strict` calls the vision harness
and refuses with exit 2, so it xfails. The moment somebody wires the target to the telemetry
gate the assertion XPASSes, `strict=True` turns that into a FAILURE, and the engineer must
delete the marker and re-review the wiring. Nothing in this file asserts SQL/Makefile text.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from evals.telemetry.tests import fixtures as fx
from evals.telemetry.tests.fixtures import tmp_path  # noqa: F401 - pytest fixture

TRIPWIRE_REASON = (
    "Makefile -> python -m evals.telemetry wiring is a named E0-T5 residual (out of allowed "
    "paths). When it lands this XPASS fails under strict=True and forces the marker to be "
    "removed and the wiring reviewed."
)


@pytest.mark.xfail(strict=True, reason=TRIPWIRE_REASON)
def test_make_evals_strict_wiring_tripwire(tmp_path: Path) -> None:
    """Real subprocess exit code: 0 once `evals-strict` is wired to the telemetry gate."""
    path = tmp_path / "dev.db"
    db = fx.make_db(path)
    fx.populate_baseline(db)
    fx.close(db)
    child_env = {**os.environ, "SNAPLIST_DB": str(path), "NO_COLOR": "1"}
    result = subprocess.run(
        ["make", "evals-strict"],
        cwd=fx.REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=child_env,
        check=False,
    )
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
