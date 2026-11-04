"""Smoke tests for the eval harness itself: it must run, and it must never invent numbers."""

import json
from pathlib import Path

from evals import UNMEASURED
from evals.run import TARGETS, Measurement, golden_state, main, render


def test_every_row_is_either_a_number_with_its_n_or_unmeasured() -> None:
    """The E0-T2 rule, checkable: no empty cells, no bare numbers without a fixture count."""
    report = render("all")
    assert report.count(UNMEASURED) >= len(TARGETS)

    measured = {"segmentation mean IoU": Measurement(0.9416, 17, "provisional", "note")}
    with_number = render("all", measured=measured)
    assert "0.9416 (n=17, provisional)" in with_number
    cells = [line for line in with_number.splitlines() if line.startswith("| ")]
    assert sum(1 for line in cells if UNMEASURED in line) == len(TARGETS) - 1


def test_a_measured_row_carries_its_provenance_and_a_caveat() -> None:
    measured = {
        "segmentation mean IoU": Measurement(0.9416, 17, "provisional", "algorithmic masks")
    }
    report = render("vision", measured=measured, notes=["measured on evals/golden-provisional"])

    assert "Where the measured numbers came from" in report
    assert "algorithmic masks" in report
    assert "measured on evals/golden-provisional" in report
    assert "n=17 measured, but the gate itself is E0-T5" in report


def test_golden_is_preferred_over_the_provisional_set(monkeypatch) -> None:
    """When Bert's photos land, the number must switch sets without a code change."""
    import tempfile

    import evals.run as run_mod

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("golden", "golden-provisional"):
            (root / name).mkdir()
            (root / name / "manifest.json").write_text(
                json.dumps([{"id": "a", "path": "photos/a.jpg", "mask_path": "masks/a.png"}])
            )
        monkeypatch.setattr(run_mod, "GOLDEN", root / "golden" / "manifest.json")
        monkeypatch.setattr(run_mod, "PROVISIONAL", root / "golden-provisional" / "manifest.json")
        assert run_mod.masked_set() == root / "golden"

        (root / "golden" / "manifest.json").write_text(json.dumps([{"id": "a", "path": "p.jpg"}]))
        assert run_mod.masked_set() == root / "golden-provisional"


def test_no_masked_set_means_unmeasured_with_a_reason(monkeypatch) -> None:
    import tempfile

    import evals.run as run_mod

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(run_mod, "GOLDEN", Path(tmp) / "nope" / "manifest.json")
        monkeypatch.setattr(run_mod, "PROVISIONAL", Path(tmp) / "nope2" / "manifest.json")
        measured, notes = run_mod.measurements()

    # E2-T2 made a second stage measurable (prettify, off its own shipped fixtures), so "the
    # dict is empty" is no longer the same claim as "the segmentation row is missing". The
    # claim this test defends is unchanged: a missing masked set must not produce a number.
    assert "segmentation mean IoU" not in measured
    assert any("no masked set on disk" in note for note in notes)


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
        monkeypatch.setattr(run_mod, "measurements", lambda: ({}, []))
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
        monkeypatch.setattr(run_mod, "measurements", lambda: ({}, []))
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
        monkeypatch.setattr(run_mod, "measurements", lambda: ({}, []))
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
            monkeypatch.setattr(run_mod, "measurements", lambda: ({}, []))
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
