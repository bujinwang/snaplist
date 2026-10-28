"""Developer CLI for the vision pipeline. Owner: CV role (E1..E3).

Wiring only — every stage it calls is still a stub, so today it exits non-zero with a
clear message instead of pretending to work. Acceptance command from prompts/02-vision.md
must keep working verbatim once the stages land:

    python -m packages.vision.cli --in evals/fixtures/tee.jpg --out /tmp/out

It writes prettified.png, cutout.png and attributes.json into --out.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from packages.vision import StageNotImplemented


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m packages.vision.cli")
    p.add_argument("--in", dest="src", required=True, help="input photo")
    p.add_argument("--out", dest="dst", required=True, help="output directory")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    try:
        raise StageNotImplemented(
            "vision pipeline is not implemented yet (E1 segment / E2 prettify / E3 attrs). "
            "See docs/PLAN.md."
        )
    except StageNotImplemented as exc:
        print(f"NOT IMPLEMENTED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
