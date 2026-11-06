"""Fail-closed semantic gate between docs/CONTRACTS.md and the real constants.

Triangle of responsibilities (why this gate exists at all):

* ``make lint`` / ``make test`` enforce "the two CODE sides agree with each other
  and behave correctly at runtime" (ruff, tsc, contract tests, parity test).
* This gate enforces the MISSING edge: "the frozen DOCUMENT agrees with the
  constants those two code sides actually define". A version bump in code with a
  stale headline, or an enum reordered in code with a stale fenced block, is
  invisible to the test suite.

The document path is FIXED (repo-relative, resolved from ``__file__``) and there
is deliberately no CLI flag to override it: a gate that can be pointed at a fake
file is not a gate. Values are parsed from ``apps/api/schemas.py`` with ``ast``
and from the two TypeScript modules with restricted regular expressions. No
business constant, version, event name or enum VALUE is hardcoded here: the
identifiers used to locate definitions are names, not values. The one literal
table is WORD_TO_NUM, which is language-level number-word vocabulary.

Anchors A1..A8 are bidirectional: each compares a value extracted from the
document with a value parsed from code, so a change on EITHER side alone turns
the gate red. STRUCT guards against deleting a whole section to bypass them.

NOTE: we deliberately never assert that the document's ``v1.0.0`` status equals
the code ``CONTRACT_VERSION`` (1.1.0). CONTRACTS.md §9 says that value "does not
move"; the two are different quantities. Equality of the two code-side
CONTRACT_VERSION constants is already covered by test_contract_parity.py.
"""

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_PATH = ROOT / "docs" / "CONTRACTS.md"
PY_SCHEMAS_PATH = ROOT / "apps" / "api" / "schemas.py"
TS_SCHEMAS_PATH = ROOT / "packages" / "shared" / "src" / "schemas.ts"
TS_ANALYTICS_PATH = ROOT / "packages" / "shared" / "src" / "analytics.ts"

# Language-level number words. The ONLY literal lookup table in this module.
WORD_TO_NUM: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

_HEADING_RE = re.compile(r"^## ([0-9]+)\.", re.MULTILINE)
_VERSION_EQ_RE = re.compile(r"EVENTS_VERSION\s*=\s*([0-9]+(?:\.[0-9]+)+)")
_BACKTICKED_CODE_RE = re.compile(r"`([a-z][a-z0-9_]*)`")
_PROSE_COUNT_RE = re.compile(r"\*\*([A-Za-z]+) events\.\*\*")
_EVENT_ROW_RE = re.compile(r"^([0-9]+)$")
_ENUM_LINE_RE = re.compile(r"\s*([A-Za-z][A-Za-z0-9]*)\s+(.+?)\s*")
_SEPARATOR_RE = re.compile(r"^[|\-:\s]+$")
_TS_ARRAY_RE = re.compile(
    r"export\s+const\s+([A-Z][A-Z0-9_]*)\s*=\s*\[(.*?)\]\s*as\s+const",
    re.DOTALL,
)
_TS_STRING_RE = re.compile(
    r"export\s+const\s+([A-Z][A-Z0-9_]*)\s*=\s*\"([^\"]+)\"\s*;"
)
_TS_QUOTED_RE = re.compile(r"\"([^\"]+)\"")


class GateError(Exception):
    """A single anchor failed; ``anchor`` is the id printed in the FAIL line."""

    def __init__(self, anchor: str, reason: str) -> None:
        super().__init__(f"{anchor} {reason}")
        self.anchor = anchor
        self.reason = reason


@dataclass(frozen=True)
class Facts:
    """Everything parsed from the frozen document and the real code sides."""

    doc: str
    sections: dict[int, str]
    py_enums: dict[str, list[str]]
    ts_schema_arrays: dict[str, list[str]]
    ts_analytics_arrays: dict[str, list[str]]
    ts_analytics_strings: dict[str, str]


def _read(path: Path, anchor: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError(anchor, f"cannot read {path.name}: {exc}") from exc


def _sections(doc: str) -> dict[int, str]:
    """Split CONTRACTS.md on top-level `## N.` headings; fail if none exist."""
    matches = list(_HEADING_RE.finditer(doc))
    if not matches:
        raise GateError("STRUCT", "no '## N.' section headings found")
    out: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(doc)
        out[int(match.group(1))] = doc[match.start():end]
    return out


def _first_table(section: str, anchor: str) -> list[list[str]]:
    """Return the DATA rows of the first Markdown table in ``section``.

    Cells are split on unescaped pipes because CONTRACTS.md §7 contains
    ``camera\\|library`` inside a table cell. The header row is excluded, which
    matters for §6 where the header itself is a backticked ``reason_code``.
    """
    lines = section.splitlines()
    for index in range(len(lines) - 1):
        if not lines[index].strip().startswith("|"):
            continue
        if not _SEPARATOR_RE.fullmatch(lines[index + 1].strip()):
            continue
        rows: list[list[str]] = []
        for raw in lines[index + 2:]:
            stripped = raw.strip()
            if not stripped.startswith("|"):
                break
            cells = re.split(r"(?<!\\)\|", stripped)[1:-1]
            rows.append([cell.strip() for cell in cells])
        if rows:
            return rows
        break
    raise GateError(anchor, "no Markdown table found in section")


def _code_cells(rows: list[list[str]], anchor: str) -> list[str]:
    """Collect first-column backticked ``code`` cells, in row order."""
    codes: list[str] = []
    for cells in rows:
        if not cells:
            continue
        match = _BACKTICKED_CODE_RE.fullmatch(cells[0].strip())
        if match is not None:
            codes.append(match.group(1))
    if not codes:
        raise GateError(anchor, "no backticked code cells in table")
    return codes


def _enumerations_block(section: str, anchor: str) -> list[tuple[str, list[str]]]:
    """Parse the §1 '### Enumerations' fenced block, preserving line order."""
    lines = section.splitlines()
    heading = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("### Enumerations")),
        None,
    )
    if heading is None:
        raise GateError(anchor, "'### Enumerations' sub-heading missing in §1")
    opening = next(
        (i for i in range(heading + 1, len(lines)) if lines[i].strip().startswith("```")),
        None,
    )
    if opening is None:
        raise GateError(anchor, "Enumerations fenced block missing in §1")
    entries: list[tuple[str, list[str]]] = []
    for raw in lines[opening + 1:]:
        if raw.strip().startswith("```"):
            break
        match = _ENUM_LINE_RE.fullmatch(raw)
        if match is None:
            raise GateError(anchor, f"unparseable enumeration line: {raw!r}")
        values = [value.strip() for value in match.group(2).split("|")]
        if not all(values):
            raise GateError(anchor, f"empty enumeration value: {raw!r}")
        entries.append((match.group(1), values))
    if not entries:
        raise GateError(anchor, "Enumerations fenced block is empty")
    return entries


def _py_string_enums(source: str, anchor: str) -> dict[str, list[str]]:
    """Extract StrEnum member values from schemas.py, preserving source order."""
    try:
        tree = ast.parse(source, filename="schemas.py")
    except SyntaxError as exc:
        raise GateError(anchor, f"cannot parse schemas.py: {exc}") from exc
    enums: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        is_str_enum = any(
            (isinstance(base, ast.Name) and base.id == "StrEnum")
            or (isinstance(base, ast.Attribute) and base.attr == "StrEnum")
            for base in node.bases
        )
        if not is_str_enum:
            continue
        values: list[str] = []
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                continue
            target = stmt.targets[0]
            if isinstance(target, ast.Name) and isinstance(stmt.value, ast.Constant):
                if isinstance(stmt.value.value, str):
                    values.append(stmt.value.value)
        enums[node.name] = values
    if not enums:
        raise GateError(anchor, "no StrEnum classes found in schemas.py")
    return enums


def _ts_arrays(source: str, anchor: str) -> dict[str, list[str]]:
    """Parse `export const NAME = [ ... ] as const;` blocks from a TS module."""
    arrays: dict[str, list[str]] = {}
    for match in _TS_ARRAY_RE.finditer(source):
        arrays[match.group(1)] = _TS_QUOTED_RE.findall(match.group(2))
    if not arrays:
        raise GateError(anchor, "no `= [ ... ] as const` arrays found in TypeScript source")
    return arrays


def _ts_strings(source: str, anchor: str) -> dict[str, str]:
    """Parse `export const NAME = "value";` assignments from a TS module."""
    strings = {m.group(1): m.group(2) for m in _TS_STRING_RE.finditer(source)}
    if not strings:
        raise GateError(anchor, "no `= \"value\";` string constants found")
    return strings


def _require(mapping: dict, key: str, anchor: str, what: str):
    if key not in mapping:
        raise GateError(anchor, f"{what} {key!r} not found")
    return mapping[key]


def _expected_ts_const(name: str) -> str:
    """Derive a schemas.ts array name from a StrEnum class name.

    ``GarmentCategory`` -> ``GARMENT_CATEGORIES``, ``MeasurementSource`` ->
    ``MEASUREMENT_SOURCES``. This is a generic CamelCase + plural rule, not a
    table of business values.
    """
    tokens = re.findall(r"[A-Z][a-z0-9]*", name)
    if not tokens:
        raise GateError("A6", f"cannot derive a TS const name from {name!r}")
    upper = [token.upper() for token in tokens]
    last = upper[-1]
    if last.endswith("Y") and len(last) > 1 and last[-2] not in "AEIOU":
        upper[-1] = last[:-1] + "IES"
    elif last.endswith("S"):
        upper[-1] = last + "ES"
    else:
        upper[-1] = last + "S"
    return "_".join(upper)


def check_a1(facts: Facts) -> None:
    """§7 title/Current version <-> analytics.ts EVENTS_VERSION."""
    version = _require(
        facts.ts_analytics_strings, "EVENTS_VERSION", "A1", "analytics.ts const"
    )
    found = _VERSION_EQ_RE.findall(facts.sections.get(7, ""))
    if len(found) < 2:
        raise GateError(
            "A1", f"expected >= 2 'EVENTS_VERSION = x.y.z' in §7, found {len(found)}"
        )
    if any(item != version for item in found):
        raise GateError(
            "A1", f"§7 version(s) {found} != EVENTS_VERSION {version!r} in analytics.ts"
        )


def check_a2(facts: Facts) -> None:
    """§7 numbered event column (ordered) <-> analytics.ts ANALYTICS_EVENTS."""
    events = _require(
        facts.ts_analytics_arrays, "ANALYTICS_EVENTS", "A2", "analytics.ts array"
    )
    if not events:
        raise GateError("A2", "ANALYTICS_EVENTS is empty")
    pairs: list[tuple[int, str]] = []
    for cells in _first_table(facts.sections.get(7, ""), "A2"):
        if len(cells) < 2 or _EVENT_ROW_RE.fullmatch(cells[0].strip()) is None:
            continue
        event = _BACKTICKED_CODE_RE.fullmatch(cells[1].strip())
        if event is None:
            continue
        pairs.append((int(cells[0].strip()), event.group(1)))
    numbers = [number for number, _ in pairs]
    expected = list(range(1, len(events) + 1))
    if numbers != expected:
        raise GateError("A2", f"§7 table numbering {numbers} != {expected}")
    doc_events = [event for _, event in pairs]
    if doc_events != events:
        raise GateError("A2", f"§7 event column {doc_events} != ANALYTICS_EVENTS {events}")


def check_a3(facts: Facts) -> None:
    """§7 prose '**Seven events.**' <-> len(ANALYTICS_EVENTS)."""
    events = _require(
        facts.ts_analytics_arrays, "ANALYTICS_EVENTS", "A3", "analytics.ts array"
    )
    words = _PROSE_COUNT_RE.findall(facts.sections.get(7, ""))
    if len(words) != 1:
        raise GateError(
            "A3", f"expected exactly one '**<Word> events.**' in §7, found {words}"
        )
    word = words[0].lower()
    if word not in WORD_TO_NUM:
        raise GateError("A3", f"number word {words[0]!r} is not in WORD_TO_NUM")
    said = WORD_TO_NUM[word]
    if said != len(events):
        raise GateError(
            "A3",
            f"'**{words[0]} events.**' says {said} but ANALYTICS_EVENTS has {len(events)}",
        )


def check_a4(facts: Facts) -> None:
    """§9 last 'events x.y.z ... ADR-00N' history row <-> EVENTS_VERSION."""
    version = _require(
        facts.ts_analytics_strings, "EVENTS_VERSION", "A4", "analytics.ts const"
    )
    hits: list[str] = []
    for cells in _first_table(facts.sections.get(9, ""), "A4"):
        first = cells[0].strip() if cells else ""
        match = re.fullmatch(r"events\s+([0-9]+(?:\.[0-9]+)+)", first)
        if match is None:
            continue
        joined = " | ".join(cells)
        if re.search(r"ADR-[0-9]+", joined) is None:
            raise GateError(
                "A4", f"§9 events row for {match.group(1)!r} has no ADR reference"
            )
        hits.append(match.group(1))
    if not hits:
        raise GateError("A4", "no 'events x.y.z' rows in §9 version history")
    if hits[-1] != version:
        raise GateError(
            "A4", f"last §9 events version {hits[-1]!r} != EVENTS_VERSION {version!r}"
        )


def check_a5(facts: Facts) -> None:
    """Header '**Status: v1.0.0 — frozen.**' freeze sentinel."""
    status = [line for line in facts.doc.splitlines() if line.startswith("**Status:")]
    if len(status) != 1:
        raise GateError(
            "A5", f"expected exactly one '**Status:' header, found {len(status)}"
        )
    if re.search(r"\bfrozen\b", status[0]) is None:
        raise GateError("A5", "'frozen' sentinel missing from CONTRACTS.md Status header")


def check_a6(facts: Facts) -> None:
    """§1 Enumerations fenced block <-> schemas.py StrEnums + schemas.ts arrays."""
    entries = _enumerations_block(facts.sections.get(1, ""), "A6")
    # Six is the structural line count of the frozen fenced block, not a
    # business value: pinning it is what makes deleting a whole line turn red.
    if len(entries) != 6:
        raise GateError(
            "A6", f"§1 Enumerations block must have 6 lines, found {len(entries)}"
        )
    names = list(facts.py_enums)
    positions: list[int] = []
    for name, values in entries:
        if name not in facts.py_enums:
            raise GateError("A6", f"§1 enumeration {name!r} has no StrEnum in schemas.py")
        if values != facts.py_enums[name]:
            raise GateError(
                "A6", f"§1 {name} {values} != schemas.py {facts.py_enums[name]}"
            )
        positions.append(names.index(name))
        matches = [
            key for key, value in facts.ts_schema_arrays.items() if value == values
        ]
        if len(matches) != 1:
            raise GateError(
                "A6",
                f"§1 {name}: expected exactly 1 matching schemas.ts array, got {matches}",
            )
        expected = _expected_ts_const(name)
        if matches[0] != expected:
            raise GateError(
                "A6", f"§1 {name}: matched {matches[0]!r}, expected {expected!r}"
            )
    if positions != sorted(positions):
        raise GateError("A6", f"§1 enumeration order {positions} != schemas.py order")


def check_a7(facts: Facts) -> None:
    """§4 reason_code table <-> ReasonCode StrEnum + REASON_CODES array."""
    py_codes = _require(facts.py_enums, "ReasonCode", "A7", "schemas.py StrEnum")
    ts_codes = _require(
        facts.ts_schema_arrays, "REASON_CODES", "A7", "schemas.ts array"
    )
    if not py_codes or py_codes != ts_codes:
        raise GateError("A7", f"ReasonCode {py_codes} != REASON_CODES {ts_codes}")
    doc_codes = _code_cells(_first_table(facts.sections.get(4, ""), "A7"), "A7")
    if doc_codes != py_codes:
        raise GateError("A7", f"§4 reason_code table {doc_codes} != code {py_codes}")


def check_a8(facts: Facts) -> None:
    """§6 failure-copy reason_codes <-> §4 table <-> both code sides."""
    py_codes = _require(facts.py_enums, "ReasonCode", "A8", "schemas.py StrEnum")
    ts_codes = _require(
        facts.ts_schema_arrays, "REASON_CODES", "A8", "schemas.ts array"
    )
    section4 = _code_cells(_first_table(facts.sections.get(4, ""), "A8"), "A8")
    section6 = _code_cells(_first_table(facts.sections.get(6, ""), "A8"), "A8")
    if section4 != section6 or section4 != py_codes or section4 != ts_codes:
        raise GateError(
            "A8",
            f"§4 {section4} / §6 {section6} / ReasonCode {py_codes} / "
            f"REASON_CODES {ts_codes} disagree",
        )


def check_struct(facts: Facts) -> None:
    """Nine `## 1.` .. `## 9.` headings + the §9 'does not move' sentinel."""
    expected = list(range(1, 10))
    if sorted(facts.sections) != expected:
        raise GateError(
            "STRUCT", f"section headings {sorted(facts.sections)} != {expected}"
        )
    section9 = facts.sections.get(9, "")
    if re.search(r"CONTRACT_VERSION`?\s+does not move", section9) is None:
        raise GateError(
            "STRUCT", "§9 'CONTRACT_VERSION does not move' sentinel missing"
        )


CHECKS = (
    check_a1,
    check_a2,
    check_a3,
    check_a4,
    check_a5,
    check_a6,
    check_a7,
    check_a8,
    check_struct,
)


def build_facts() -> Facts:
    """Read and parse all four fixed inputs; any failure is fail-closed."""
    doc = _read(CONTRACTS_PATH, "IO")
    py_source = _read(PY_SCHEMAS_PATH, "IO")
    ts_schema_source = _read(TS_SCHEMAS_PATH, "IO")
    ts_analytics_source = _read(TS_ANALYTICS_PATH, "IO")
    return Facts(
        doc=doc,
        sections=_sections(doc),
        py_enums=_py_string_enums(py_source, "A6"),
        ts_schema_arrays=_ts_arrays(ts_schema_source, "A7"),
        ts_analytics_arrays=_ts_arrays(ts_analytics_source, "A1"),
        ts_analytics_strings=_ts_strings(ts_analytics_source, "A1"),
    )


def main() -> int:
    """Run every anchor; print a real count on success, exit 0."""
    if len(sys.argv) != 1:
        raise GateError("IO", "no CLI arguments accepted; the document path is fixed")
    facts = build_facts()
    for check in CHECKS:
        check(facts)
    print(f"CONTRACT_GATE: OK ({len(CHECKS)} anchors checked)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GateError as err:
        print(f"CONTRACT_GATE: FAIL {err.anchor} {err.reason}")
        sys.exit(1)
    except Exception as err:
        print(f"CONTRACT_GATE: FAIL UNEXPECTED {type(err).__name__}: {err}")
        sys.exit(1)
