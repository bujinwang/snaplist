"""E3-T4 metric tests — synthetic fixture + shipped fixture, zero network, zero credentials.

What is pinned here:

* `evals/metrics/dedupe.py` REUSES `packages.vision.dedupe.run_fixture` and does not
  reimplement comparison, thresholds or sha256 logic (identity check + a call-count check);
* `run()` parses every printed pair verdict back into a row, and `summarise()` cross-checks
  the false-merge count and the rate against those rows;
* `dedupe.md` contains every pair verdict and states why the denominator is `n_distinct`, not
  `n_pairs`;
* the harness row is keyed exactly `dedupe false-merge rate` with `n=n_pairs` and
  `set_name="fixtures/dedupe"`;
* a missing fixture manifest returns UNMEASURED with a reason instead of a number;
* no socket is opened anywhere in this path.
"""

from __future__ import annotations

import json
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

import packages.vision.dedupe as vision_dedupe
from evals.metrics import dedupe as dedupe_metric


@pytest.fixture
def workspace() -> Iterator[Path]:
    """`tempfile`, NOT pytest's `tmp_path`: the latter collides with the sandbox on this host."""
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _fake_printed() -> str:
    return (
        "pair       label      kind   verdict    layer  structure    colour  note\n"
        "exact-01   duplicate  exact  duplicate  exact      exact        -  ok\n"
        "dist-01    distinct   None   distinct   -          0.4127     0.1234  ok (distinct)\n"
        "dist-02    distinct   None   duplicate  near       0.9500     0.8000  <<< FALSE MERGE\n"
        "\n"
        "exact: n=1 detected=1 recall=1.00 (a 1.0 here is the layer's definition)\n"
        "near : n=0 detected=0 recall=nan (upper bound: positives are transforms)\n"
        "digest_decided=1 thresholds: structure>=0.90 colour>=0.55 gate: false_merge<=0.05\n"
        "n_pairs=3 n_distinct=2 false_merge_rate=0.500\n"
    )


def _fake_summary() -> dict[str, float]:
    return {
        "n_pairs": 3.0,
        "n_distinct": 2.0,
        "n_duplicate": 1.0,
        "n_exact": 1.0,
        "n_near": 0.0,
        "n_false_merges": 1.0,
        "false_merge_rate": 0.5,
        "near_recall": float("nan"),
        "exact_recall": 1.0,
    }


def _synthetic_fixture(workspace: Path) -> Path:
    fixture = workspace / "dedupe_fixture"
    fixture.mkdir()
    pairs = [
        {"id": "exact-01", "a_path": "a.jpg", "b_path": "b.jpg", "label": "duplicate"},
        {"id": "dist-01", "a_path": "a.jpg", "b_path": "c.jpg", "label": "distinct"},
        {"id": "dist-02", "a_path": "a.jpg", "b_path": "d.jpg", "label": "distinct"},
    ]
    (fixture / "manifest.json").write_text(
        json.dumps({"fixture": "synthetic", "pairs": pairs}), encoding="utf-8"
    )
    return fixture


def test_it_reuses_run_fixture_and_does_not_reimplement_the_comparison() -> None:
    assert dedupe_metric.run_fixture is vision_dedupe.run_fixture
    source = Path(dedupe_metric.__file__).read_text(encoding="utf-8")
    assert "import hashlib" not in source
    assert "STRUCTURE_MIN =" not in source
    assert not hasattr(dedupe_metric, "_score_candidate")
    assert not hasattr(dedupe_metric, "find_duplicate")


def test_run_calls_run_fixture_once_and_parses_every_verdict(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    fixture = _synthetic_fixture(workspace)
    calls: list[Path] = []

    def _fake(fixture_dir: Path) -> dict[str, float]:
        calls.append(fixture_dir)
        print(_fake_printed(), end="")
        return _fake_summary()

    monkeypatch.setattr(dedupe_metric, "run_fixture", _fake)
    rows, meta = dedupe_metric.run(fixture)

    assert calls == [fixture]
    assert len(rows) == 3
    assert meta["n_pairs"] == 3
    assert meta["n_distinct"] == 2
    assert [row["id"] for row in rows] == ["exact-01", "dist-01", "dist-02"]
    assert rows[0]["duplicate_hit"] is True
    assert rows[1]["distinct_correct"] is True
    assert rows[2]["false_merge"] is True
    assert rows[2]["note"].startswith("<<< FALSE MERGE")


def test_summarise_cross_checks_the_rate_against_the_parsed_rows(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    fixture = _synthetic_fixture(workspace)

    def _fake(fixture_dir: Path) -> dict[str, float]:
        print(_fake_printed(), end="")
        return _fake_summary()

    monkeypatch.setattr(dedupe_metric, "run_fixture", _fake)
    rows, meta = dedupe_metric.run(fixture)
    stats = dedupe_metric.summarise(rows, meta)

    assert stats["false_merge_rate"] == 0.5
    assert stats["n_false_merges_from_rows"] == 1
    assert stats["meets_gate"] is False
    assert stats["n_duplicate_hits"] == 1
    assert stats["n_distinct_correct"] == 1

    # A summary that disagrees with the printed verdicts must be rejected, not averaged away.
    tampered = dict(meta["summary"])
    tampered["false_merge_rate"] = 0.0
    bad_meta = {**meta, "summary": tampered}
    with pytest.raises(ValueError, match="does not equal"):
        dedupe_metric.summarise(rows, bad_meta)


def test_report_contains_every_pair_and_the_denominator_explanation(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    fixture = _synthetic_fixture(workspace)

    def _fake(fixture_dir: Path) -> dict[str, float]:
        print(_fake_printed(), end="")
        return _fake_summary()

    monkeypatch.setattr(dedupe_metric, "run_fixture", _fake)
    rows, meta = dedupe_metric.run(fixture)
    text = dedupe_metric.render(rows, meta, dedupe_metric.summarise(rows, meta))

    for row in rows:
        assert row["id"] in text
        assert row["verdict"] in text
    assert "n_distinct" in text
    assert "NOT n_pairs" in text
    assert "would report 0.3333" in text
    assert "false_merge_rate = 1 / 2 = 0.5000" in text
    assert "n_pairs=3 n_distinct=2 false_merge_rate=0.500" in text
    assert "NOT reimplemented" in text


def test_shipped_fixture_smoke_is_all_local_and_has_no_false_merges() -> None:
    rows, meta = dedupe_metric.run(dedupe_metric.DEFAULT_FIXTURE_DIR)
    stats = dedupe_metric.summarise(rows, meta)

    assert len(rows) == 20
    assert meta["n_pairs"] == 20
    assert meta["n_distinct"] == 10
    assert meta["n_false_merges"] == 0
    assert stats["false_merge_rate"] == 0.0
    assert stats["meets_gate"] is True
    assert all(row["verdict"] in ("duplicate", "distinct") for row in rows)


def test_harness_dedupe_measurement_uses_the_real_fixture() -> None:
    import evals.run as run_mod
    from evals.run import Measurement, render

    measurement, notes = run_mod._measure_dedupe()

    assert isinstance(measurement, Measurement)
    assert measurement.n == 20
    assert measurement.set_name == "fixtures/dedupe"
    assert measurement.value == pytest.approx(0.0)
    assert "run_fixture" in measurement.note
    assert "n_distinct" in measurement.note
    assert "NOT n_pairs" in measurement.note
    assert any("measured on fixtures/dedupe" in note for note in notes)

    report = render("vision", measured={"dedupe false-merge rate": measurement}, notes=notes)
    assert "0.0000 (n=20, fixtures/dedupe)" in report


def test_missing_fixture_manifest_returns_unmeasured_with_a_reason(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    import evals.run as run_mod

    monkeypatch.setattr(run_mod, "DEDUPE_FIXTURES", workspace / "empty")
    measurement, notes = run_mod._measure_dedupe()

    assert measurement is None
    assert "UNMEASURED" in notes[0]
    assert "no manifest at" in notes[0]


def test_no_socket_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("the dedupe metric attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _forbid)
    rows, meta = dedupe_metric.run(dedupe_metric.DEFAULT_FIXTURE_DIR)
    assert dedupe_metric.summarise(rows, meta)["false_merge_rate"] == 0.0
