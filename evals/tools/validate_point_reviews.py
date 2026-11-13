"""ADR-010 — read-only validator for the point-review register.

    python -m evals.tools.validate_point_reviews
    python -m evals.tools.validate_point_reviews --cross-check-manifest [PATH]
    python -m evals.tools.validate_point_reviews --verify-local-masks [DIR]

Two modes, deliberately separate:

* **CI / clean clone (default)** reads ONLY `evals/golden-review/point_review_register.json`.
  A clean clone has no `evals/golden/manifest.json`, so this mode must never open it. It checks
  the register's own consistency and reconciles `expected_ids` ↔ the 20 `records`.
* **Local optional** adds `--cross-check-manifest` (reconcile the committable id index with the
  20 local manifest entries whose `mask_path != null`) and/or `--verify-local-masks` (B2: raw
  PNG byte hashes plus XOR@128 edit distance for `reviewed` rows; PIL is imported lazily, only
  inside that path).

B2 field semantics (ADR-010, clarified by the architect — these are definitions, not a design
change): `changed_pixels = int(count_nonzero((pred >= 128) ^ (ref >= 128)))` — an **XOR** count,
not an IoU union/intersection expression, even though on binary masks it is numerically equal
to `union - intersection`. `changed_ratio = changed_pixels / (H * W)`, i.e. the denominator is
the **whole frame**, not the union and not the garment region. Both `sha256` values are the hash
of the **raw PNG file bytes** (`hashlib.sha256(path.read_bytes()).hexdigest()`), never of a
binarised ndarray. File identity and pixel difference are two independent facts: re-encoding a
PNG changes its byte hash without changing its pixels, so the hash is
not a pixel-equivalence judgement. A size mismatch is reported as a named failing check
(entry id + both sizes); B2 never silently resizes.

Exit codes: `0` = the register is structurally valid (and, today, prints `U1 UNMET`);
`1` = broken/conflicting register or a failed reconciliation; `2` = register missing or not
usable JSON. **`0` is NOT a DoD verdict.** U1's last step is a human sign-off, and while the
edit-distance threshold is `UNSET`, `u1_met` is False even if all 20 rows say `reviewed`.

This validator does not wire itself into CI (`ci.yml` / `steps.sh` untouched), does not write
any file, does not read a credential and does not touch the copyright-frozen binaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from evals.mask_provenance import MaskSourceTier, ReviewState, classify

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTER = ROOT / "evals" / "golden-review" / "point_review_register.json"
DEFAULT_GOLDEN = ROOT / "evals" / "golden"

EXPECTED_SLOTS = 20
STATUSES = frozenset({"pending", "reviewed"})
#: `changed_ratio` is recorded to four decimals in the register; allow that rounding when B2
#: recomputes it from `changed_pixels / (H * W)`.
_RATIO_TOLERANCE = 1e-4
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EDIT_DISTANCE_KEYS = ("method", "changed_pixels", "changed_ratio")
_REVIEW_KEYS = ("review_id", "reviewer", "reviewed_at", "points_reviewed")


@dataclass(frozen=True, slots=True)
class Check:
    """One named, individually reportable structural check."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReviewReport:
    """The complete, immutable outcome of one validation run.

    `u1_met` follows ADR-010 exactly:
    `(reviewed == slots == 20) and threshold_status != "UNSET" and all_checks_ok`.
    `passed` means "structurally valid" — it is deliberately a different claim.
    """

    checks: list[Check]
    reviewed: int
    pending: int
    slots: int
    u1_met: bool
    threshold_status: str = "UNSET"
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Structure only. A passing report does not satisfy U1."""
        return bool(self.checks) and all(check.ok for check in self.checks)

    def as_state(self) -> ReviewState:
        """The immutable summary handed to report renderers (`seg_iou.render`)."""
        return ReviewState(
            reviewed=self.reviewed,
            slots=self.slots,
            u1_met=self.u1_met,
            threshold_status=self.threshold_status,
        )


# ----------------------------------------------------------------------------- loading


def load_register(path: Path = DEFAULT_REGISTER) -> dict:
    """Load the register JSON; missing/bad JSON propagates for `main()` to map to exit 2."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _records(payload: dict) -> list[dict]:
    """Valid records only; a malformed register still produces a report, not a crash."""
    raw = payload.get("records")
    if not isinstance(raw, list):
        return []
    return [record for record in raw if isinstance(record, dict)]


def _threshold_status(payload: dict) -> str:
    """`UNSET` until all three numeric bounds are decided AND value_status says so."""
    block = payload.get("edit_distance_threshold")
    if not isinstance(block, dict):
        return "UNSET"
    numbers = (block.get("min_changed_pixels"), block.get("max_changed_pixels"),
               block.get("max_changed_ratio"))
    status = str(block.get("value_status", ""))
    if any(value is None for value in numbers) or status.upper().startswith("UNSET"):
        return "UNSET"
    return "SET"


def _is_iso_utc(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


# ------------------------------------------------------------------- structural checks


def _enforce_threshold(
    label: str, distance: dict, threshold: object, checks: list[Check]
) -> None:
    """Gate each reviewed row against the decided threshold.

    Only fires when the threshold is SET. While UNSET, U1 is already blocked by
    `_threshold_status`, so we must not invent a gate that does not yet exist.
    """
    if not isinstance(threshold, dict):
        return
    if str(threshold.get("value_status", "")).upper().startswith("UNSET"):
        return
    cp = distance.get("changed_pixels")
    cr = distance.get("changed_ratio")
    mn = threshold.get("min_changed_pixels")
    mx = threshold.get("max_changed_pixels")
    mr = threshold.get("max_changed_ratio")
    if _is_number(mn) and _is_number(mx) and _is_number(cp):
        if not (mn <= cp <= mx):
            checks.append(
                Check(
                    f"{label} edit-distance pixels",
                    False,
                    f"changed_pixels {cp} is outside the accepted [{mn}, {mx}]",
                )
            )
    if _is_number(mr) and _is_number(cr):
        if not (cr <= mr):
            checks.append(
                Check(
                    f"{label} edit-distance ratio",
                    False,
                    f"changed_ratio {cr:.6f} exceeds max_changed_ratio {mr}",
                )
            )


def _record_checks(record: dict, index: int, threshold: dict | None = None) -> list[Check]:
    """All per-record rules, including ADR-010's two-condition conflict detection."""
    rid = str(record.get("id", f"<{index}>"))
    label = f"record {rid}"
    checks: list[Check] = []

    required = ("id", "status", "provenance", "proposal", "reviewed", "edit_distance", "review")
    missing = [key for key in required if key not in record]
    if missing:
        return [Check(label, False, f"missing keys: {', '.join(missing)}")]

    proposal = record["proposal"]
    reviewed = record["reviewed"]
    if not isinstance(proposal, dict) or not isinstance(reviewed, dict):
        return [Check(label, False, "proposal/reviewed must be objects")]
    for key in ("model", "model_version", "mask_path", "sha256"):
        if key not in proposal:
            return [Check(label, False, f"proposal is missing {key!r}")]
    for key in ("mask_path", "sha256"):
        if key not in reviewed:
            return [Check(label, False, f"reviewed is missing {key!r}")]
    for holder, key in ((proposal, "mask_path"), (reviewed, "mask_path")):
        if not isinstance(holder.get(key), str) or not str(holder.get(key)).strip():
            return [Check(label, False, f"{key} must be a non-empty relative path")]

    status = record["status"]
    if status not in STATUSES:
        return [Check(label, False, f"invalid status {status!r}; expected pending/reviewed")]

    provenance = record["provenance"]
    tier = classify(provenance).tier
    model = proposal.get("model")
    has_ai_proposal = isinstance(model, str) and bool(model.strip())

    # Hard conflict: an AI proposal may never be labelled with the human `hand` prefix.
    if has_ai_proposal and tier is MaskSourceTier.HAND:
        checks.append(
            Check(
                f"{label} two-condition conflict",
                False,
                'CONFLICT: AI proposal recorded but mask_source starts with "hand"',
            )
        )

    if status == "pending":
        evidence = (
            record["provenance"], proposal["model"], proposal["model_version"],
            proposal["sha256"], reviewed["sha256"], record["edit_distance"], record["review"],
        )
        if any(value is not None for value in evidence):
            return checks + [
                Check(label, False, "pending records must leave every evidence field null")
            ]
        return checks + [Check(label, True, "pending; all evidence fields null")]

    # ---- reviewed -------------------------------------------------------------------------
    if not isinstance(provenance, str) or tier is not MaskSourceTier.AI_ASSISTED_REVIEWED:
        checks.append(
            Check(
                label,
                False,
                "provenance must fully match ai-assisted:<slug>+point-reviewed "
                f"(got {provenance!r}, tier {tier.value})",
            )
        )
    if not has_ai_proposal or not isinstance(proposal.get("model_version"), str) \
            or not proposal["model_version"].strip():
        checks.append(Check(label, False, "proposal model and model_version are required"))
    for holder, key in ((proposal, "sha256"), (reviewed, "sha256")):
        value = holder.get(key)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            checks.append(Check(label, False, f"{key} must be 64 lowercase hex characters"))

    distance = record["edit_distance"]
    if not isinstance(distance, dict) or any(key not in distance for key in _EDIT_DISTANCE_KEYS):
        checks.append(
            Check(label, False, f"edit_distance needs {', '.join(_EDIT_DISTANCE_KEYS)}")
        )
    elif not _is_number(distance.get("changed_pixels")) \
            or not _is_number(distance.get("changed_ratio")) \
            or not isinstance(distance.get("method"), str):
        checks.append(Check(label, False, "edit_distance values have the wrong types"))
    else:
        # Types are sound: enforce the decided threshold (no-op while UNSET).
        _enforce_threshold(label, distance, threshold, checks)

    review = record["review"]
    if not isinstance(review, dict) or any(key not in review for key in _REVIEW_KEYS):
        checks.append(
            Check(
                f"{label} two-condition conflict",
                False,
                "CONFLICT: AI-assisted tier requires a review record "
                f"({', '.join(_REVIEW_KEYS)})",
            )
        )
    elif not _is_iso_utc(review.get("reviewed_at")) or not _is_number(
        review.get("points_reviewed")
    ):
        checks.append(
            Check(label, False, "review.reviewed_at must be ISO 8601 UTC; points_reviewed a number")
        )

    return checks or [Check(label, True, "reviewed; two-condition evidence present")]


def _structure_checks(payload: dict) -> list[Check]:
    """CI/default mode: register self-consistency ONLY. Never opens manifest.json."""
    checks: list[Check] = []
    version = payload.get("schema_version")
    checks.append(
        Check(
            "schema_version",
            isinstance(version, int) and not isinstance(version, bool) and version == 1,
            f"got {version!r}, want 1",
        )
    )
    policy = payload.get("policy")
    checks.append(Check("policy", policy == "ADR-010", f"got {policy!r}, want 'ADR-010'"))

    block = payload.get("edit_distance_threshold")
    threshold_ok = isinstance(block, dict) and block.get("method") == "xor@128" and all(
        key in block for key in ("min_changed_pixels", "max_changed_pixels",
                                "max_changed_ratio", "value_status")
    )
    checks.append(
        Check(
            "edit_distance_threshold",
            bool(threshold_ok),
            f"method xor@128, status {_threshold_status(payload)} (UNSET is allowed and expected "
            "until the 20 masks are re-made)",
        )
    )
    if threshold_ok and _threshold_status(payload) == "SET":
        mn = block.get("min_changed_pixels")
        mx = block.get("max_changed_pixels")
        mr = block.get("max_changed_ratio")
        consistent = (
            _is_number(mn) and _is_number(mx) and _is_number(mr)
            and mn <= mx and isinstance(mr, int | float) and mr >= 0
        )
        checks.append(
            Check(
                "edit_distance_threshold consistency",
                bool(consistent),
                f"SET bounds must satisfy 0 <= min <= max and max_ratio >= 0 "
                f"(got min={mn}, max={mx}, ratio={mr})",
            )
        )

    expected = payload.get("expected_ids")
    expected_ok = (
        isinstance(expected, list)
        and len(expected) == EXPECTED_SLOTS
        and len(set(expected)) == EXPECTED_SLOTS
        and all(isinstance(item, str) and item.strip() for item in expected)
    )
    checks.append(
        Check(
            "expected_ids",
            bool(expected_ok),
            f"{len(expected) if isinstance(expected, list) else 'n/a'} unique ids, want 20",
        )
    )

    raw_records = payload.get("records")
    records = _records(payload)
    checks.append(
        Check(
            "records count",
            isinstance(raw_records, list) and len(records) == EXPECTED_SLOTS,
            f"{len(records)} records, want {EXPECTED_SLOTS}",
        )
    )
    record_ids = [record.get("id") for record in records]
    checks.append(
        Check(
            "records match expected_ids",
            bool(expected_ok) and record_ids == expected,
            "id order equals expected_ids" if record_ids == expected
            else "record ids differ from expected_ids (or expected_ids is invalid)",
        )
    )

    for index, record in enumerate(records):
        checks.extend(_record_checks(record, index, block))
    return checks


def _finalise(checks: list[Check], payload: dict, notes: list[str] | None = None) -> ReviewReport:
    """Apply ADR-010's u1_met formula to a list of checks."""
    records = _records(payload)
    reviewed = sum(1 for record in records if record.get("status") == "reviewed")
    pending = sum(1 for record in records if record.get("status") == "pending")
    expected = payload.get("expected_ids")
    slots = len(expected) if isinstance(expected, list) else len(records)
    threshold_status = _threshold_status(payload)
    all_ok = bool(checks) and all(check.ok for check in checks)
    u1_met = reviewed == slots == EXPECTED_SLOTS and threshold_status != "UNSET" and all_ok
    return ReviewReport(
        checks=checks,
        reviewed=reviewed,
        pending=pending,
        slots=slots,
        u1_met=u1_met,
        threshold_status=threshold_status,
        notes=list(notes or ()),
    )


def validate_structure(payload: dict) -> ReviewReport:
    """CI/clean-clone mode. Reads no manifest, no binaries, no credentials."""
    return _finalise(_structure_checks(payload), payload)


# ------------------------------------------------------------------ optional local checks


def _manifest_checks(payload: dict, manifest_path: Path) -> list[Check]:
    """Read-only text reconciliation. A missing manifest is a SKIP, never a failure."""
    path = Path(manifest_path)
    if not path.is_file():
        return [
            Check(
                "manifest cross-check",
                True,
                "manifest absent (clean clone) — cross-check skipped",
            )
        ]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [
            Check("manifest cross-check", False,
                  f"manifest unreadable: {type(exc).__name__}: {exc}")
        ]
    if not isinstance(manifest, list):
        return [Check("manifest cross-check", False, "manifest is not a JSON array")]
    masked_ids = sorted(
        str(entry["id"])
        for entry in manifest
        if isinstance(entry, dict) and entry.get("id") and entry.get("mask_path")
    )
    expected = payload.get("expected_ids")
    expected_sorted = sorted(expected) if isinstance(expected, list) else []
    ok = masked_ids == expected_sorted and len(masked_ids) == EXPECTED_SLOTS
    return [
        Check(
            "manifest cross-check",
            ok,
            f"manifest has {len(masked_ids)} entries with mask_path; expected_ids has "
            f"{len(expected_sorted)}; "
            + ("ids match" if ok else "ids differ — local index is out of date"),
        )
    ]


def _local_mask_checks(payload: dict, golden_dir: Path) -> list[Check]:
    """B2: local, human-run verification of `reviewed` rows. PIL/numpy are imported ONLY here.

    Three independent facts are checked against the register: the two **raw PNG byte** sha256
    digests (file identity), the **XOR@128** `changed_pixels` (pixel edit distance), and
    `changed_ratio = changed_pixels / (H * W)` (whole-frame denominator). A shape mismatch is a
    named failing check carrying the entry id and both sizes — never a silent resize.
    """
    records = [
        record
        for record in _records(payload)
        if record.get("status") == "reviewed" and isinstance(record.get("proposal"), dict)
    ]
    if not records:
        return [
            Check(
                "local mask verification",
                True,
                "0 reviewed masks — nothing to verify (pending register / clean clone)",
            )
        ]
    try:
        from PIL import Image  # local: the CI/default path must not need Pillow
    except ImportError as exc:  # pragma: no cover - Pillow is a declared dependency
        return [Check("local mask verification", False, f"Pillow unavailable: {exc}")]

    checks: list[Check] = []
    for record in records:
        rid = str(record.get("id", "<no id>"))
        proposal = record["proposal"]
        reviewed = record["reviewed"]
        proposal_path = Path(golden_dir) / str(proposal.get("mask_path", ""))
        reviewed_path = Path(golden_dir) / str(reviewed.get("mask_path", ""))
        if not proposal_path.is_file() or not reviewed_path.is_file():
            checks.append(
                Check(f"local mask {rid}", False, "proposal or reviewed PNG is missing")
            )
            continue

        # (1) File identity: sha256 of the RAW PNG BYTES, not of a binarised ndarray.
        proposal_digest = hashlib.sha256(proposal_path.read_bytes()).hexdigest()
        reviewed_digest = hashlib.sha256(reviewed_path.read_bytes()).hexdigest()
        expected_digests = (proposal.get("sha256"), reviewed.get("sha256"))
        hashes_ok = expected_digests == (proposal_digest, reviewed_digest)
        checks.append(
            Check(
                f"local mask {rid} sha256",
                hashes_ok,
                "raw PNG byte digests match the register" if hashes_ok
                else f"raw PNG byte digests differ from the register for {rid}",
            )
        )

        try:
            with Image.open(proposal_path) as left, Image.open(reviewed_path) as right:
                left_gray = left.convert("L")
                right_gray = right.convert("L")
                if left_gray.size != right_gray.size:
                    # Same rule as seg_iou.run(): name the entry and both shapes; never resize.
                    checks.append(
                        Check(
                            f"local mask {rid} shape",
                            False,
                            f"{rid}: proposal is {left_gray.size} but reviewed mask is "
                            f"{right_gray.size} — XOR is only defined on equal shapes",
                        )
                    )
                    continue
                import numpy as np  # local: only this verification path needs numpy

                pred = np.asarray(left_gray) >= 128
                ref = np.asarray(right_gray) >= 128
                # (2) XOR count. On binary masks this equals union - intersection, but it is
                # deliberately written as XOR so nobody conflates edit distance with IoU.
                changed = int(np.count_nonzero(pred ^ ref))
                total_pixels = int(pred.size)  # H * W, the WHOLE frame
        except (OSError, ValueError) as exc:
            checks.append(Check(f"local mask {rid}", False, f"undecodable PNG: {exc}"))
            continue

        distance = record.get("edit_distance")
        recorded_pixels = distance.get("changed_pixels") if isinstance(distance, dict) else None
        recorded_ratio = distance.get("changed_ratio") if isinstance(distance, dict) else None
        pixels_ok = _is_number(recorded_pixels) and int(recorded_pixels) == changed
        checks.append(
            Check(
                f"local mask {rid} xor@128",
                bool(pixels_ok),
                f"xor@128 changed_pixels={changed}" if pixels_ok
                else f"xor@128 changed_pixels {changed} != recorded {recorded_pixels!r}",
            )
        )

        # (3) changed_ratio denominator is H * W, not the union and not the garment region.
        computed_ratio = changed / total_pixels if total_pixels else 0.0
        ratio_ok = _is_number(recorded_ratio) and (
            abs(float(recorded_ratio) - computed_ratio) <= _RATIO_TOLERANCE
        )
        checks.append(
            Check(
                f"local mask {rid} changed_ratio",
                bool(ratio_ok),
                f"changed_ratio={computed_ratio:.6f} over {total_pixels} pixels" if ratio_ok
                else f"changed_ratio {recorded_ratio!r} != recomputed {computed_ratio:.6f} "
                     f"(denominator H*W={total_pixels})",
            )
        )
    return checks


def cross_check_manifest(
    payload: dict, manifest_path: Path = DEFAULT_GOLDEN / "manifest.json"
) -> ReviewReport:
    """Structure + a local, read-only manifest reconciliation. Never writes anything."""
    return _finalise(
        _structure_checks(payload) + _manifest_checks(payload, manifest_path), payload
    )


def verify_local_masks(payload: dict, golden_dir: Path = DEFAULT_GOLDEN) -> ReviewReport:
    """Structure + optional local pixel verification for `reviewed` rows."""
    return _finalise(
        _structure_checks(payload) + _local_mask_checks(payload, golden_dir), payload
    )


# ------------------------------------------------------------------------------ rendering


def render(report: ReviewReport) -> str:
    """Plain text; `U1 UNMET` is derived from the present-tense register state."""
    lines = [
        "# ADR-010 point-review register",
        "",
        f"policy: ADR-010 · slots: {report.slots} · reviewed: {report.reviewed} · "
        f"pending: {report.pending} · edit-distance threshold: {report.threshold_status}",
        "",
        "| check | result | detail |",
        "|---|---|---|",
    ]
    for check in report.checks:
        detail = check.detail.replace("|", "\\|")
        lines.append(
            f"| {check.name} | {'PASS' if check.ok else 'FAIL'} | {detail} |"
        )
    lines += [
        "",
        f"Register state: {report.slots}/{report.slots} slots registered, "
        f"{report.reviewed}/{report.slots} point-reviewed, "
        f"U1 {'MET' if report.u1_met else 'UNMET'} "
        f"(edit-distance threshold {report.threshold_status}).",
        "",
        "The **not a PASS** verdict follows from this present-tense register state, not from "
        "the mask-source tier. `python -m evals.tools.validate_point_reviews` exit 0 means the "
        "register is structurally valid; it is NOT a DoD verdict. U1 is the human sign-off encoded "
        "in the edit-distance threshold; with the threshold SET and all 20 rows reviewed, u1_met "
        "is True by the ADR-010 formula — but that is a structural verdict from this ledger, not "
        "an adjudication of BiRefNet quality.",
        "",
        "ADR-010 does not pre-adjudicate the IoU semantics after the 20 masks are re-made; "
        "that is a follow-up ticket.",
    ]
    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in report.notes]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.tools.validate_point_reviews")
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument(
        "--cross-check-manifest",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="optional local read-only reconciliation (default: evals/golden/manifest.json)",
    )
    parser.add_argument(
        "--verify-local-masks",
        nargs="?",
        const="",
        default=None,
        metavar="DIR",
        help="optional local XOR@128 pixel verification (default: evals/golden)",
    )
    args = parser.parse_args(argv)

    try:
        payload = load_register(args.register)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(
            f"point-review register unusable: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    checks = _structure_checks(payload)
    notes: list[str] = []
    if args.cross_check_manifest is not None:
        manifest_path = (
            Path(args.cross_check_manifest)
            if args.cross_check_manifest
            else DEFAULT_GOLDEN / "manifest.json"
        )
        checks += _manifest_checks(payload, manifest_path)
        notes.append("local --cross-check-manifest requested; this mode is not available in CI")
    if args.verify_local_masks is not None:
        golden_dir = Path(args.verify_local_masks) if args.verify_local_masks else DEFAULT_GOLDEN
        checks += _local_mask_checks(payload, golden_dir)
        notes.append("local --verify-local-masks requested; this mode is not available in CI")
    report = _finalise(checks, payload, notes)
    print(render(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
