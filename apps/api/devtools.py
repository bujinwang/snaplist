"""Developer tooling for a database that no worker is writing to yet.

Why this file exists
--------------------
In production, `assets` rows are written by the job worker (E5-T3) after `packages/vision`
produces a PNG. That worker does not exist in this build, so **no item can ever have a photo** —
which means `POST /v1/exports` correctly refuses every row (`missing_fields=prettified_url`) and
there is no way to exercise the CSV path against a running server by hand.

Rather than fake an asset inside the export endpoint (a lie that would ship), this module exposes
the same `repository.set_asset` call the worker will make, as a CLI:

    SNAPLIST_DB=/tmp/snaplist.db python -m apps.api.devtools attach-asset \\
        --item <uuid> --user <uuid> --path https://example.test/p.png

It refuses to run outside a local/test environment, because attaching an asset without running the
pipeline is exactly the kind of shortcut that must never be reachable in production. Nothing in the
API imports this module; it exists for humans running a vertical slice by hand, and QA replicating
one.
"""

from __future__ import annotations

import argparse
import sys
from uuid import UUID

from apps.api.auth import LOCAL_ENVS
from apps.api.repository import build_repository
from apps.api.settings import get_settings

ASSET_KINDS = ("source", "mask", "cutout", "prettified", "on_model")


def _guard() -> None:
    settings = get_settings()
    if settings.env.lower() not in LOCAL_ENVS:
        raise SystemExit(
            f"refusing to run: ENV={settings.env!r} is not local/test/development. "
            "Attaching assets without running the pipeline is a local-development shortcut."
        )


def attach_asset(args: argparse.Namespace) -> int:
    repository = build_repository(args.db)
    item = repository.get_item(UUID(args.item), user_id=UUID(args.user), include_deleted=True)
    if item is None:
        print(f"no item {args.item} owned by {args.user}", file=sys.stderr)
        return 2
    repository.set_asset(
        UUID(args.item),
        user_id=UUID(args.user),
        kind=args.kind,
        storage_path=args.path,
        position=args.position,
    )
    record = repository.get_item(UUID(args.item), user_id=UUID(args.user))
    paths = {asset.kind: asset.storage_path for asset in (record.assets if record else ())}
    print(f"attached {args.kind} -> {args.path}")
    print(f"item {args.item} assets: {paths}")
    return 0


def main(argv: list[str] | None = None) -> int:
    _guard()
    parser = argparse.ArgumentParser(prog="python -m apps.api.devtools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    attach = subparsers.add_parser("attach-asset", help="write an assets row (worker stand-in)")
    attach.add_argument("--db", default=None, help="sqlite path; defaults to SNAPLIST_DB")
    attach.add_argument("--item", required=True, help="item uuid")
    attach.add_argument("--user", required=True, help="owner uuid")
    attach.add_argument("--kind", required=True, choices=ASSET_KINDS)
    attach.add_argument("--path", required=True, help="storage path or URL")
    attach.add_argument("--position", type=int, default=0)
    attach.set_defaults(handler=attach_asset)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
