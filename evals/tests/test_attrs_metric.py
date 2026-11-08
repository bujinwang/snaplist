"""E3-T3 metric tests — SYNTHETIC fixtures only, zero network, zero credentials.

What is pinned here (PLAN E3-T3 DoD):

* all four matching rules, including the primary-colour hit and `unknown` pattern;
* a `failed` prediction stays in `n` and is a MISS on all four fields — never skipped to
  flatter an accuracy;
* an empty `material` prediction is a MISS and is counted in `empty_predictions`;
* `summarise()` returns raw `hits/n` floats with `n == len(manifest)`;
* the confusion list lists every miss verbatim, and `attrs.md` states the material evidence
  caveat and never rounds a verdict up;
* a missing or corrupt predictions artifact leaves the harness UNMEASURED with the collection
  command named in the reason — the harness never calls the network;
* `evals.metrics.attrs` opens no socket at all.

Nothing in this file reads `.env`, `evals/golden/photos/**`, or instantiates a VLM.
"""

from __future__ import annotations

import json
import socket
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from evals import UNMEASURED
from evals.metrics import attrs as attrs_metric
from evals.run import TARGETS, Measurement, render


@pytest.fixture
def workspace() -> Iterator[Path]:
    """`tempfile`, NOT pytest's `tmp_path`: the latter collides with the sandbox on this host."""
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _manifest_entry(
    rid: str,
    *,
    category: str = "top",
    colors: list[str] | None = None,
    pattern: str = "solid",
    material: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": rid,
        "path": f"photos/{rid}.jpg",
        "category": category,
        "colors": ["white"] if colors is None else colors,
        "pattern": pattern,
        "material": ["cotton"] if material is None else material,
    }


def _measured(
    rid: str,
    *,
    category: str = "top",
    colors: list[str] | None = None,
    pattern: str = "solid",
    material: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": rid,
        "status": "measured",
        "category": category,
        "colors": ["white"] if colors is None else colors,
        "pattern": pattern,
        "material": ["cotton"] if material is None else material,
        "round_trip_seconds": 1.5,
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "attempts": 1,
        "retry_count": 0,
        "repaired": False,
        "error_type": None,
        "error_message": None,
    }


def _failed(rid: str, *, error_type: str = "AttributeExtractionFailed") -> dict[str, Any]:
    return {
        "id": rid,
        "status": "failed",
        "category": None,
        "colors": None,
        "pattern": None,
        "material": None,
        "round_trip_seconds": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "attempts": 2,
        "retry_count": 1,
        "repaired": True,
        "error_type": error_type,
        "error_message": "synthetic provider failure (no real credential here)",
    }


def _payload(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": "2026-11-07T00:00:00Z",
        "git_sha": "deadbeef",
        "provider": "http",
        "model": "synthetic-model",
        "n_entries": len(entries),
        "n_measured": sum(1 for entry in entries if entry["status"] == "measured"),
        "n_failed": sum(1 for entry in entries if entry["status"] == "failed"),
        "prompt_tokens_total": sum(entry["prompt_tokens"] for entry in entries),
        "completion_tokens_total": sum(entry["completion_tokens"] for entry in entries),
        "round_trip_seconds_total": 3.0,
        "retry_count_total": sum(entry["retry_count"] for entry in entries),
        "token_accounting": "synthetic fixture — no real tokens",
        "entries": entries,
    }


def _write(workspace: Path, manifest: list[dict], payload: dict) -> tuple[Path, Path]:
    manifest_path = workspace / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    predictions_path = workspace / "attrs_predictions.json"
    predictions_path.write_text(json.dumps(payload), encoding="utf-8")
    return predictions_path, manifest_path


def _four_rule_fixture(workspace: Path) -> tuple[Path, Path]:
    """a=all hit; b=colour primary hit via a secondary GT colour; c=unknown/empty; d=failed."""
    manifest = [
        _manifest_entry("a"),
        _manifest_entry("b", category="top", colors=["white", "navy"]),
        _manifest_entry("c", pattern="floral", material=["wool"]),
        _manifest_entry("d"),
    ]
    entries = [
        _measured("a"),
        _measured("b", category="bottom", colors=["navy"]),
        _measured("c", colors=[], pattern="unknown", material=[]),
        _failed("d"),
    ]
    return _write(workspace, manifest, _payload(entries))


def test_all_four_matching_rules_and_secondary_statistic(workspace: Path) -> None:
    predictions, manifest = _four_rule_fixture(workspace)
    rows, meta = attrs_metric.run(predictions, manifest)
    stats = attrs_metric.summarise(rows)

    assert len(rows) == 4
    assert meta["n_measured"] == 3
    assert meta["n_failed"] == 1
    assert stats["n"] == 4  # failed row is IN the denominator

    # category: a and c hit; b mismatches; d failed.
    assert stats["fields"]["category"]["hits"] == 2
    assert stats["accuracies"]["attr category accuracy"] == 0.5
    # colours: a exact, b primary hit on a secondary GT colour; c empty, d failed.
    assert stats["fields"]["colors"]["hits"] == 2
    assert stats["accuracies"]["attr colour accuracy"] == 0.5
    assert stats["colors_exact_list"]["hits"] == 1
    assert stats["colors_exact_list"]["accuracy"] == 0.25
    # pattern: a and b hit; c is 'unknown'; d failed.
    assert stats["fields"]["pattern"]["hits"] == 2
    assert stats["accuracies"]["attr pattern accuracy"] == 0.5
    # material: a and b intersect; c is empty; d failed.
    assert stats["fields"]["material"]["hits"] == 2
    assert stats["accuracies"]["attr material accuracy"] == 0.5
    assert stats["material_empty_predictions"] == 1
    assert all(
        stats["fields"][field]["hits"] + stats["fields"][field]["misses"] == 4
        for field in attrs_metric.FIELDS
    )


def test_failed_entry_counts_as_a_miss_on_every_field(workspace: Path) -> None:
    manifest = [_manifest_entry("hit"), _manifest_entry("failed")]
    entries = [_measured("hit"), _failed("failed")]
    predictions, manifest_path = _write(workspace, manifest, _payload(entries))
    rows, _ = attrs_metric.run(predictions, manifest_path)
    stats = attrs_metric.summarise(rows)

    assert stats["n"] == 2
    assert stats["n_failed"] == 1
    for key in attrs_metric.METRIC_KEYS:
        assert stats["accuracies"][key] == 0.5, key
    failed_reasons = [row["reasons"] for row in rows if row["id"] == "failed"][0]
    assert all("prediction failed" in reason for reason in failed_reasons.values())
    assert all(
        "AttributeExtractionFailed" in reason for reason in failed_reasons.values()
    )


def test_empty_material_prediction_is_a_miss_and_is_counted(workspace: Path) -> None:
    manifest = [_manifest_entry("only", material=["wool"])]
    entries = [_measured("only", material=[])]
    predictions, manifest_path = _write(workspace, manifest, _payload(entries))
    rows, _ = attrs_metric.run(predictions, manifest_path)
    stats = attrs_metric.summarise(rows)

    assert stats["accuracies"]["attr material accuracy"] == 0.0
    assert stats["material_empty_predictions"] == 1
    assert rows[0]["material_empty_prediction"] is True
    assert "empty" in rows[0]["reasons"]["material"]


def test_unknown_pattern_never_matches_a_concrete_ground_truth(workspace: Path) -> None:
    manifest = [_manifest_entry("only", pattern="floral")]
    entries = [_measured("only", pattern="unknown")]
    predictions, manifest_path = _write(workspace, manifest, _payload(entries))
    rows, _ = attrs_metric.run(predictions, manifest_path)

    assert rows[0]["hits"]["pattern"] is False
    assert "'unknown'" in rows[0]["reasons"]["pattern"]
    assert attrs_metric.summarise(rows)["accuracies"]["attr pattern accuracy"] == 0.0


def test_summarise_returns_unrounded_hits_over_n_floats(workspace: Path) -> None:
    """3 hits out of 7 must be exactly 3/7, not a rounded 0.43 or a filtered denominator."""
    manifest = [_manifest_entry(f"e{i}") for i in range(7)]
    entries = [
        _measured(f"e{i}") if i < 3 else _measured(f"e{i}", category="bottom")
        for i in range(7)
    ]
    predictions, manifest_path = _write(workspace, manifest, _payload(entries))
    rows, _ = attrs_metric.run(predictions, manifest_path)
    stats = attrs_metric.summarise(rows)

    assert stats["n"] == 7
    assert stats["accuracies"]["attr category accuracy"] == 3 / 7
    assert isinstance(stats["accuracies"]["attr category accuracy"], float)


def test_confusion_list_lists_every_miss_verbatim_and_report_states_the_caveat(
    workspace: Path,
) -> None:
    predictions, manifest = _four_rule_fixture(workspace)
    rows, meta = attrs_metric.run(predictions, manifest)
    stats = attrs_metric.summarise(rows)

    primary = [item for item in stats["confusion"] if item["kind"] == "primary"]
    assert len(primary) == 8  # 16 field judgements - 8 hits
    for item in primary:
        assert set(item) == {"id", "field", "kind", "ground_truth", "predicted", "reason"}
        assert item["reason"]
    report = attrs_metric.render(rows, meta, stats)
    for item in stats["confusion"]:
        assert item["id"] in report
        assert item["field"] in report
    assert "8 primary misses" in report
    assert "no metric is rounded up in prose" in report
    assert "UNREACHABLE under this policy" in report
    assert "did NOT relax the evidence policy" in report
    assert "NOT a bug and NOT a model defect" in report
    # A verdict is computed on raw hits/n, and the exact fraction is printed, not rounded up.
    assert "| 2/4 |" in report
    assert "DOES NOT MEET" in report


def test_corrupt_predictions_are_rejected_instead_of_guessed(workspace: Path) -> None:
    manifest = [_manifest_entry("a")]
    manifest_path = workspace / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    bad_json = workspace / "attrs_predictions.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        attrs_metric.run(bad_json, manifest_path)

    short = _write(workspace, manifest, _payload([_measured("a")]))[0]
    short.write_text(
        json.dumps(_payload([_measured("a"), _measured("ghost")])), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="golden manifest has 1"):
        attrs_metric.run(short, manifest_path)

    wrong_status = workspace / "wrong_status.json"
    record = _measured("a")
    record["status"] = "estimated"
    wrong_status.write_text(json.dumps(_payload([record])), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid status"):
        attrs_metric.run(wrong_status, manifest_path)


def test_missing_predictions_leave_the_harness_unmeasured_with_a_reason(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    import evals.run as run_mod

    monkeypatch.setattr(run_mod, "ATTRS_PREDICTIONS", workspace / "does-not-exist.json")
    result, notes = run_mod._measure_attrs()

    assert result is None
    assert len(notes) == 1
    assert "UNMEASURED" in notes[0]
    assert "python -m evals.tools.collect_attrs" in notes[0]
    assert "never calls the network" in notes[0]
    report = render("vision", measured={}, notes=notes)
    for key in attrs_metric.METRIC_KEYS:
        row = next(line for line in report.splitlines() if line.startswith(f"| {key} |"))
        assert UNMEASURED in row, key


def test_corrupt_predictions_leave_the_harness_unmeasured_with_a_reason(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    import evals.run as run_mod

    broken = workspace / "attrs_predictions.json"
    broken.write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(run_mod, "ATTRS_PREDICTIONS", broken)
    result, notes = run_mod._measure_attrs()

    assert result is None
    assert "UNMEASURED" in notes[0]
    assert "unreadable or invalid" in notes[0]
    assert "python -m evals.tools.collect_attrs" in notes[0]


def test_harness_attr_keys_exactly_match_targets(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    import evals.run as run_mod

    predictions, manifest = _four_rule_fixture(workspace)
    monkeypatch.setattr(run_mod, "GOLDEN", manifest)
    monkeypatch.setattr(run_mod, "ATTRS_PREDICTIONS", predictions)
    got, notes = run_mod._measure_attrs()

    assert got is not None
    assert set(got) == set(attrs_metric.METRIC_KEYS)
    assert set(got) <= {target.metric for target in TARGETS}
    for measurement in got.values():
        assert isinstance(measurement, Measurement)
        assert measurement.n == 4
        assert measurement.set_name == "evals/golden"
        assert "provider=http" in measurement.note
        assert "evidence policy" in measurement.note
        assert "evals/reports/attrs.md" in measurement.note
    assert any("measured on evals/golden" in note for note in notes)
    report = render("vision", measured=got, notes=notes)
    assert "0.5000 (n=4, evals/golden)" in report


def test_measurements_without_artifact_has_no_attr_numbers_but_still_has_dedupe(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    """One missing artifact costs exactly four rows, never the whole harness."""
    import evals.run as run_mod

    monkeypatch.setattr(run_mod, "ATTRS_PREDICTIONS", workspace / "nope.json")
    monkeypatch.setattr(run_mod, "_measure_segmentation", lambda: (None, []))
    monkeypatch.setattr(run_mod, "_measure_prettify", lambda: (None, []))
    measured, notes = run_mod.measurements()

    assert not any(key in measured for key in attrs_metric.METRIC_KEYS)
    assert "dedupe false-merge rate" in measured
    assert any("python -m evals.tools.collect_attrs" in note for note in notes)


def test_metric_module_opens_no_socket(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    def _forbid(*args: object, **kwargs: object) -> None:
        raise AssertionError("evals.metrics.attrs attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _forbid)
    predictions, manifest = _four_rule_fixture(workspace)
    rows, meta = attrs_metric.run(predictions, manifest)
    assert len(attrs_metric.summarise(rows)["accuracies"]) == 4
    assert "VLM_API_KEY" not in json.dumps(meta)
