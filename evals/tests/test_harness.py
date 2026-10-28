"""Smoke tests for the eval harness itself: it must run, and it must never invent numbers."""

from pathlib import Path

from evals import UNMEASURED
from evals.run import TARGETS, golden_state, main, render


def test_report_marks_everything_unmeasured() -> None:
    report = render("all")
    assert report.count(UNMEASURED) >= len(TARGETS)


def test_every_target_has_a_threshold_and_an_epic() -> None:
    for t in TARGETS:
        assert t.threshold, t
        assert t.epic.startswith("E"), t


def test_main_writes_a_report_and_exits_zero(monkeypatch) -> None:
    import tempfile

    import evals.run as run_mod

    # Deliberately not pytest's tmp_path: the pytest tmp root collides with the sandbox on
    # some machines, and the harness must not depend on the runner's scratch layout.
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(run_mod, "REPORTS", Path(tmp))
        assert main(["--suite", "vision"]) == 0
        written = Path(tmp) / "vision.md"
        assert written.exists()
        assert "UNMEASURED" in written.read_text()


def test_dev_gate_prints_the_banner_and_exits_zero(monkeypatch, capsys) -> None:
    """--allow-inconclusive is a dev gate: exit 0, but the output must say it is not a pass."""
    import tempfile

    import evals.run as run_mod

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(run_mod, "REPORTS", Path(tmp))
        assert main(["--suite", "vision", "--allow-inconclusive"]) == 0
    out = capsys.readouterr().out
    assert "GATE NOT EVALUATED — n=0. This is NOT a pass." in out


def test_gate_mode_refuses_without_measurement(monkeypatch, capsys) -> None:
    """The release gate must fail loudly: there is no measurement to release against."""
    import tempfile

    import evals.run as run_mod

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(run_mod, "REPORTS", Path(tmp))
        assert main(["--suite", "vision", "--gate"]) == 2
    assert "RELEASE GATE NOT AVAILABLE" in capsys.readouterr().err


def test_pure_report_mode_keeps_the_terminal_clean(monkeypatch, capsys) -> None:
    """Report-only is not a gate: no banner on the terminal..."""
    import tempfile

    import evals.run as run_mod

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(run_mod, "REPORTS", Path(tmp))
        assert main(["--suite", "vision"]) == 0
    assert "GATE NOT EVALUATED" not in capsys.readouterr().out


def test_report_file_carries_the_banner_in_every_mode(monkeypatch, capsys) -> None:
    """...but the FILE does, because the file is what leaves this machine.

    QA pastes evals/reports/*.md into a handoff, a PR carries it, and months later it is the
    only thing anyone reads. A terminal that nobody saved cannot carry that warning.
    """
    import tempfile

    import evals.run as run_mod

    for argv in (["--suite", "vision"], ["--suite", "vision", "--allow-inconclusive"]):
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(run_mod, "REPORTS", Path(tmp))
            assert main(argv) == 0
            written = (Path(tmp) / "vision.md").read_text()
        capsys.readouterr()
        assert "GATE NOT EVALUATED — n=0. This is NOT a pass." in written, argv


def test_golden_state_is_honest_about_a_missing_manifest() -> None:
    state = golden_state()
    if state["present"]:
        assert int(state["entries"]) > 0
    else:
        assert state["entries"] == 0
