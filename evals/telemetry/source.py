"""The only module that opens a database. It opens one READ-ONLY.

    sqlite3.connect(f"file:{path}?mode=ro", uri=True)

It must NEVER construct `apps.api.repository.SqliteRepository`: that constructor runs
`executescript(_SCHEMA)`, `alter table` migrations and can even raise RuntimeError. A report that
writes DDL would turn "source absent (3a)" into "table exists but is empty", which is exactly the
honesty violation this ticket exists to prevent (architect's E0-T5 design, §2). The main sample
is grouped on `events.item_id` and is NEVER INNER JOINed to `items`: hard-deleted items become
dangling uuids by ADR-008 design and must stay in the sample.

Refund attribution uses the `usage_events` ledger self-join, because `job_refunded.item_id` is
always NULL in production (`apps/api/usage.py::refund_one` never passes it). Reading that column
directly returns zero refunded items while printing a confident `excluded_refunded=0`.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Protocol

from evals.telemetry.compute import build_attribution, is_uuid_shaped
from evals.telemetry.model import Attribution, AttributionStatus, EventRow, SourceData
from evals.telemetry.spec import FAILURE_MIX_CODES

# Explicit column list. `field_names` is JSON text and no gate uses it: not read, not parsed.
# `item_id` is TEXT (36 characters), never a UUID object, never coerced.
_EVENT_COLUMNS = (
    "event_id",
    "event",
    "received_at",
    "client_ts",
    "session_id",
    "subject",
    "app_version",
    "platform",
    "item_id",
    "decision",
    "prettify_latency_ms",
    "attrs_latency_ms",
    "fields_touched",
    "export_latency_ms",
    "delivery_latency_ms",
    "payload_sha256",
)

# The normative ledger self-join (addendum 1, A), expressed as a LEFT JOIN so that a refund row
# with no recoverable item still appears and can be counted as evidence absence:
#
#   select distinct e.item_id
#   from usage_events r
#   join usage_events e on e.job_id = r.job_id and e.event = 'item_enqueued'
#   where r.event = 'job_refunded' and e.item_id is not null
#
# `usage_events_one_spend_per_job` (a partial UNIQUE index on job_id) guarantees there is at
# most one `item_enqueued` row per job_id, so the LEFT JOIN yields exactly one row per refund.
_LEDGER_SELF_JOIN = (
    "select r.job_id as refund_job_id, e.item_id as enqueued_item_id "
    "from usage_events r "
    "left join usage_events e "
    "  on e.event = 'item_enqueued' and e.job_id = r.job_id "
    "where r.event = 'job_refunded'"
)


class EventSource(Protocol):
    """Adapter boundary. Implementations return a fully materialized, immutable SourceData."""

    def load(self) -> SourceData:
        """Read the source. Never write, never create a table, never coerce an id."""
        raise NotImplementedError("EventSource.load must be provided by an adapter")


def _row_to_event(row: sqlite3.Row) -> EventRow:
    """Convert a raw sqlite3.Row into an EventRow without coercion."""
    return EventRow(
        event_id=str(row["event_id"]),
        event=str(row["event"]),
        received_at=str(row["received_at"]),
        client_ts=str(row["client_ts"]),
        session_id=str(row["session_id"]),
        subject=str(row["subject"]),
        app_version=str(row["app_version"]),
        platform=str(row["platform"]),
        item_id=row["item_id"],
        decision=row["decision"],
        prettify_latency_ms=row["prettify_latency_ms"],
        attrs_latency_ms=row["attrs_latency_ms"],
        fields_touched=row["fields_touched"],
        export_latency_ms=row["export_latency_ms"],
        delivery_latency_ms=row["delivery_latency_ms"],
        payload_sha256=str(row["payload_sha256"] or ""),
    )


class SqliteEventSource:
    """Read-only adapter over the local dev SQLite database.

    `SNAPLIST_DB` is the same env var `apps/api/repository.py::build_repository` uses, so a
    `make dev` database and this report agree on which file is being measured.
    """

    def __init__(self, database: str | Path | None = None) -> None:
        if database is None:
            database = os.environ.get("SNAPLIST_DB", "")
        self.database = str(database)
        self.source_label = f"sqlite:{self.database}" if self.database else "sqlite:<unset>"

    def load(self) -> SourceData:
        """Read every table that exists. A missing file or missing `events` table is 3a, NOT
        an empty result set: we never create anything to make the source look present.
        """
        if not self.database:
            return SourceData(source_label=self.source_label, present=False, events_table=False)
        try:
            connection = sqlite3.connect(f"file:{self.database}?mode=ro", uri=True)
        except sqlite3.Error:
            return SourceData(source_label=self.source_label, present=False, events_table=False)

        try:
            connection.row_factory = sqlite3.Row
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "select name from sqlite_master where type = 'table'"
                ).fetchall()
            }
            if "events" not in tables:
                return SourceData(
                    source_label=self.source_label, present=True, events_table=False
                )
            return self._read(connection, tables)
        finally:
            connection.close()

    def _read(self, connection: sqlite3.Connection, tables: set[str]) -> SourceData:
        """Materialize events, item state, jobs and the refund ledger."""
        columns = ", ".join(_EVENT_COLUMNS)
        raw_events = connection.execute(f"select {columns} from events").fetchall()

        # Shape validation applies ONLY to the columns we SELECT and actually join on. A NULL
        # item_id is legal (`subscription_started`, settings-page paywall) and is evidence
        # absence, not corruption; a non-NULL malformed id is unparseable -> 3b.
        events: list[EventRow] = []
        malformed: list[str] = []
        for row in raw_events:
            event = _row_to_event(row)
            if event.item_id is not None and not is_uuid_shaped(event.item_id):
                malformed.append(event.event_id)
            events.append(event)

        event_item_ids = {
            event.item_id for event in events if event.item_id is not None
        }

        items: dict[str, str | None] = {}
        if "items" in tables:
            for row in connection.execute("select id, deleted_at from items").fetchall():
                items[str(row["id"])] = row["deleted_at"]

        jobs_by_item: dict[str, list[str]] = {}
        if "jobs" in tables:
            for row in connection.execute(
                "select item_id, state, reason_code from jobs"
            ).fetchall():
                jobs_by_item.setdefault(str(row["item_id"]), []).append(
                    str(row["reason_code"] or "")
                )

        ledger, ledger_unparseable = self._attribution(connection, tables)

        # Failure mix: only positive evidence (a surviving `jobs` row with a contract reason
        # code) excludes an item. Soft-deleted items KEEP their events (Bert ruling 1), and a
        # hard-deleted orphan with no jobs row is NOT excluded — it is reported separately.
        failed_items: set[str] = set()
        failure_mix: dict[str, int] = {code: 0 for code in FAILURE_MIX_CODES}
        for item_id, reasons in jobs_by_item.items():
            # DISTINCT item per code, not per job row (addendum 1, C). One item with three
            # failed jobs is one excluded item; counting rows would inflate excluded_rate
            # past 0.20 and manufacture a fake 3b.
            for code in set(reasons) & set(FAILURE_MIX_CODES):
                failure_mix[code] += 1
                failed_items.add(item_id)

        # `failure_mix_unattributable`: items that HAVE events but no surviving jobs row, so
        # their reason_code is unrecoverable (jobs.item_id is ON DELETE CASCADE). This is an
        # honest residual of ADR-008's no-FK design, not data corruption (addendum 1, D).
        unattributable_items = event_item_ids - set(jobs_by_item)
        orphan_item_ids = event_item_ids - set(items)

        return SourceData(
            source_label=self.source_label,
            backend="local-dev",
            present=True,
            events_table=True,
            events=tuple(events),
            items=frozenset(items),
            deleted_items=frozenset(
                item_id for item_id, deleted_at in items.items() if deleted_at is not None
            ),
            failed_items=frozenset(failed_items),
            failure_mix=failure_mix,
            failure_mix_unattributable=len(unattributable_items),
            orphan_item_ids=frozenset(orphan_item_ids),
            attribution=ledger,
            unparseable_rows=len(malformed) + ledger_unparseable,
            malformed_event_ids=tuple(malformed),
        )

    def _attribution(
        self, connection: sqlite3.Connection, tables: set[str]
    ) -> tuple[Attribution, int]:
        """Refund attribution via the ledger self-join, with a generic NULL/malformed split.

        NULL join key  -> evidence absence -> DEGRADED (counted in `missing_rate`, NOT 3b).
        Malformed key  -> data corruption -> unparseable -> 3b.
        This classification is generic: `0001_init.sql:231-232` SET NULL is a REAL producer of
        these NULLs on Postgres, not just something a mutation fixture invents.

        Returns `(attribution, unparseable_join_keys)`; the second value is folded into
        `SourceData.unparseable_rows` so `source_state()` can classify 3b.
        """
        if "usage_events" not in tables:
            return Attribution(), 0

        # Explicit SELECT: ONLY the two join keys. `job_refunded.item_id` is never read — in
        # production `refund_one()` leaves it NULL, so reading it would silently exclude zero
        # items while printing a confident `excluded_refunded=0`. Shape validation therefore
        # applies only to the keys we SELECT and actually join on, never blanket over the
        # whole ledger (Q2 / addendum 2, #2).
        joined_rows = connection.execute(_LEDGER_SELF_JOIN).fetchall()

        total = len(joined_rows)
        attributable = 0
        unattributable = 0
        corrupted = 0
        excluded_item_ids: set[str] = set()
        for row in joined_rows:
            job_id = row["refund_job_id"]
            item_id = row["enqueued_item_id"]
            if job_id is None or item_id is None:
                # NULL = EVIDENCE ABSENCE (a real producer: 0001_init.sql:231-232 SET NULL on
                # Postgres). Counted in `missing_rate` -> DEGRADED, never 3b.
                unattributable += 1
                continue
            if not is_uuid_shaped(job_id) or not is_uuid_shaped(item_id):
                # Non-NULL but malformed = data corruption -> unparseable -> 3b. No lower(),
                # no UUID() coercion: two distinct items must never merge into one.
                corrupted += 1
                continue
            attributable += 1
            excluded_item_ids.add(str(item_id))

        attribution = build_attribution(
            refund_rows_total=total,
            refund_rows_attributable=attributable,
            unattributable=unattributable,
            excluded_item_ids=frozenset(excluded_item_ids),
        )
        if corrupted:
            attribution = Attribution(
                status=AttributionStatus.UNVERIFIED,
                refund_rows_total=total,
                refund_rows_attributable=attributable,
                unattributable=unattributable,
                missing_rate=attribution.missing_rate,
                excluded_item_ids=attribution.excluded_item_ids,
            )
        return attribution, corrupted
