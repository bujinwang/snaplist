"""Real SQLite fixtures + a real CLI subprocess runner for E0-T5.

Correctness in this package is proven by **real database behaviour** and **real process exit
codes**, never by asserting SQL or Makefile text. This module builds a small but faithful copy of
the four tables the gate reads (`events`, `items`, `jobs`, `usage_events`) and drives
`python -m evals.telemetry` as a subprocess with a pinned `--now`.

The `events` fixture intentionally has NO uuid-shape CHECK on `item_id`: pre-0003 file databases
and the raw SQLite ledger have no shape constraint either, so malformed ids must be detectable
by the reader (3b) rather than being rejected by a fixture-only constraint that production lacks.

The FK asymmetry is real, not simulated by a DELETE in the test: `jobs.item_id` is
`references items(id) on delete cascade` and the writer enables `pragma foreign_keys = on`,
while `usage_events` and `events.item_id` have NO foreign key at all (ADR-008 / the append-only
ledger). A hard delete therefore removes jobs but leaves both the funnel events and the quota
ledger rows intact — the exact shape measured in addendum 1, B.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# Fixed window end so every CLI run is reproducible.
NOW = "2026-11-05T00:00:00.000000Z"
NOW_DT = datetime(2026, 11, 5, tzinfo=UTC)
WINDOW_START = "2026-10-29T00:00:00.000000Z"

REPO_ROOT = Path(__file__).resolve().parents[3]
TELEMETRY_DIR = Path(__file__).resolve().parents[1]

# A day inside [now - 7d, now).
IN_WINDOW = "2026-11-04T12:00:00.000000Z"
# Exactly the window end — a half-open window must EXCLUDE it (mutation 3: `< end` -> `<=`).
AT_WINDOW_END = NOW
JUST_OUTSIDE = "2026-11-05T00:00:00.000001Z"
BEFORE_WINDOW = "2026-10-28T23:59:59.999999Z"


def item_uuid(index: int) -> str:
    """A 36-character lowercase-hex uuid-shaped TEXT id."""
    return f"{index:08x}-0000-4000-8000-000000000000"


def job_uuid(index: int) -> str:
    """A distinct uuid-shaped job id."""
    return f"{index:08x}-1111-4111-8111-111111111111"


def event_uuid(index: int) -> str:
    """A distinct uuid-shaped event id."""
    return f"{index:08x}-2222-4222-8222-222222222222"


def subject_uuid(seller: int) -> str:
    """A uuid-shaped seller suffix (ADR-003 uses the suffix as the identity)."""
    return f"{seller:08x}-3333-4333-8333-333333333333"


def subject(seller: int) -> str:
    return f"usr_{subject_uuid(seller)}"


SCHEMA = """
create table items (
    id         text primary key,
    deleted_at text
);
create table jobs (
    id         text primary key,
    item_id    text not null references items (id) on delete cascade,
    state      text not null,
    reason_code text
);
create table usage_events (
    id      integer primary key autoincrement,
    event   text not null,
    units   integer not null,
    job_id  text,
    item_id text
);
create table events (
    event_id            text primary key,
    event               text not null,
    received_at         text not null,
    client_ts           text not null,
    session_id          text not null,
    subject             text not null,
    app_version         text not null,
    platform            text not null,
    item_id             text,
    decision            text,
    job_latency_ms      integer,
    prettify_latency_ms integer,
    attrs_latency_ms    integer,
    edited_field_count  integer,
    fields_touched      integer,
    field_names         text,
    marketplace         text,
    row_count           integer,
    export_latency_ms   integer,
    bytes               integer,
    delivery_latency_ms integer,
    payload_sha256      text
);
"""


class Db:
    """A real on-disk SQLite database with helpers for inserting production-shaped rows."""

    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self.connection = connection
        self.path = path
        self._serial = 0

    def _next(self) -> int:
        self._serial += 1
        return self._serial

    def item(self, index: int, *, deleted_at: str | None = None, exists: bool = True) -> str:
        """Insert an `items` row. `exists=False` models a HARD delete (no row at all)."""
        item_id = item_uuid(index)
        if exists:
            self.connection.execute(
                "insert into items (id, deleted_at) values (?, ?)", (item_id, deleted_at)
            )
        return item_id

    def job(
        self,
        index: int,
        item_id: str,
        *,
        state: str = "done",
        reason_code: str | None = None,
        exists: bool = True,
    ) -> str:
        """Insert a `jobs` row. `exists=False` models the CASCADE that follows a hard delete."""
        job_id = job_uuid(index)
        if exists:
            self.connection.execute(
                "insert into jobs (id, item_id, state, reason_code) values (?, ?, ?, ?)",
                (job_id, item_id, state, reason_code),
            )
        return job_id

    def ledger_enqueued(self, job_id: str | None, item_id: str | None) -> None:
        """The `spend_one()` row: carries item_id, the key the self-join recovers."""
        self.connection.execute(
            "insert into usage_events (event, units, job_id, item_id) "
            "values ('item_enqueued', -1, ?, ?)",
            (job_id, item_id),
        )

    def ledger_refunded(self, job_id: str | None, *, item_id: str | None = None) -> None:
        """The `refund_one()` row: production leaves item_id NULL (usage.py:215-223)."""
        self.connection.execute(
            "insert into usage_events (event, units, job_id, item_id) "
            "values ('job_refunded', 1, ?, ?)",
            (job_id, item_id),
        )

    def event(
        self,
        *,
        name: str,
        item_id: str | None,
        seller: int = 0,
        received_at: str = IN_WINDOW,
        client_ts: str | None = None,
        subject_text: str | None = None,
        decision: str | None = None,
        prettify_latency_ms: int | None = None,
        attrs_latency_ms: int | None = None,
        fields_touched: int | None = None,
        export_latency_ms: int | None = None,
        delivery_latency_ms: int | None = None,
        event_id: str | None = None,
    ) -> str:
        """Insert one `events` row with an explicit column list."""
        serial = self._next()
        eid = event_id or event_uuid(serial)
        who = subject_text if subject_text is not None else subject(seller)
        self.connection.execute(
            "insert into events (event_id, event, received_at, client_ts, session_id, subject, "
            "app_version, platform, item_id, decision, job_latency_ms, prettify_latency_ms, "
            "attrs_latency_ms, edited_field_count, fields_touched, field_names, marketplace, "
            "row_count, export_latency_ms, bytes, delivery_latency_ms, payload_sha256) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eid,
                name,
                received_at,
                client_ts or received_at,
                "sess-1a2b3c",
                who,
                "1.4.0",
                "ios",
                item_id,
                decision,
                None,
                prettify_latency_ms,
                attrs_latency_ms,
                None,
                fields_touched,
                None,
                None,
                None,
                export_latency_ms,
                None,
                delivery_latency_ms,
                "0" * 64,
            ),
        )
        return eid

    def hard_delete(self, item_id: str) -> None:
        """Delete only the `items` row; the real FK cascade removes `jobs` on its own.

        `usage_events` and `events` have no FK, so their rows survive with their original ids.
        """
        self.connection.execute("delete from items where id = ?", (item_id,))

    def full_item(
        self,
        index: int,
        *,
        seller: int | None = None,
        received_at: str = IN_WINDOW,
        prettify_latency_ms: int = 1_000,
        attrs_latency_ms: int = 1_000,
        export_latency_ms: int = 1_000,
        delivery_latency_ms: int = 9_000,
        fields_touched: int = 3,
        retries: tuple[int, ...] = (),
        with_export_clicked: bool = True,
        with_attributes_edited: bool = True,
        with_delivery: bool = True,
    ) -> str:
        """A complete item timeline: approve (+ optional retries), fields, click, delivery.

        `export_latency_ms` lives on `export_clicked` and `delivery_latency_ms` on
        `export_delivered` — the two differ by default so the PLAN:124 reconciliation can be
        proven behaviourally: tool time must use the CLICK value, never the delivery one.
        """
        who = seller if seller is not None else index % 3
        item_id = self.item(index)
        self.job(index, item_id)

        # Retries come first; the last `approve` wins for attrs_latency_ms, while tool_time
        # SUMS prettify_latency_ms across every attempt (PLAN:117-136).
        for retry_latency in retries:
            self.event(
                name="prettify_approved",
                item_id=item_id,
                seller=who,
                received_at=received_at,
                decision="retry",
                prettify_latency_ms=retry_latency,
                attrs_latency_ms=attrs_latency_ms,
            )
        self.event(
            name="prettify_approved",
            item_id=item_id,
            seller=who,
            received_at=received_at,
            decision="approve",
            prettify_latency_ms=prettify_latency_ms,
            attrs_latency_ms=attrs_latency_ms,
        )
        if with_attributes_edited:
            self.event(
                name="attributes_edited",
                item_id=item_id,
                seller=who,
                received_at=received_at,
                fields_touched=fields_touched,
            )
        if with_export_clicked:
            self.event(
                name="export_clicked",
                item_id=item_id,
                seller=who,
                received_at=received_at,
                export_latency_ms=export_latency_ms,
            )
        if with_delivery:
            self.event(
                name="export_delivered",
                item_id=item_id,
                seller=who,
                received_at=received_at,
                delivery_latency_ms=delivery_latency_ms,
            )
        return item_id


def make_db(path: Path, *, with_events_table: bool = True) -> Db:
    """Create the fixture database file. `with_events_table=False` models source absent (3a)."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("pragma foreign_keys = on")
    if with_events_table:
        connection.executescript(SCHEMA)
    connection.commit()
    return Db(connection, path)


def populate_baseline(db: Db, *, count: int = 30, **kwargs: object) -> None:
    """Thirty complete items across three sellers: all eight gates PASS with n = 30."""
    for index in range(1, count + 1):
        db.full_item(index, **kwargs)  # type: ignore[arg-type]
    db.connection.commit()


def close(db: Db) -> None:
    """Close the writer connection so the CLI's read-only connection sees committed rows."""
    db.connection.commit()
    db.connection.close()


def run_cli(
    db_path: Path,
    *args: str,
    now: str = NOW,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `python -m evals.telemetry` as a REAL subprocess and return stdout + returncode."""
    command = [
        sys.executable,
        "-m",
        "evals.telemetry",
        "--db",
        str(db_path),
        "--now",
        now,
        *args,
    ]
    child_env = {**os.environ, "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    if env:
        child_env.update(env)
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        env=child_env,
        check=False,
    )


def window_end_literal() -> str:
    """The exact `--now` used by every CLI run, pinned so the half-open window is testable."""
    return NOW


def iso_days_before(days: int) -> str:
    """Helper for tests that need a timestamp relative to the pinned window end."""
    return (NOW_DT - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """A real on-disk temp directory, independent of pytest's `tmp_path_factory`.

    This environment's sandbox shim raises EEXIST for `Path.mkdir(exist_ok=True)` when the
    shared pytest basetemp already exists, which makes the plain acceptance command
    `python -m pytest evals/telemetry/tests -q` fail at fixture setup for reasons that have
    nothing to do with the gate. `tempfile.mkdtemp()` is not intercepted by that shim, so the
    suite stays runnable without `--basetemp`. Test modules import this fixture by name.
    """
    directory = Path(tempfile.mkdtemp(prefix="sl-e0t5-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)
