"""Companion kit for the future ticket that re-makes the 20 golden masks (ADR-010).

WHAT THIS IS
------------
Mechanical assistance for the SOP in `evals/golden-review/README.md`: it computes the two
raw-PNG sha256 digests, the `xor@128` pixel edit distance, and validates mask format. It removes
the arithmetic from the human's workload. Three subcommands:

    python -m evals.tools.mask_review_kit measure --id top-01     # read-only, prints JSON
    python -m evals.tools.mask_review_kit measure --all           # read-only, one JSON per id
    python -m evals.tools.mask_review_kit record  --id top-01 \
        --model sam2 --model-version 2.1 --reviewer bert \
        --review-id PR-010-top-01 --points-reviewed 412 \
        --proposal-source ai-assisted:sam2+point-reviewed --attest
    python -m evals.tools.mask_review_kit record  --id top-01 ... --attest --write
    python -m evals.tools.mask_review_kit check                    # read-only batch format check

WHAT THIS IS NOT (deliberately, not by omission)
-----------------------------------------------
* **It does not certify a human review.** `--attest` is a human's explicit statement that they
  reviewed that mask point by point. `reviewer`, `review_id` and `points_reviewed` are required
  arguments that this tool NEVER generates or infers. Without `--attest` the record stays
  `pending` and `record` refuses to write. ADR-010 §6: the last step of U1 is a human sign-off.
* **It does not relabel a proposal's provenance.** `--proposal-source` is a required human
  declaration, classified by `evals.mask_provenance.classify()`; MACHINE (`algorithmic:*`,
  historical `ai:*`) and HAND proposals are refused, never written as
  `ai-assisted:<slug>+point-reviewed` (ADR-010 N2). Measured pixels never substitute for it.
* **It does not decide U1.** It never touches `edit_distance_threshold`, never sets `u1_met`,
  never produces or back-fills an IoU number, and never changes `manifest.json`. While the
  threshold is UNSET, 20 `reviewed` rows still leave U1 UNMET by construction.
* **It is not a CI gate.** No `ci.yml` step, no `make golden` integration. It reads the local
  copyright-frozen PNGs but never copies, stages or commits any binary. Default paths never
  open `manifest.json`, so a clean clone can still run `check` (missing binaries are reported,
  not fabricated).
* `exit 0` means "the mechanical checks passed on the files present" — nothing about DoD.

Format checks are stricter than `validate_golden.py` on mode: this kit requires `mode == "L"`
(the validator also accepts `"1"`). Pixel values are a different story: `evals/golden/README.md`
says "soft edges welcome but not required", and B2 binarises at 128, so **anti-aliased grey
values are allowed by the contract**. They are reported as INFO by default and only become a
failure under `--strict-binary`. The validator's own pixel-value-set gap is a separate ticket;
this tool does not change the validator's behaviour.

Pillow and numpy are imported lazily inside the pixel path only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals.mask_provenance import (
    AI_ASSISTED_PREFIX,
    AI_ASSISTED_RE,
    POINT_REVIEWED_SUFFIX,
    MaskSourceTier,
    classify,
    parse_model_slug,
)
from evals.tools.validate_point_reviews import (
    DEFAULT_GOLDEN,
    DEFAULT_REGISTER,
    load_register,
    validate_structure,
)

ROOT = Path(__file__).resolve().parents[2]
_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
_REVIEW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RATIO_DECIMALS = 4


@dataclass(frozen=True, slots=True)
class Measurement:
    """Purely mechanical facts about one mask pair. No human judgement is represented here."""

    rid: str
    proposal_path: str
    reviewed_path: str
    proposal_sha256: str | None
    reviewed_sha256: str | None
    changed_pixels: int | None
    changed_ratio: float | None
    width: int
    height: int
    problems: tuple[str, ...]
    notes: tuple[str, ...]

    def as_evidence(self) -> dict:
        """The fields that can be written to the register without any human claim."""
        return {
            "proposal_sha256": self.proposal_sha256,
            "reviewed_sha256": self.reviewed_sha256,
            "edit_distance": {
                "method": "xor@128",
                "changed_pixels": self.changed_pixels,
                "changed_ratio": self.changed_ratio,
            },
        }


def _sha256(path: Path) -> str:
    """Hash the RAW PNG file bytes, never a binarised ndarray (ADR-010 §5)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mask_problems(
    path: Path, photo_path: Path | None, *, strict_binary: bool = False
) -> tuple[list[str], tuple[int, int], list[str]]:
    """Format checks. Returns (problems, size, notes).

    `evals/golden/README.md` allows soft edges ("soft edges welcome but not required") and B2
    binarises at 128, so anti-aliased grey values are INFO by default. `--strict-binary` is the
    opt-in for a strictly {0,255} mask; without it we must not fail a contract-compliant mask.
    """
    problems: list[str] = []
    notes: list[str] = []
    size = (0, 0)
    try:
        from PIL import Image  # local: the non-pixel paths must not need Pillow
    except ImportError as exc:  # pragma: no cover
        return [f"Pillow unavailable: {exc}"], size, notes
    try:
        with Image.open(path) as opened:
            opened.load()
            size = opened.size
            if opened.mode != "L":
                problems.append(f"mode {opened.mode}, wants L (8-bit single channel)")
            import numpy as np  # local: only the pixel path needs numpy

            values = set(np.unique(np.asarray(opened.convert("L"))).tolist())
            if not values <= {0, 255}:
                message = f"soft edges present ({len(values)} distinct values); allowed by " \
                          "evals/golden/README.md, B2 binarises at 128"
                if strict_binary:
                    problems.append(f"not strictly {{0,255}}: {message}")
                else:
                    notes.append(message)
    except (OSError, ValueError) as exc:
        return [f"undecodable PNG: {exc}"], size, notes
    if photo_path is not None and photo_path.is_file():
        try:
            with Image.open(photo_path) as photo:
                if photo.size != size:
                    problems.append(f"mask {size} != photo {photo.size}")
        except (OSError, ValueError) as exc:
            problems.append(f"photo undecodable: {exc}")
    return problems, size, notes


def measure(
    rid: str, golden_dir: Path, payload: dict, *, strict_binary: bool = False
) -> Measurement:
    """Read-only: compute hashes, XOR@128 edit distance, and format problems for one id."""
    record = next(
        (rec for rec in payload.get("records", []) if str(rec.get("id")) == rid), None
    )
    if record is None:
        raise ValueError(f"{rid!r} is not in the register's records")

    proposal_rel = str(record["proposal"]["mask_path"])
    reviewed_rel = str(record["reviewed"]["mask_path"])
    proposal_path = golden_dir / proposal_rel
    reviewed_path = golden_dir / reviewed_rel
    photo_path = golden_dir / "photos" / f"{rid}.jpg"
    png_photo = golden_dir / "photos" / f"{rid}.png"

    problems: list[str] = []
    notes: list[str] = []
    for label, path in (("proposal", proposal_path), ("reviewed", reviewed_path)):
        if not path.is_file():
            shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            problems.append(f"{label} PNG missing at {shown}")
    photo = photo_path if photo_path.is_file() else (png_photo if png_photo.is_file() else None)
    if photo is None:
        notes.append("photo absent (clean clone) — size check skipped")

    if any("PNG missing" in problem or "undecodable" in problem for problem in problems):
        return Measurement(
            rid, proposal_rel, reviewed_rel, None, None, None, None, 0, 0,
            tuple(problems), tuple(notes),
        )

    prob_mask, size, mask_notes = _mask_problems(
        reviewed_path, photo, strict_binary=strict_binary
    )
    problems.extend(prob_mask)
    notes.extend(mask_notes)
    prob_proposal, prop_size, prop_notes = _mask_problems(
        proposal_path, None, strict_binary=strict_binary
    )
    problems.extend(f"proposal: {problem}" for problem in prob_proposal)
    notes.extend(f"proposal: {note}" for note in prop_notes)
    if size and prop_size and size != prop_size:
        problems.append(f"proposal {prop_size} != reviewed {size} — XOR needs equal shapes")

    proposal_sha = _sha256(proposal_path)
    reviewed_sha = _sha256(reviewed_path)
    changed: int | None = None
    ratio: float | None = None
    if size and prop_size and size == prop_size:
        try:
            import numpy as np  # local
            from PIL import Image  # local

            with Image.open(proposal_path) as left, Image.open(reviewed_path) as right:
                pred = np.asarray(left.convert("L")) >= 128
                ref = np.asarray(right.convert("L")) >= 128
            changed = int(np.count_nonzero(pred ^ ref))
            total = int(pred.size)  # whole frame H*W, not the union
            ratio = round(changed / total, _RATIO_DECIMALS) if total else 0.0
        except (OSError, ValueError, ImportError) as exc:
            problems.append(f"XOR computation failed: {exc}")

    return Measurement(
        rid, proposal_rel, reviewed_rel, proposal_sha, reviewed_sha,
        changed, ratio, size[0], size[1], tuple(problems), tuple(notes),
    )


def build_record(
    base: dict,
    measured: Measurement,
    *,
    model: str,
    model_version: str,
    reviewer: str,
    review_id: str,
    points_reviewed: int,
    reviewed_at: str,
    proposal_source: str,
) -> dict:
    """A `reviewed` record. Only `--attest` (outside this function) permits its creation.

    The human-supplied fields are passed through verbatim; nothing here infers that a human
    looked at the mask. `proposal_source` is a required human declaration of the proposal's
    ACTUAL provenance: `classify()` (the single source of truth in evals.mask_provenance)
    must place it in AI_ASSISTED_REVIEWED. MACHINE and HAND tiers are refused rather than
    relabelled (ADR-010 N2). `provenance` is built from the model slug with the ADR-010 regex.
    """
    if not _SLUG_RE.fullmatch(model):
        raise ValueError(f"model slug {model!r} must match [a-z0-9_-]+ (no '.', ';', ',', ':')")
    if not model_version.strip():
        raise ValueError("model_version must be a non-empty full version string")
    if not reviewer.strip() or not _REVIEW_ID_RE.fullmatch(review_id):
        raise ValueError("reviewer and a valid review_id are required (human-supplied)")
    if points_reviewed < 1:
        raise ValueError("points_reviewed must be >= 1 (a human-supplied count)")
    if measured.proposal_sha256 is None or measured.reviewed_sha256 is None \
            or measured.changed_pixels is None or measured.changed_ratio is None:
        raise ValueError(f"{measured.rid}: measurement incomplete; cannot record")

    tier = classify(proposal_source).tier
    if tier is MaskSourceTier.MACHINE:
        raise ValueError(
            f"proposal_source {proposal_source!r} is MACHINE tier: machine-tier proposal "
            "cannot be recorded as ai-assisted point-reviewed (ADR-010 N2); declare the "
            f"fully qualified {AI_ASSISTED_PREFIX}<slug>{POINT_REVIEWED_SUFFIX} source"
        )
    if tier is MaskSourceTier.HAND:
        raise ValueError(
            f"proposal_source {proposal_source!r} is HAND tier: a hand-drawn mask cannot be "
            "recorded as ai-assisted point-reviewed (ADR-010 N2); register 'reviewed' rows "
            "accept only AI_ASSISTED_REVIEWED proposals"
        )
    declared_slug = parse_model_slug(proposal_source)
    if declared_slug != model:
        raise ValueError(
            f"--model {model!r} does not match the proposal source slug {declared_slug!r}; "
            "the tool never infers provenance (ADR-010 N2)"
        )

    provenance = f"{AI_ASSISTED_PREFIX}{model}{POINT_REVIEWED_SUFFIX}"
    if proposal_source.strip() != provenance:
        raise ValueError(
            f"proposal_source {proposal_source!r} must be the canonical human declaration "
            f"{provenance!r}; the tool never normalises provenance (ADR-010 N2)"
        )
    if not AI_ASSISTED_RE.fullmatch(provenance) or parse_model_slug(provenance) != model:
        raise ValueError(f"constructed provenance {provenance!r} violates ADR-010 prefix")

    record = dict(base)
    record.update(
        status="reviewed",
        provenance=provenance,
        proposal={
            "model": model,
            "model_version": model_version,
            "mask_path": measured.proposal_path,
            "sha256": measured.proposal_sha256,
        },
        reviewed={
            "mask_path": measured.reviewed_path,
            "sha256": measured.reviewed_sha256,
        },
        edit_distance={
            "method": "xor@128",
            "changed_pixels": measured.changed_pixels,
            "changed_ratio": measured.changed_ratio,
        },
        review={
            "review_id": review_id,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "points_reviewed": points_reviewed,
        },
    )
    return record


def write_record(payload: dict, record: dict, register: Path) -> None:
    """Write back ONLY the target record. Rejects if the result breaks structure or threshold."""
    before_threshold = payload.get("edit_distance_threshold")
    target = str(record.get("id"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("register has no records array")
    index = next((i for i, rec in enumerate(records) if str(rec.get("id")) == target), None)
    if index is None:
        raise ValueError(f"{target!r} not found in register")

    updated = dict(payload)
    updated["records"] = [dict(rec) for rec in records]
    updated["records"][index] = record
    if updated.get("edit_distance_threshold") != before_threshold:
        raise ValueError("refusing to write: edit_distance_threshold would change")
    report = validate_structure(updated)
    if not report.passed:
        failures = ", ".join(check.name for check in report.checks if not check.ok)
        raise ValueError(f"refusing to write: register would be invalid ({failures})")
    register.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {target} to {register} — status=reviewed; threshold UNCHANGED; U1 still UNMET")


def _print_measurement(measured: Measurement) -> None:
    print(json.dumps({
        "id": measured.rid,
        "evidence": measured.as_evidence(),
        "size": [measured.width, measured.height],
        "problems": list(measured.problems),
        "notes": list(measured.notes),
    }, indent=2))


def _cmd_measure(args: argparse.Namespace) -> int:
    payload = load_register(args.register)
    ids = [str(rec.get("id")) for rec in payload.get("records", [])]
    targets = ids if args.all else [args.id]
    if not targets or targets == [None]:
        print("measure needs --id ID or --all", file=sys.stderr)
        return 2
    bad = False
    for rid in targets:
        try:
            measured = measure(rid, args.golden_dir, payload, strict_binary=args.strict_binary)
        except (ValueError, KeyError) as exc:
            print(json.dumps({"id": rid, "error": str(exc)}), file=sys.stderr)
            bad = True
            continue
        _print_measurement(measured)
        bad = bad or bool(measured.problems)
    return 1 if bad else 0


def _cmd_record(args: argparse.Namespace) -> int:
    if not args.attest:
        print(
            "REFUSED: --attest is required. This flag is a human's explicit statement that "
            "they reviewed this mask point by point. A tool cannot certify a human review "
            "(ADR-010 §6); without it the record stays pending and nothing is written.",
            file=sys.stderr,
        )
        return 2
    payload = load_register(args.register)
    try:
        measured = measure(
            args.id, args.golden_dir, payload, strict_binary=args.strict_binary
        )
        if measured.problems:
            print("REFUSED: format problems: " + "; ".join(measured.problems), file=sys.stderr)
            return 1
        base = next(rec for rec in payload["records"] if str(rec.get("id")) == args.id)
        record = build_record(
            base, measured,
            model=args.model, model_version=args.model_version,
            reviewer=args.reviewer, review_id=args.review_id,
            points_reviewed=args.points_reviewed,
            reviewed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            proposal_source=args.proposal_source,
        )
    except (ValueError, KeyError, StopIteration) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    print("AUTO-COMPUTED (mechanical): sha256 x2, xor@128 changed_pixels, changed_ratio.")
    print("HUMAN-SUPPLIED (not verified by this tool): reviewer, review_id, points_reviewed,")
    print("  model, model_version, and the --attest statement itself.")
    print("This is social evidence, not cryptographic proof. U1's last step is a human")
    print("sign-off; the threshold is UNSET, so u1_met stays False even with 20 reviewed rows.")
    if args.write:
        try:
            write_record(payload, record, args.register)
        except (ValueError, OSError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
    else:
        print("--write not given: nothing was written. The record would be:")
        print(json.dumps(record, indent=2))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    payload = load_register(args.register)
    ids = list(payload.get("expected_ids", []))
    records = {str(rec.get("id")): rec for rec in payload.get("records", [])}
    problems: list[str] = []
    if len(ids) != 20 or len(set(ids)) != 20:
        problems.append(f"expected_ids has {len(ids)} entries / {len(set(ids))} unique; wants 20")
    if set(ids) != set(records):
        problems.append("expected_ids and records ids do not match")

    rows: list[str] = []
    for rid in ids:
        record = records.get(rid)
        if record is None:
            problems.append(f"{rid}: no record")
            continue
        rel = str(record["reviewed"]["mask_path"])
        mask = args.golden_dir / rel
        photo = args.golden_dir / "photos" / f"{rid}.jpg"
        png_photo = args.golden_dir / "photos" / f"{rid}.png"
        photo_path = photo if photo.is_file() else (png_photo if png_photo.is_file() else None)
        if not mask.is_file():
            problems.append(f"{rid}: reviewed mask missing ({rel})")
            rows.append(f"| {rid} | MISSING | — |")
            continue
        found, size, notes = _mask_problems(
            mask, photo_path, strict_binary=args.strict_binary
        )
        proposal = args.golden_dir / str(record["proposal"]["mask_path"])
        if not proposal.is_file():
            found.append(f"proposal mask missing ({record['proposal']['mask_path']})")
        status = str(record.get("status", "?"))
        detail = "; ".join(found) if found else (
            "L mode, size ok" + (f"; INFO: {'; '.join(notes)}" if notes else "")
        )
        rows.append(f"| {rid} | {'PASS' if not found else 'FAIL'} | {size} {status} {detail} |")
        problems.extend(f"{rid}: {problem}" for problem in found)

    mode = "strict {0,255}" if args.strict_binary else "contract (soft edges allowed)"
    print(f"# mask format check (read-only; mode: {mode})")
    print("")
    print("| id | result | detail |")
    print("|---|---|---|")
    for row in rows:
        print(row)
    print("")
    print(f"{len(ids)} ids · {len(problems)} problem(s) · threshold "
          f"{payload.get('edit_distance_threshold', {}).get('value_status', '?')}")
    print("This check proves file format only. It does NOT verify a human review, decide U1,")
    print("produce an IoU number, or change any file. On a clean clone the binaries are absent")
    print("and are reported as missing, never fabricated.")
    if not args.strict_binary:
        print("Soft edges are INFO, not failures: evals/golden/README.md allows them and B2")
        print("binarises at 128. Use --strict-binary to require a strictly {0,255} mask.")
    if problems:
        print("")
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.tools.mask_review_kit",
        description="Mechanical companion for re-making the 20 masks. "
        "NOT a CI gate, NOT a U1 verdict.",
    )
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--strict-binary",
        action="store_true",
        help="FAIL masks with anti-aliased grey values (contract allows soft edges by default)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_measure = sub.add_parser("measure", help="read-only hashes + XOR@128 + format")
    group = p_measure.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", help="one register id")
    group.add_argument("--all", action="store_true", help="all 20 ids")
    p_measure.set_defaults(func=_cmd_measure)

    p_record = sub.add_parser("record", help="build (and optionally write) a reviewed record")
    p_record.add_argument("--id", required=True)
    p_record.add_argument("--model", required=True, help="slug [a-z0-9_-]+, no dots/colons")
    p_record.add_argument("--model-version", required=True, help="full version string, e.g. 2.1")
    p_record.add_argument("--reviewer", required=True, help="HUMAN-supplied reviewer")
    p_record.add_argument("--review-id", required=True, help="HUMAN-supplied review id")
    p_record.add_argument("--points-reviewed", required=True, type=int,
                          help="HUMAN-supplied count of points reviewed")
    p_record.add_argument(
        "--proposal-source", required=True,
        help="HUMAN declaration of the proposal's ACTUAL provenance: the fully qualified "
             "ai-assisted:<slug>+point-reviewed string. The tool NEVER infers it from the "
             "pixels; MACHINE (algorithmic:*, ai:*) and HAND sources are refused (ADR-010 N2).",
    )
    p_record.add_argument("--attest", action="store_true",
                          help="explicit human statement that a point-by-point review happened")
    p_record.add_argument("--write", action="store_true",
                          help="write the single record back (default: print only)")
    p_record.set_defaults(func=_cmd_record)

    p_check = sub.add_parser("check", help="read-only batch format check of the 20 masks")
    p_check.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
