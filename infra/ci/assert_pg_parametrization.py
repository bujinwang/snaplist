"""Fail the run when the Postgres parametrization did not actually execute.

Why this exists
---------------
`apps/api/tests/conftest.py` parametrizes the `repository` fixture over `["sqlite"]` and, only
when `SNAPLIST_TEST_PG` is set, `"postgres"`. That is the right design for a laptop (offline, fast,
no database), and it is a trap in CI: if the service is down, the URL is wrong, the migrations did
not apply, or somebody unsets the variable, **pytest simply runs the sqlite half and exits 0**. The
job is green and the Postgres code has still never been executed — a coverage claim that is false
while every visible signal says otherwise.

So this reads the JUnit XML and refuses to let that pass: it counts test cases by their
parametrization suffix and exits non-zero when either half is missing.

It checks the sqlite half too, for the mirror-image failure: a change to the parametrization that
silently drops `sqlite` would otherwise look like success from the Postgres side.

    .venv/bin/python infra/ci/assert_pg_parametrization.py <junit.xml>
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

#: pytest renders the parametrization id in the test name: `test_x[postgres]`, `test_x[sqlite]`.
SQLITE = "[sqlite]"
POSTGRES = "[postgres]"


def _counts(junit: Path) -> tuple[int, int, int]:
    """`(sqlite_cases, postgres_cases, total_cases)` from a pytest JUnit report."""
    tree = ET.parse(junit)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    sqlite = postgres = total = 0
    for suite in suites:
        for case in suite.iter("testcase"):
            total += 1
            # The suffix lives in `name`; `classname` carries the module path.
            name = case.get("name", "")
            if name.endswith(SQLITE):
                sqlite += 1
            elif name.endswith(POSTGRES):
                postgres += 1
    return sqlite, postgres, total


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: assert_pg_parametrization.py <junit.xml>", file=sys.stderr)
        return 2
    junit = Path(args[0])
    if not junit.is_file():
        print(f"no JUnit report at {junit}: did pytest run with --junitxml?", file=sys.stderr)
        return 2

    sqlite, postgres, total = _counts(junit)
    print(f"parametrization: total={total} sqlite={sqlite} postgres={postgres}")

    problems: list[str] = []
    if postgres == 0:
        problems.append(
            "the Postgres parametrization did not run: no test in the report ends with "
            f"'{POSTGRES}'. The service, SNAPLIST_TEST_PG, or the migration chain is wrong — this "
            "run proves nothing about the Postgres backend."
        )
    if sqlite == 0:
        problems.append(
            f"no test in the report ends with '{SQLITE}': the two-backend parametrization in "
            "apps/api/tests/conftest.py is broken, so neither half is meaningful."
        )
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
