"""E0-T1 tooling: validate the golden set before anyone quotes a number from it.

The E0-T1 golden set is 40 real garment photos with 20 hand-drawn masks and a manifest.
This tool checks that 20 mask files are present; it does NOT verify they were drawn by hand
(see mask_source in manifest.json). The photos are not something this tool can produce — and
fabricating them is exactly what `docs/PLAN.md` E0-T1 forbids. What the tool *can* do is make
the contract mechanical: given the directory, it says PASS/FAIL for every rule, names the counts,
and refuses to let a half-valid set become the base of an eval report.

    python -m evals.tools.validate_golden [--golden-dir PATH]

Exit codes: 0 complete, 1 incomplete (missing/mismatched files or counts), 2 the manifest
itself is unusable (absent, not JSON, not an array). "Missing" and "broken" are different
fixes, so they get different exit codes — the same reasoning as the eval gates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from apps.api.schemas import GarmentCategory, Pattern

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN = ROOT / "evals" / "golden"

# Composition rules — see evals/golden/README.md for the interpretation of PLAN E0-T1.
TOTAL_ENTRIES = 40
TOTAL_MASKS = 20
HARD_CASES = 5
BASE_QUOTAS: dict[str, int] = {
    "top": 10,
    "bottom": 10,
    "dress": 5,
    "shoe": 5,
    "accessory": 5,
}

# Profiles. `golden` is the E0-T1 contract and the default everywhere. `provisional` is for
# `evals/golden-provisional/` — a web-sourced stand-in whose whole point is to exercise the
# metric while the real set waits for Bert's photos. It checks structure only: the files are
# promised, the vocabulary is right, every entry carries a mask. It does **not** invent a
# composition rule the source could not satisfy, because a set that pretends to be the
# golden set is worse than one that says out loud that it is not.
PROFILES = ("golden", "provisional")
PROVISIONAL_MIN_ENTRIES = 10

# Tokens that smell like a fabricated fixture. The tool cannot prove a photo is real; it can
# refuse the obvious placeholder that would otherwise make every number downstream a fiction.
FORBIDDEN_TOKENS = ("placeholder", "synthetic", "fake", "dummy", "todo", "lorem", "sample")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REQUIRED = ("id", "path", "category", "colors", "pattern", "material", "hard_attributes")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    fatal: bool = False  # the manifest itself is unusable, not merely incomplete
    profile: str = "golden"

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name=name, ok=ok, detail=detail))

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    def render(self, golden_dir: Path) -> str:
        lines = [f"golden set check — {golden_dir}"]
        if self.profile != "golden":
            lines += [
                "",
                f"*** profile: {self.profile.upper()} — composition is NOT checked. A set "
                "validated here is not the E0-T1 golden set and cannot stand in for it. ***",
            ]
        lines.append("")
        width = max((len(check.name) for check in self.checks), default=0)
        for check in self.checks:
            mark = "PASS" if check.ok else "FAIL"
            lines.append(f"  [{mark}] {check.name.ljust(width)}  {check.detail}")
        failed = [check for check in self.checks if not check.ok]
        lines.append("")
        if self.passed:
            if self.profile == "golden":
                lines.append(
                    "summary: STRUCTURE COMPLETE — the file/count checks above hold. "
                    "Mask provenance is NOT verified by this tool (see the reported "
                    "mask_source tally); E0-T1's hand-drawn requirement is UNVERIFIED and "
                    "make golden exit 0 is NOT a golden-set DoD verdict."
                )
            else:
                lines.append(
                    "summary: COMPLETE (provisional profile) — structure holds. Composition "
                    "and mask provenance were not verified; see the directory's README."
                )
        else:
            lines.append(f"summary: INCOMPLETE — {len(failed)} blocking problem(s).")
        return "\n".join(lines)


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _contains_forbidden(text: str) -> str | None:
    lowered = text.lower()
    for token in FORBIDDEN_TOKENS:
        if token in lowered:
            return token
    return None


def validate(golden_dir: Path, profile: str = "golden") -> Report:
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}, got {profile!r}")
    strict = profile == "golden"
    report = Report(profile=profile)
    manifest_path = golden_dir / "manifest.json"

    if not manifest_path.exists():
        report.record(
            "manifest present",
            False,
            f"{manifest_path} does not exist — the golden set is empty by design "
            "(see evals/golden/README.md); every metric stays UNMEASURED until it does",
        )
        report.fatal = True
        return report

    try:
        payload = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        report.record("manifest parses", False, f"invalid JSON: {exc}")
        report.fatal = True
        return report

    if not isinstance(payload, list):
        report.record(
            "manifest is an array",
            False,
            f"top-level type is {type(payload).__name__}; it must be a bare array so "
            "`jq 'length'` prints the entry count (see README)",
        )
        report.fatal = True
        return report

    report.record("manifest is an array", True, f"{len(payload)} entries")
    if strict:
        report.record(
            f"entry count is {TOTAL_ENTRIES}",
            len(payload) == TOTAL_ENTRIES,
            f"{len(payload)}/{TOTAL_ENTRIES}",
        )
    else:
        report.record(
            f"at least {PROVISIONAL_MIN_ENTRIES} entries",
            len(payload) >= PROVISIONAL_MIN_ENTRIES,
            f"{len(payload)} entries (no composition requirement in this profile)",
        )

    # --- per-entry structure -----------------------------------------------------------
    entries = [entry for entry in payload if isinstance(entry, dict)]
    if len(entries) != len(payload):
        report.record(
            "every entry is an object",
            False,
            f"{len(payload) - len(entries)} entries are not objects",
        )
        return report

    missing_fields: list[str] = []
    for index, entry in enumerate(entries):
        for name in _REQUIRED:
            if name not in entry:
                missing_fields.append(f"#{index} missing {name}")
    report.record(
        "required fields present",
        not missing_fields,
        "; ".join(missing_fields[:5]) + (" …" if len(missing_fields) > 5 else "")
        if missing_fields
        else f"all {len(entries)} entries carry {len(_REQUIRED)} fields",
    )

    ids = [entry.get("id") for entry in entries]
    bad_ids = [value for value in ids if not (isinstance(value, str) and _ID_RE.match(value))]
    duplicates = sorted({value for value in ids if isinstance(value, str) and ids.count(value) > 1})
    report.record(
        "ids are unique slugs",
        not bad_ids and not duplicates,
        (
            f"bad: {bad_ids[:3]}"
            if bad_ids
            else (f"duplicates: {duplicates[:5]}" if duplicates else "ok")
        ),
    )

    leaky = []
    for entry in entries:
        for name in ("id", "path"):
            token = _contains_forbidden(str(entry.get(name, "")))
            if token:
                leaky.append(f"{entry.get('id', '?')} contains '{token}'")
    report.record(
        "no placeholder-looking entries",
        not leaky,
        "; ".join(leaky[:5]) if leaky else "no forbidden tokens in ids or paths",
    )

    # --- vocabulary --------------------------------------------------------------------
    bad_categories = sorted(
        {
            str(entry.get("category"))
            for entry in entries
            if entry.get("category") not in {value.value for value in GarmentCategory}
        }
    )
    report.record(
        "categories are contract values",
        not bad_categories,
        f"unknown: {bad_categories[:5]}" if bad_categories else "all from GarmentCategory",
    )

    bad_patterns = sorted(
        {
            str(entry.get("pattern"))
            for entry in entries
            if entry.get("pattern") not in {value.value for value in Pattern}
        }
    )
    report.record(
        "patterns are contract values",
        not bad_patterns,
        f"unknown: {bad_patterns[:5]}" if bad_patterns else "all from Pattern",
    )

    malformed_colors = [
        str(entry.get("id"))
        for entry in entries
        if not (
            isinstance(entry.get("colors"), list)
            and 1 <= len(entry["colors"]) <= 3
            and all(_non_empty_str(color) and color == color.lower() for color in entry["colors"])
        )
    ]
    report.record(
        "colors are 1–3 lowercase strings",
        not malformed_colors,
        f"offending: {malformed_colors[:5]}" if malformed_colors else "ok",
    )

    malformed_material = [
        str(entry.get("id"))
        for entry in entries
        if not (
            isinstance(entry.get("material"), list)
            and entry["material"]
            and all(_non_empty_str(fibre) and fibre == fibre.lower() for fibre in entry["material"])
        )
    ]
    report.record(
        "material is a non-empty lowercase list",
        not malformed_material,
        f"offending: {malformed_material[:5]}" if malformed_material else "ok",
    )

    bad_hard_attrs = [
        str(entry.get("id"))
        for entry in entries
        if not (
            isinstance(entry.get("hard_attributes"), list)
            and len(entry["hard_attributes"]) == 3
            and all(_non_empty_str(text) for text in entry["hard_attributes"])
        )
    ]
    report.record(
        "exactly 3 hard attributes each",
        not bad_hard_attrs,
        f"offending: {bad_hard_attrs[:5]}" if bad_hard_attrs else "ok",
    )

    # --- composition -------------------------------------------------------------------
    if strict:
        hard_entries = [entry for entry in entries if entry.get("hard_case") is True]
        report.record(
            f"exactly {HARD_CASES} hard cases",
            len(hard_entries) == HARD_CASES,
            f"{len(hard_entries)}/{HARD_CASES}",
        )
        if any("hard_case" not in entry for entry in entries):
            report.record(
                "hard_case is explicit on every entry", False, "boolean missing on some entries"
            )

        base = [entry for entry in entries if entry.get("hard_case") is not True]
        base_counts = {category: 0 for category in BASE_QUOTAS}
        for entry in base:
            category = entry.get("category")
            if category in base_counts:
                base_counts[category] += 1
        quota_failures = [
            f"{category} {count}/{quota}"
            for category, quota in BASE_QUOTAS.items()
            if (count := base_counts[category]) != quota
        ]
        report.record(
            "category quotas (35 non-hard entries)",
            not quota_failures,
            ", ".join(quota_failures) if quota_failures else ", ".join(
                f"{category} {count}" for category, count in base_counts.items()
            ),
        )
    else:
        # Reported, never gated: the reader has to know what the number was measured on.
        tally: dict[str, int] = {}
        for entry in entries:
            tally[str(entry.get("category"))] = tally.get(str(entry.get("category")), 0) + 1
        ordered = ", ".join(f"{name} {count}" for name, count in sorted(tally.items()))
        report.record(
            "composition (reported, not gated)",
            True,
            f"{len(entries)} entries: {ordered}",
        )

    # --- files on disk -----------------------------------------------------------------
    missing_photos: list[str] = []
    undecodable: list[str] = []
    sizes: dict[str, tuple[int, int]] = {}
    for entry in entries:
        rel = entry.get("path")
        if not _non_empty_str(rel):
            missing_photos.append(f"{entry.get('id', '?')}: no path")
            continue
        photo_path = golden_dir / rel
        if not photo_path.exists():
            missing_photos.append(str(rel))
            continue
        try:
            with Image.open(photo_path) as opened:
                opened.load()
                sizes[str(entry.get("id"))] = opened.size
        except Exception:  # noqa: BLE001 - any decode failure is the same failure here
            undecodable.append(str(rel))
    report.record(
        "every photo exists",
        not missing_photos,
        f"missing: {missing_photos[:5]}" if missing_photos else f"all {len(entries)} present",
    )
    report.record(
        "every photo decodes",
        not undecodable,
        f"undecodable: {undecodable[:5]}" if undecodable else "ok",
    )

    mask_entries = [entry for entry in entries if _non_empty_str(entry.get("mask_path"))]
    if strict:
        report.record(
            f"exactly {TOTAL_MASKS} mask files (presence only)",
            len(mask_entries) == TOTAL_MASKS,
            f"{len(mask_entries)}/{TOTAL_MASKS}",
        )
        sources = sorted(
            {str(entry.get("mask_source") or "unspecified") for entry in mask_entries}
        )
        hand = sum(
            1
            for entry in mask_entries
            if str(entry.get("mask_source") or "").strip().lower().startswith("hand")
        )
        report.record(
            "mask provenance (reported, not gated)",
            True,
            f"{len(mask_entries)} masks: {', '.join(sources) or 'none'}; "
            f"hand-drawn by mask_source convention: {hand}/{len(mask_entries)}. "
            "This tool does not gate provenance; E0-T1's hand-drawn requirement is "
            "UNVERIFIED here.",
        )
    else:
        # In this profile every entry is meant to be measurable, so a missing mask is a
        # failure — but the masks are not claimed to be hand-drawn, and the report says so.
        report.record(
            "every entry carries a mask",
            len(mask_entries) == len(entries),
            f"{len(mask_entries)}/{len(entries)}",
        )
    mask_problems: list[str] = []
    for entry in mask_entries:
        mask_path = golden_dir / str(entry["mask_path"])
        if not mask_path.exists():
            mask_problems.append(f"{entry['mask_path']}: missing")
            continue
        try:
            with Image.open(mask_path) as opened:
                opened.load()
                if opened.mode not in ("L", "1"):
                    mask_problems.append(f"{entry['mask_path']}: mode {opened.mode}, wants L")
                photo_size = sizes.get(str(entry.get("id")))
                if photo_size and opened.size != photo_size:
                    mask_problems.append(
                        f"{entry['mask_path']}: {opened.size} vs photo {photo_size}"
                    )
        except Exception:  # noqa: BLE001
            mask_problems.append(f"{entry['mask_path']}: undecodable")
    report.record(
        "masks are single-channel and sized like their photo",
        not mask_problems,
        "; ".join(mask_problems[:5]) if mask_problems else "ok",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.tools.validate_golden",
        description="Check the E0-T1 golden set against the rules in evals/golden/README.md",
    )
    parser.add_argument("--golden-dir", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--profile",
        choices=PROFILES,
        default="golden",
        help="golden = the E0-T1 contract; provisional = structure only, composition not gated",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="only print the summary line and failures"
    )
    args = parser.parse_args(argv)

    golden_dir: Path = args.golden_dir
    if not golden_dir.is_dir():
        print(f"FAIL: {golden_dir} is not a directory", file=sys.stderr)
        return 2

    report = validate(golden_dir, profile=args.profile)
    if args.quiet:
        failures = [check for check in report.checks if not check.ok]
        state = "COMPLETE" if report.passed else f"INCOMPLETE ({len(failures)} problems)"
        print(f"golden set: {state}")
        for check in failures:
            print(f"  - {check.name}: {check.detail}")
    else:
        print(report.render(golden_dir))

    if report.fatal:
        return 2
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
