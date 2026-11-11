"""ADR-010 mask-review companion kit — mechanical facts and honesty boundaries.

`tmp_path` is deliberately NOT used: on this host the sandbox raises EEXIST for pytest's temp
root. All fixtures use `tempfile.TemporaryDirectory()` instead. No test reads or writes the
copyright-frozen golden set; every PNG here is a tiny in-test 4x4 image.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from evals.tools import mask_review_kit as kit
from evals.tools import validate_point_reviews as vp

_ID = "top-01"
#: Take the 20 ids from the real (tracked, committable) register so the fixture cannot drift
#: from the contract. This is text, not a copyright-frozen binary.
_ALL_IDS: list[str] = list(vp.load_register(vp.DEFAULT_REGISTER)["expected_ids"])


@pytest.fixture
def workspace() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _png(path: Path, values: list[list[int]], mode: str = "L") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = [value for row in values for value in row]
    image = Image.new(mode, (len(values[0]), len(values)))
    image.putdata(flat)
    image.save(path, format="PNG")


def _write_register(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _payload(ids: list[str] | None = None) -> dict:
    names = ids if ids is not None else _ALL_IDS
    return {
        "schema_version": 1,
        "policy": "ADR-010",
        "expected_ids": names,
        "expected_ids_source": "test fixture",
        "edit_distance_threshold": {
            "method": "xor@128",
            "min_changed_pixels": None,
            "max_changed_pixels": None,
            "max_changed_ratio": None,
            "decided_by": "ADR-010 / Bert ruling 2",
            "value_status": "UNSET",
        },
        "records": [
            {
                "id": rid,
                "status": "pending",
                "provenance": None,
                "proposal": {
                    "model": None,
                    "model_version": None,
                    "mask_path": f"masks_draft/{rid}.png",
                    "sha256": None,
                },
                "reviewed": {"mask_path": f"masks/{rid}.png", "sha256": None},
                "edit_distance": None,
                "review": None,
            }
            for rid in names
        ],
        "notes": [],
    }


def _fixture(workspace: Path, *, proposal_values: list[list[int]] | None = None,
             reviewed_values: list[list[int]] | None = None,
             mode: str = "L", ids: list[str] | None = None) -> tuple[Path, Path, dict]:
    """Build a full 20-slot register by default: the validator requires exactly 20 records."""
    golden = workspace / "golden"
    register = workspace / "register.json"
    names = ids if ids is not None else _ALL_IDS
    proposal = proposal_values or [[0, 0, 0, 0], [0, 255, 255, 0],
                                   [0, 255, 255, 0], [0, 0, 0, 0]]
    reviewed = reviewed_values or [[0, 0, 0, 0], [0, 255, 255, 0],
                                   [0, 255, 0, 0], [0, 0, 0, 0]]
    for rid in names:
        _png(golden / "masks_draft" / f"{rid}.png", proposal, mode)
        _png(golden / "masks" / f"{rid}.png", reviewed, mode)
    payload = _payload(names)
    _write_register(register, payload)
    return golden, register, payload


# --------------------------------------------------------------------------- measurement


def test_measure_computes_raw_png_hashes_xor_and_whole_frame_ratio(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace)
    measured = kit.measure(_ID, golden, payload)

    # sha256 is over the RAW PNG BYTES, not a binarised ndarray.
    assert measured.proposal_sha256 == hashlib.sha256(
        (golden / "masks_draft" / f"{_ID}.png").read_bytes()
    ).hexdigest()
    assert measured.reviewed_sha256 == hashlib.sha256(
        (golden / "masks" / f"{_ID}.png").read_bytes()
    ).hexdigest()
    # One pixel differs (row 2, col 2): XOR@128 -> 1 changed pixel, denominator H*W = 16.
    assert measured.changed_pixels == 1
    assert measured.changed_ratio == round(1 / 16, 4)
    assert measured.as_evidence()["edit_distance"]["method"] == "xor@128"
    assert measured.problems == ()
    assert (measured.width, measured.height) == (4, 4)


def test_measure_is_read_only(workspace: Path) -> None:
    golden, register, payload = _fixture(workspace)
    before = register.read_bytes()
    kit.measure(_ID, golden, payload)
    assert register.read_bytes() == before, "measure must never write"


def test_soft_edges_are_info_by_contract_not_a_failure(workspace: Path) -> None:
    gold_rgb = [[0, 0, 0, 0], [0, 128, 255, 0], [0, 255, 255, 0], [0, 0, 0, 0]]
    golden, _, payload = _fixture(workspace, reviewed_values=gold_rgb)
    relaxed = kit.measure(_ID, golden, payload)
    assert relaxed.problems == ()
    assert any("soft edges" in note for note in relaxed.notes)

    strict = kit.measure(_ID, golden, payload, strict_binary=True)
    assert any("not strictly" in problem for problem in strict.problems)


def test_non_L_mode_is_rejected(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace, mode="RGB")
    measured = kit.measure(_ID, golden, payload)
    assert any("wants L" in problem for problem in measured.problems)


def test_shape_mismatch_is_named_not_silently_resized(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace)
    _png(golden / "masks" / f"{_ID}.png", [[0, 0, 0], [255, 255, 0]])
    measured = kit.measure(_ID, golden, payload)
    assert any("XOR needs equal shapes" in problem for problem in measured.problems)
    assert measured.changed_pixels is None


def test_clean_clone_reports_missing_files_never_fabricates(workspace: Path) -> None:
    empty = workspace / "absent"
    measured = kit.measure(_ID, empty, _payload())
    assert measured.proposal_sha256 is None and measured.reviewed_sha256 is None
    assert measured.changed_pixels is None
    assert any("PNG missing" in problem for problem in measured.problems)
    assert any("photo absent" in note for note in measured.notes)


# --------------------------------------------------------------------------- record


def _record_args(golden: Path, register: Path, *, attest: bool = False,
                 write: bool = False, model: str = "sam2",
                 proposal_source: str = "ai-assisted:sam2+point-reviewed") -> list[str]:
    args = [
        "--register", str(register), "--golden-dir", str(golden),
        "record", "--id", _ID, "--model", model, "--model-version", "2.1",
        "--reviewer", "bert", "--review-id", "PR-010-top-01",
        "--points-reviewed", "1",
        "--proposal-source", proposal_source,
    ]
    if attest:
        args.append("--attest")
    if write:
        args.append("--write")
    return args


def test_record_without_attest_is_refused_and_writes_nothing(workspace: Path,
                                                             capsys) -> None:
    golden, register, _ = _fixture(workspace)
    before = register.read_bytes()
    assert kit.main(_record_args(golden, register)) == 2
    assert "--attest is required" in capsys.readouterr().err
    assert register.read_bytes() == before


def test_record_without_write_prints_the_record_but_does_not_write(workspace: Path,
                                                                   capsys) -> None:
    golden, register, _ = _fixture(workspace)
    before = register.read_bytes()
    assert kit.main(_record_args(golden, register, attest=True)) == 0
    out = capsys.readouterr().out
    assert "nothing was written" in out
    assert '"status": "reviewed"' in out
    assert "social evidence" in out
    assert register.read_bytes() == before


def test_write_changes_only_the_target_record_and_never_the_threshold(workspace: Path,
                                                                      capsys) -> None:
    golden, register, payload = _fixture(workspace)

    assert kit.main(_record_args(golden, register, attest=True, write=True)) == 0
    after = json.loads(register.read_text())
    assert after["edit_distance_threshold"] == payload["edit_distance_threshold"]
    assert after["expected_ids"] == _ALL_IDS
    assert after["records"][0]["status"] == "reviewed"
    assert after["records"][1:] == payload["records"][1:], "other records must be untouched"
    assert after["records"][0]["provenance"] == "ai-assisted:sam2+point-reviewed"
    assert after["records"][0]["review"]["reviewer"] == "bert"
    assert after["records"][0]["review"]["points_reviewed"] == 1
    # Threshold UNSET keeps u1_met False even with a reviewed row.
    report = vp.validate_structure(after)
    assert report.passed
    assert report.u1_met is False
    assert "threshold UNCHANGED; U1 still UNMET" in capsys.readouterr().out


def test_invalid_model_slug_is_refused_before_any_write(workspace: Path) -> None:
    golden, register, _ = _fixture(workspace)
    before = register.read_bytes()
    assert kit.main(_record_args(golden, register, attest=True, write=True,
                                 model="sam2.1")) == 1
    assert register.read_bytes() == before


def test_human_supplied_fields_are_never_inferred(workspace: Path) -> None:
    """A record cannot be built with missing/blank human fields."""
    golden, _, payload = _fixture(workspace)
    measured = kit.measure(_ID, golden, payload)
    base = payload["records"][0]
    with pytest.raises(ValueError, match="reviewer"):
        kit.build_record(base, measured, model="sam2", model_version="2.1",
                         reviewer="", review_id="x", points_reviewed=1,
                         reviewed_at="2026-11-09T00:00:00Z",
                         proposal_source="ai-assisted:sam2+point-reviewed")
    with pytest.raises(ValueError, match="points_reviewed"):
        kit.build_record(base, measured, model="sam2", model_version="2.1",
                         reviewer="bert", review_id="x", points_reviewed=0,
                         reviewed_at="2026-11-09T00:00:00Z",
                         proposal_source="ai-assisted:sam2+point-reviewed")


# --------------------------------------------------------------------------- check


def test_check_reconciles_ids_and_reports_clean_clone(workspace: Path, capsys) -> None:
    ids = ["top-01", "top-04"]
    empty_register = workspace / "register.json"
    _write_register(empty_register, _payload(ids))
    rc = kit.main([
        "--register", str(empty_register), "--golden-dir", str(workspace / "absent"),
        "check",
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "2 ids" in out
    assert "MISSING" in out
    assert "never fabricated" in out


def test_check_passes_on_contract_compliant_masks(workspace: Path, capsys) -> None:
    golden, register, _ = _fixture(workspace)
    assert kit.main([
        "--register", str(register), "--golden-dir", str(golden), "check",
    ]) == 0
    out = capsys.readouterr().out
    assert "0 problem(s)" in out
    assert "threshold UNSET" in out


def test_zero_network_and_no_secret_reads() -> None:
    source = Path(kit.__file__).read_text()
    for forbidden in ("socket", "requests", "httpx", "urllib", "urlopen", "API_KEY",
                      "SECRET", "PASSWORD", "os.environ"):
        assert forbidden not in source, f"unexpected {forbidden!r} in the kit"


# --------------------------------------------------------------------------- provenance gate


def _record_kwargs(proposal_source: str) -> dict:
    """Common human-supplied fields for a direct build_record() call."""
    return dict(
        model="sam2", model_version="2.1", reviewer="bert",
        review_id="PR-010-top-01", points_reviewed=412,
        reviewed_at="2026-11-11T00:00:00Z", proposal_source=proposal_source,
    )


def test_machine_tier_proposal_cannot_be_recorded(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace)
    measured = kit.measure(_ID, golden, payload)
    base = payload["records"][0]
    with pytest.raises(ValueError) as excinfo:
        kit.build_record(base, measured,
                         **_record_kwargs("algorithmic:floodfill-border"))
    message = str(excinfo.value)
    assert "machine-tier proposal cannot be recorded as ai-assisted point-reviewed" in message
    assert "ADR-010 N2" in message
    # No ai-assisted string may be fabricated anywhere in the register.
    assert base["status"] == "pending" and base["provenance"] is None
    assert "ai-assisted:" not in json.dumps(payload)


def test_historical_ai_prefix_is_refused(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace)
    measured = kit.measure(_ID, golden, payload)
    base = payload["records"][0]
    with pytest.raises(ValueError, match="ADR-010 N2"):
        kit.build_record(base, measured,
                         **_record_kwargs("ai:google-ai-mode-nano-banana"))
    assert "ai-assisted:" not in json.dumps(payload)


def test_bare_proposal_stage_string_is_machine_tier_and_refused(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace)
    measured = kit.measure(_ID, golden, payload)
    base = payload["records"][0]
    # A proposal without +point-reviewed is deliberately MACHINE (mask_provenance.py:26-27).
    with pytest.raises(ValueError, match="machine-tier proposal cannot be recorded"):
        kit.build_record(base, measured,
                         **_record_kwargs("ai-assisted:google-nano-banana"))
    assert "ai-assisted:" not in json.dumps(payload)


def test_hand_tier_proposal_is_refused(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace)
    measured = kit.measure(_ID, golden, payload)
    base = payload["records"][0]
    with pytest.raises(ValueError, match="HAND tier"):
        kit.build_record(base, measured, **_record_kwargs("hand-drawn"))
    assert "ai-assisted:" not in json.dumps(payload)


def test_valid_ai_assisted_source_roundtrips_and_guards_remain(workspace: Path) -> None:
    golden, _, payload = _fixture(workspace)
    measured = kit.measure(_ID, golden, payload)
    base = payload["records"][0]
    canonical = "ai-assisted:sam2+point-reviewed"
    record = kit.build_record(base, measured, **_record_kwargs(canonical))
    assert record["provenance"] == canonical
    assert kit.AI_ASSISTED_RE.fullmatch(record["provenance"])
    assert kit.parse_model_slug(record["provenance"]) == "sam2"
    assert record["proposal"]["model"] == "sam2"
    # The original :244-245 guards must still be present in the source.
    source_text = Path(kit.__file__).read_text(encoding="utf-8")
    assert "AI_ASSISTED_RE.fullmatch(provenance)" in source_text
    assert "parse_model_slug(provenance) != model" in source_text
    with pytest.raises(ValueError, match="does not match"):
        kit.build_record(base, measured,
                         **_record_kwargs("ai-assisted:other+point-reviewed"))


def test_record_cli_refuses_machine_tier_and_leaves_register_unchanged(
        workspace: Path, capsys) -> None:
    golden, register, _ = _fixture(workspace)
    before = register.read_bytes()
    rc = kit.main(_record_args(golden, register, attest=True, write=True,
                               proposal_source="algorithmic:floodfill-border"))
    captured = capsys.readouterr()
    assert rc != 0
    assert "machine-tier proposal cannot be recorded" in captured.err
    assert '"status": "reviewed"' not in captured.out
    assert "ai-assisted:" not in captured.out
    assert register.read_bytes() == before


def test_record_requires_an_explicit_proposal_source(workspace: Path) -> None:
    golden, register, _ = _fixture(workspace)
    args = [
        "--register", str(register), "--golden-dir", str(golden),
        "record", "--id", _ID, "--model", "sam2", "--model-version", "2.1",
        "--reviewer", "bert", "--review-id", "PR-010-top-01",
        "--points-reviewed", "1", "--attest",
    ]
    with pytest.raises(SystemExit) as excinfo:
        kit.main(args)
    assert excinfo.value.code == 2
