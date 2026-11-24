"""Asset lifecycle cleanup — the boring, auditable kind.

Cost pressure comes from bytes that stay in the bucket after nobody can use them. This module
removes exactly those bytes:

* the item must be **soft-deleted** (privacy policy: assets live until the seller deletes the
  item), **and**
* a `export_delivered` event for that item must be older than the retention window (the seller
  already has the file), **and**
* both timestamps must precede the cutoff.

A live listing never loses its images to this job, and a deleted item whose export never landed
is left alone. Unmeasured `byte_size` is reported as unmeasured — reclaimed bytes are summed
only from rows that actually carried a number.

Dry-run is the default. The CLI prints an audit table (or JSON) and only deletes on `--execute`.
Re-running is idempotent: deleted rows no longer match the query, and an already-missing object
is a successful no-op that still cleans up its row.

    .venv/bin/python -m apps.api.retention --db /path/snaplist.db
    .venv/bin/python -m apps.api.retention --db /path/snaplist.db --execute --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from apps.api.repository import Repository, build_repository, utcnow
from apps.api.settings import get_settings
from apps.api.storage import Storage, StorageError, build_storage

DEFAULT_RETENTION_DAYS = 30


@dataclass(slots=True)
class RetentionAction:
    """What happened to one candidate. Every field describes an observed outcome."""

    item_id: str
    user_id: str
    kind: str
    position: int
    storage_path: str
    byte_size: int | None
    object_deleted: bool
    object_missing: bool
    row_deleted: bool
    error: str | None = None


@dataclass(slots=True)
class RetentionReport:
    """The audit artifact. Nothing in here is inferred from a price or a guess."""

    dry_run: bool
    retention_days: int
    cutoff: datetime
    actions: list[RetentionAction] = field(default_factory=list)

    @property
    def candidates(self) -> int:
        return len(self.actions)

    @property
    def rows_deleted(self) -> int:
        return sum(1 for action in self.actions if action.row_deleted)

    @property
    def objects_deleted(self) -> int:
        return sum(1 for action in self.actions if action.object_deleted)

    @property
    def objects_missing(self) -> int:
        return sum(1 for action in self.actions if action.object_missing)

    @property
    def failures(self) -> int:
        return sum(1 for action in self.actions if action.error is not None)

    @property
    def bytes_reclaimed(self) -> int | None:
        """Measured bytes freed, or UNMEASURED (None) when no row had a byte_size."""
        values = [
            action.byte_size
            for action in self.actions
            if action.row_deleted and action.byte_size is not None
        ]
        return sum(values) if values else None

    @property
    def unmeasured_byte_rows(self) -> int:
        return sum(
            1
            for action in self.actions
            if action.row_deleted and action.byte_size is None
        )

    def to_json(self) -> str:
        payload = {
            "dry_run": self.dry_run,
            "retention_days": self.retention_days,
            "cutoff": self.cutoff.isoformat(),
            "candidates": self.candidates,
            "rows_deleted": self.rows_deleted,
            "objects_deleted": self.objects_deleted,
            "objects_missing": self.objects_missing,
            "failures": self.failures,
            "bytes_reclaimed": self.bytes_reclaimed,  # null == UNMEASURED
            "unmeasured_byte_rows": self.unmeasured_byte_rows,
            "actions": [asdict(action) for action in self.actions],
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def render(self) -> str:
        mode = "DRY-RUN (nothing was deleted)" if self.dry_run else "EXECUTED"
        reclaimed = "UNMEASURED" if self.bytes_reclaimed is None else str(self.bytes_reclaimed)
        lines = [
            f"SnapList asset retention — {mode}",
            f"retention_days={self.retention_days} cutoff={self.cutoff.isoformat()}",
            f"candidates={self.candidates} rows_deleted={self.rows_deleted} "
            f"objects_deleted={self.objects_deleted} objects_missing={self.objects_missing} "
            f"failures={self.failures}",
            f"bytes_reclaimed={reclaimed} unmeasured_byte_rows={self.unmeasured_byte_rows}",
            "",
        ]
        if self.actions:
            lines += [
                "| item | kind | position | bytes | object | row | error |",
                "|---|---|---|---|---|---|---|",
            ]
            for action in self.actions:
                state = (
                    "would-delete"
                    if self.dry_run
                    else ("deleted" if action.object_deleted else
                          "missing" if action.object_missing else "kept")
                )
                row = "would-delete" if self.dry_run else (
                    "deleted" if action.row_deleted else "kept"
                )
                lines.append(
                    f"| {action.item_id} | {action.kind} | {action.position} | "
                    f"{'UNMEASURED' if action.byte_size is None else action.byte_size} | "
                    f"{state} | {row} | {action.error or '—'} |"
                )
        else:
            lines.append("No soft-deleted, exported item is older than the cutoff.")
        return "\n".join(lines)


def sweep(
    repository: Repository,
    storage: Storage,
    *,
    now: datetime | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    dry_run: bool = True,
    limit: int | None = None,
) -> RetentionReport:
    """Clean up eligible assets. `storage.delete` failures keep the row for a later retry."""
    if retention_days < 0:
        raise ValueError("retention_days must be >= 0")
    moment = now or utcnow()
    if moment.tzinfo is None:
        raise ValueError("now must be timezone-aware UTC")
    cutoff = moment - timedelta(days=retention_days)
    candidates = repository.assets_for_retention(cutoff=cutoff, limit=limit)
    report = RetentionReport(dry_run=dry_run, retention_days=retention_days, cutoff=cutoff)

    for candidate in candidates:
        action = RetentionAction(
            item_id=str(candidate.item_id),
            user_id=str(candidate.user_id),
            kind=candidate.kind,
            position=candidate.position,
            storage_path=candidate.storage_path,
            byte_size=candidate.byte_size,
            object_deleted=False,
            object_missing=False,
            row_deleted=False,
        )
        if dry_run:
            report.actions.append(action)
            continue
        try:
            deleted = storage.delete(candidate.storage_path)
        except StorageError as exc:
            action.error = f"object delete failed: {type(exc).__name__}: {exc}"
            report.actions.append(action)
            continue
        action.object_deleted = deleted
        action.object_missing = not deleted
        # Delete the object first. If the row delete fails, the next run still sees the row and
        # treats the missing object as a successful no-op — never an orphan row with no bytes.
        try:
            action.row_deleted = repository.delete_asset(
                candidate.item_id,
                user_id=candidate.user_id,
                kind=candidate.kind,
                position=candidate.position,
            )
        except Exception as exc:  # noqa: BLE001 - a DB refusal must be audited and retried
            action.error = f"asset row delete failed: {type(exc).__name__}: {exc}"
        if not action.row_deleted and action.error is None:
            action.error = "asset row was concurrently removed or never matched"
        report.actions.append(action)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m apps.api.retention",
        description="Delete assets of soft-deleted items whose export was delivered long ago.",
    )
    parser.add_argument("--db", default=None, help="database (default: SNAPLIST_DB or :memory:)")
    parser.add_argument(
        "--retention-days", type=int, default=DEFAULT_RETENTION_DAYS,
        help=f"days since deletion and delivery (default: {DEFAULT_RETENTION_DAYS})",
    )
    parser.add_argument("--limit", type=int, default=None, help="max rows to consider")
    parser.add_argument("--execute", action="store_true", help="actually delete (default: dry run)")
    parser.add_argument("--json", action="store_true", help="emit the audit as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = build_repository(args.db)
    storage = build_storage(get_settings())
    report = sweep(
        repository,
        storage,
        retention_days=args.retention_days,
        dry_run=not args.execute,
        limit=args.limit,
    )
    print(report.to_json() if args.json else report.render())
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
