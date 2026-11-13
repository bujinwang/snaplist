"""ADR-010 — point-review register schema, reconciliation, two-condition conflicts, U1.

`tmp_path` is deliberately NOT used: on this host the sandbox raises EEXIST for pytest's temp
root. A `workspace` fixture built on `tempfile.TemporaryDirectory()` is used instead, and
`DEFAULT_GOLDEN` is monkeypatched to simulate a clean clone with no `manifest.json`.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from evals.mask_provenance import MaskSourceTier, ReviewState
from evals.tools import validate_point_reviews as vp

_SHA = "a" * 64


@pytest.fixture
def workspace() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as directory:
        yield Path(directory)


def _register() -> dict:
    return vp.load_register(vp.DEFAULT_REGISTER)


def _pending_register() -> dict:
    """An explicitly all-pending ledger, independent of the shipped file's progress.

    The shipped register is a LIVING ledger: rows graduate from pending to reviewed as
    E0-T1 advances. Tests that pin pending-row invariants must construct that state
    explicitly rather than assuming the shipped file stays at 0/20 forever — otherwise
    every legitimate graduation to `reviewed` breaks a test that was never about U1.
    """
    payload = _register()
    # Take ANY row as a shape template, then blank every evidence field. Do not search
    # the shipped ledger for a pending row: once all 20 slots graduate there is none, and
    # this fixture must keep working at 0/20, 10/20 and 20/20 alike.
    blank = copy.deepcopy(payload["records"][0])
    blank["status"] = "pending"
    blank["provenance"] = None
    blank["edit_distance"] = None
    blank["review"] = None
    blank["proposal"] = {
        "model": None, "model_version": None, "mask_path": None, "sha256": None
    }
    blank["reviewed"] = {"mask_path": None, "sha256": None}
    rows = []
    for rid in payload["expected_ids"]:
        rec = copy.deepcopy(blank)
        rec["id"] = rid
        rec["proposal"]["mask_path"] = f"masks_draft/{rid}.png"
        rec["reviewed"]["mask_path"] = f"masks/{rid}.png"
        rows.append(rec)
    payload["records"] = rows
    # The pending ledger models the pre-decision state: threshold not yet set.
    payload["edit_distance_threshold"] = {
        "method": "xor@128",
        "min_changed_pixels": None,
        "max_changed_pixels": None,
        "max_changed_ratio": None,
        "value_status": "UNSET — pending fixture",
    }
    return payload


def _reviewed(record: dict) -> dict:
    """Turn one pending record into a fully evidenced reviewed record."""
    result = copy.deepcopy(record)
    result["status"] = "reviewed"
    result["provenance"] = "ai-assisted:sam2+point-reviewed"
    result["proposal"]["model"] = "sam2"
    result["proposal"]["model_version"] = "2.1"
    result["proposal"]["sha256"] = _SHA
    result["reviewed"]["sha256"] = _SHA
    result["edit_distance"] = {
        "method": "xor@128",
        "changed_pixels": 42,
        "changed_ratio": 0.01,
    }
    result["review"] = {
        "review_id": f"R-{result['id']}",
        "reviewer": "tester",
        "reviewed_at": "2026-11-08T00:00:00Z",
        "points_reviewed": 10,
    }
    return result


def _all_reviewed() -> dict:
    payload = _register()
    payload["records"] = [_reviewed(record) for record in payload["records"]]
    return payload


def _set_threshold(payload: dict) -> dict:
    payload["edit_distance_threshold"].update(
        {
            "min_changed_pixels": 1,
            "max_changed_pixels": 1000,
            "max_changed_ratio": 0.05,
            "value_status": "SET — synthetic test value",
        }
    )
    return payload


# ---------------------------------------------------------------------------- schema


def test_shipped_register_is_structurally_valid_and_u1_met() -> None:
    # The shipped ledger is a LIVING file: once the threshold is SET and all 20 rows are
    # reviewed, U1 is MET by the ADR-010 formula. The invariant is structural validity +
    # the present-tense U1 state, NOT a frozen 0/20 count.
    report = vp.validate_structure(_register())
    assert report.passed is True
    assert report.slots == 20
    assert report.reviewed == 20
    assert report.pending == 0
    assert report.threshold_status == "SET"
    assert report.u1_met is True
    assert isinstance(report.as_state(), ReviewState)
    assert report.as_state().u1_met is True
    # Keep the original 0/20 assertion, pinned against an explicitly pending ledger.
    blank = vp.validate_structure(_pending_register())
    assert blank.passed is True
    assert blank.reviewed == 0
    assert blank.pending == 20
    assert blank.threshold_status == "UNSET"
    assert blank.u1_met is False


def test_expected_ids_match_the_twenty_records_exactly() -> None:
    payload = _register()
    assert len(payload["expected_ids"]) == 20
    assert len(set(payload["expected_ids"])) == 20
    assert [record["id"] for record in payload["records"]] == payload["expected_ids"]
    assert payload["expected_ids"][0] == "top-01"
    assert payload["expected_ids"][-1] == "hard-05"
    names = [check.name for check in vp.validate_structure(payload).checks]
    assert "records match expected_ids" in names


def test_pending_rows_leave_every_evidence_field_null() -> None:
    payload = _pending_register()
    for record in payload["records"]:
        assert record["status"] == "pending"
        for key in ("provenance", "edit_distance", "review"):
            assert record[key] is None
        assert record["proposal"]["model"] is None
        assert record["proposal"]["model_version"] is None
        assert record["proposal"]["sha256"] is None
        assert record["reviewed"]["sha256"] is None
        # Declared relative paths are pointers, not evidence.
        assert record["proposal"]["mask_path"].startswith("masks_draft/")
        assert record["reviewed"]["mask_path"].startswith("masks/")


def test_a_half_filled_pending_row_is_rejected() -> None:
    payload = _pending_register()
    payload["records"][0]["proposal"]["model"] = "sam2"
    report = vp.validate_structure(payload)
    assert report.passed is False
    assert any("must leave every evidence field null" in check.detail for check in report.checks)


def test_record_id_mismatch_is_rejected() -> None:
    payload = _register()
    payload["records"][3]["id"] = "top-99"
    report = vp.validate_structure(payload)
    assert report.passed is False
    assert any(check.name == "records match expected_ids" and not check.ok
               for check in report.checks)


# ------------------------------------------------------------- two-condition conflicts


def test_ai_proposal_with_a_hand_prefix_is_a_hard_conflict() -> None:
    payload = _all_reviewed()
    payload["records"][0]["proposal"]["model"] = "sam2"
    payload["records"][0]["provenance"] = "hand:photoshop"
    report = vp.validate_structure(payload)
    assert report.passed is False
    assert any(
        'CONFLICT: AI proposal recorded but mask_source starts with "hand"' in check.detail
        for check in report.checks
    )


def test_ai_assisted_tier_without_a_review_is_rejected() -> None:
    payload = _all_reviewed()
    payload["records"][0]["review"] = None
    report = vp.validate_structure(payload)
    assert report.passed is False
    assert any("requires a review record" in check.detail for check in report.checks)


def test_bad_provenance_and_bad_sha256_are_rejected() -> None:
    payload = _all_reviewed()
    payload["records"][0]["provenance"] = "ai-assisted:sam2"  # missing suffix
    payload["records"][1]["proposal"]["sha256"] = "not-a-digest"
    report = vp.validate_structure(payload)
    assert report.passed is False
    assert sum(1 for check in report.checks if not check.ok) >= 2


# ------------------------------------------------------------- U1 formula


def test_twenty_reviewed_rows_with_unset_threshold_keep_u1_unmet() -> None:
    """Bert ruling 2: no number, no U1 — even when every slot says reviewed.

    This test pins the U1 FORMULA, so it forces the threshold UNSET on its own payload
    rather than depending on whatever state the shipped register is in.
    """
    payload = _all_reviewed()
    payload["edit_distance_threshold"] = {
        "method": "xor@128",
        "min_changed_pixels": None,
        "max_changed_pixels": None,
        "max_changed_ratio": None,
        "value_status": "UNSET — test override",
    }
    report = vp.validate_structure(payload)
    assert report.reviewed == 20
    assert report.pending == 0
    assert report.slots == 20
    assert report.threshold_status == "UNSET"
    assert report.passed is True, "structural validity is not the U1 decision"
    assert report.u1_met is False


def test_threshold_enforcement_rejects_a_row_over_the_cap() -> None:
    """The threshold is now a real gate: a reviewed row exceeding the cap breaks U1."""
    payload = _set_threshold(_all_reviewed())
    payload["records"][0]["edit_distance"]["changed_pixels"] = 10_000
    payload["records"][0]["edit_distance"]["changed_ratio"] = 0.50
    report = vp.validate_structure(payload)
    assert report.threshold_status == "SET"
    assert report.u1_met is False
    assert any(
        "edit-distance" in check.name and not check.ok for check in report.checks
    )


def test_u1_met_requires_all_three_conditions() -> None:
    payload = _set_threshold(_all_reviewed())
    report = vp.validate_structure(payload)
    assert report.threshold_status == "SET"
    assert report.passed is True
    assert report.u1_met is True
    # Break the count back to 19 and U1 falls again.
    payload["records"][0]["status"] = "pending"
    for key in ("provenance", "edit_distance", "review"):
        payload["records"][0][key] = None
    payload["records"][0]["proposal"].update({"model": None, "model_version": None,
                                              "sha256": None})
    payload["records"][0]["reviewed"]["sha256"] = None
    assert vp.validate_structure(payload).u1_met is False


# ------------------------------------------------------------- CLI / clean clone


def test_default_cli_reports_present_tense_u1_state_and_exits_zero(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The shipped register now has 20/20 reviewed AND the threshold SET, so the default run
    # legitimately reports U1 MET — but exit 0 still means "structurally valid", not "DoD done".
    assert vp.main([]) == 0
    out = capsys.readouterr().out
    assert "U1 MET" in out
    assert "NOT a DoD verdict" in out
    assert "ADR-010 does not pre-adjudicate" in out
    # The literal 0/20 wording stays pinned, but against an explicitly pending ledger.
    blank = workspace / "pending_register.json"
    blank.write_text(json.dumps(_pending_register()), encoding="utf-8")
    assert vp.main(["--register", str(blank)]) == 0
    assert "20/20 slots registered, 0/20 point-reviewed, U1 UNMET" in capsys.readouterr().out


def test_clean_clone_without_manifest_still_exits_zero(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI mode must never require manifest.json; an absent one is a SKIP, not a FAIL."""
    monkeypatch.setattr(vp, "DEFAULT_GOLDEN", workspace / "no-such-golden")
    assert not (workspace / "no-such-golden" / "manifest.json").exists()
    assert vp.main(["--cross-check-manifest"]) == 0
    out = capsys.readouterr().out
    assert "manifest absent (clean clone) — cross-check skipped" in out
    # The shipped register now has 20/20 reviewed AND the threshold SET, so the default
    # run legitimately reports U1 MET even in clean-clone (manifest-less) mode.
    assert "U1 MET" in out
    assert "| FAIL |" not in out


def test_cross_check_manifest_reconciles_the_twenty_masked_ids(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = _register()["expected_ids"]
    manifest = [
        {"id": rid, "mask_path": f"masks/{rid}.png"} for rid in expected
    ] + [{"id": "unmasked", "mask_path": None}]
    path = workspace / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert vp.main(["--cross-check-manifest", str(path)]) == 0
    assert "ids match" in capsys.readouterr().out

    mismatch = workspace / "mismatch.json"
    mismatch.write_text(json.dumps(manifest[:-2]), encoding="utf-8")
    assert vp.main(["--cross-check-manifest", str(mismatch)]) == 1
    assert "ids differ" in capsys.readouterr().out


def test_missing_register_is_exit_two(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert vp.main(["--register", str(workspace / "nope.json")]) == 2
    assert "unusable" in capsys.readouterr().err
    broken = workspace / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert vp.main(["--register", str(broken)]) == 2


def test_conflicting_register_is_exit_one(workspace: Path) -> None:
    payload = _all_reviewed()
    payload["records"][0]["provenance"] = "hand:photoshop"
    path = workspace / "register.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert vp.main(["--register", str(path)]) == 1


def test_verify_local_masks_is_a_noop_on_a_pending_register(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No reviewed rows => no PNG opened, no Pillow import, clean clone stays green."""
    monkeypatch.setattr(vp, "DEFAULT_GOLDEN", workspace / "no-binaries")
    blank = workspace / "pending_register.json"
    blank.write_text(json.dumps(_pending_register()), encoding="utf-8")
    assert vp.main(["--register", str(blank), "--verify-local-masks"]) == 0


def test_classifier_tiers_used_by_register_validation() -> None:
    from evals.mask_provenance import classify

    payload = _all_reviewed()
    for record in payload["records"]:
        assert classify(record["provenance"]).tier is MaskSourceTier.AI_ASSISTED_REVIEWED


# ---------------------------------------------------------------- B2 field semantics


def _write_png(path: Path, rows: list[list[int]]) -> None:
    """Write a tiny 8-bit L-mode PNG at runtime (never committed, never a fixture binary)."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (len(rows[0]), len(rows)))
    image.putdata([value for row in rows for value in row])
    image.save(path, format="PNG")


def _reviewed_with_real_masks(workspace: Path) -> dict:
    """20 reviewed rows whose declared PNG pairs exist and whose registered values are exact.

    Proposal has one foreground pixel; reviewed adds a second. XOR@128 therefore counts 1
    changed pixel over a 4x4 frame => changed_ratio = 1/16 = 0.0625.
    """
    payload = _register()
    payload["records"] = [_reviewed(record) for record in payload["records"]]
    # Isolate B2 semantics from whatever state the shipped threshold is in: force UNSET so
    # u1_met is False here for the B2 tests' own assertions, independent of the live ledger.
    payload["edit_distance_threshold"] = {
        "method": "xor@128",
        "min_changed_pixels": None,
        "max_changed_pixels": None,
        "max_changed_ratio": None,
        "value_status": "UNSET — B2 test isolation",
    }
    proposal_rows = [[0] * 4 for _ in range(4)]
    proposal_rows[0][0] = 255
    reviewed_rows = [list(row) for row in proposal_rows]
    reviewed_rows[1][1] = 255
    for record in payload["records"]:
        proposal_path = workspace / record["proposal"]["mask_path"]
        reviewed_path = workspace / record["reviewed"]["mask_path"]
        _write_png(proposal_path, proposal_rows)
        _write_png(reviewed_path, reviewed_rows)
        record["proposal"]["sha256"] = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        record["reviewed"]["sha256"] = hashlib.sha256(reviewed_path.read_bytes()).hexdigest()
        record["edit_distance"]["changed_pixels"] = 1
        record["edit_distance"]["changed_ratio"] = 1 / 16
    return payload


def test_b2_recomputes_raw_png_hashes_xor_count_and_whole_frame_ratio(workspace: Path) -> None:
    """Architect's three field semantics: raw bytes, XOR (not IoU), H*W denominator."""
    payload = _reviewed_with_real_masks(workspace)
    report = vp.verify_local_masks(payload, workspace)
    assert report.passed is True
    names = [check.name for check in report.checks]
    assert any("sha256" in name for name in names)
    assert any("xor@128" in name for name in names)
    assert any("changed_ratio" in name for name in names)
    assert any("changed_ratio=0.062500 over 16 pixels" in check.detail
               for check in report.checks)
    # u1_met stays False: _reviewed_with_real_masks forces the threshold UNSET to isolate
    # B2 field semantics from the live ledger's U1 state.
    assert report.u1_met is False


def test_b2_rejects_a_wrong_raw_png_byte_hash(workspace: Path) -> None:
    payload = _reviewed_with_real_masks(workspace)
    payload["records"][0]["proposal"]["sha256"] = "0" * 64
    report = vp.verify_local_masks(payload, workspace)
    assert report.passed is False
    assert any(not check.ok and "sha256" in check.name for check in report.checks)


def test_b2_rejects_a_wrong_xor_changed_pixels(workspace: Path) -> None:
    payload = _reviewed_with_real_masks(workspace)
    payload["records"][0]["edit_distance"]["changed_pixels"] = 99
    report = vp.verify_local_masks(payload, workspace)
    assert report.passed is False
    assert any(not check.ok and "xor@128" in check.name for check in report.checks)


def test_b2_rejects_a_wrong_changed_ratio_denominator(workspace: Path) -> None:
    payload = _reviewed_with_real_masks(workspace)
    # 1 / 8 would be the value if somebody used half the frame instead of H * W.
    payload["records"][0]["edit_distance"]["changed_ratio"] = 1 / 8
    report = vp.verify_local_masks(payload, workspace)
    assert report.passed is False
    assert any(not check.ok and "changed_ratio" in check.name for check in report.checks)


def test_b2_reports_a_shape_mismatch_without_silent_resize(workspace: Path) -> None:
    payload = _reviewed_with_real_masks(workspace)
    first = payload["records"][0]
    _write_png(workspace / first["reviewed"]["mask_path"], [[0] * 5 for _ in range(5)])
    first["reviewed"]["sha256"] = hashlib.sha256(
        (workspace / first["reviewed"]["mask_path"]).read_bytes()
    ).hexdigest()
    report = vp.verify_local_masks(payload, workspace)
    assert report.passed is False
    mismatch = [check for check in report.checks if "shape" in check.name and not check.ok]
    assert len(mismatch) == 1
    assert first["id"] in mismatch[0].detail
    assert "XOR is only defined on equal shapes" in mismatch[0].detail
