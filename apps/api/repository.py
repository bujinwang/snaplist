"""Storage abstraction + the SQLite implementation that makes the slice runnable.

Why a protocol and not direct SQL in the router
-----------------------------------------------
The real deployment talks to Supabase/Postgres through `infra/migrations/0001_init.sql`. That
needs credentials nobody has in this environment, so every endpoint in `apps/api/routers/`
takes a `Repository` and the tests hand it `SqliteRepository(":memory:")`. When E5-T4/E5-T5
land, a `PostgresRepository` implements the same protocol and **no router changes**. The
protocol is the seam, and it is small enough to read in one screen.

What this implementation is not
-------------------------------
It is not a SQLite-shaped imitation of every Postgres nicety. Specifically:

* `SELECT ... FOR UPDATE SKIP LOCKED` does not exist in SQLite. `SqliteRepository` is safe
  because there is one process, one connection and one re-entrant lock; the *Postgres* claim
  query is in `infra/migrations/0001_init.sql` comments and `apps/api/jobs/state_machine.py`
  and is what actually enforces single-worker ownership in production.
* Row-level security is a database feature. The isolation guarantee in this slice comes from
  every query carrying `user_id` as a predicate, and `apps/api/tests/test_isolation.py`
  proves it with two identities. It is not a substitute for RLS and does not pretend to be.

Timestamps
----------
Stored as ISO-8601 UTC text with a `Z` suffix, which is the same string the API puts on the
wire and the same format the zod mirror validates. One format, no timezone surprises.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from apps.api.schemas import (
    AssetPack,
    ItemAttributes,
    Job,
    JobKind,
    JobState,
    PlanTier,
    ReasonCode,
)


# ---------------------------------------------------------------------------------------
# Time helpers — one format, everywhere.
# ---------------------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(UTC)


def to_wire(value: datetime | None) -> str | None:
    """ISO-8601 UTC with a `Z` suffix, the format in docs/CONTRACTS.md."""
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def from_wire(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


# ---------------------------------------------------------------------------------------
# Records — plain frozen dataclasses, no ORM, no lazy loading, no surprise queries.
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class AssetRecord:
    kind: str
    position: int
    storage_path: str
    #: Measured by the writer, never accepted from a caller. None means "not measured".
    byte_size: int | None = None
    #: Stage metadata: sha256, content_type, backend, seconds. Absent values are absent.
    meta: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ItemRecord:
    id: UUID
    user_id: UUID
    status: str
    attributes: ItemAttributes | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    assets: tuple[AssetRecord, ...] = ()

    def path_for(self, kind: str, position: int = 0) -> str | None:
        for asset in self.assets:
            if asset.kind == kind and asset.position == position:
                return asset.storage_path
        return None

    def on_model_paths(self) -> list[str]:
        return [
            asset.storage_path
            for asset in sorted(self.assets, key=lambda a: a.position)
            if asset.kind == "on_model"
        ]

    def to_asset_pack(
        self, url_for: Callable[[str], str | None] | None = None
    ) -> AssetPack:
        """The five frozen keys. "Not ready yet" is `null`/`[]` inside a 200.

        `url_for` is the seam between "a path in the bucket" and "a URL a phone can fetch":

        * with `url_for` (what the router passes) each `storage_path` is signed on the way
          out, so the row keeps a canonical key and the URL keeps a TTL;
        * without it (tests, the worker, `apps/api/devtools.py`) the raw key is returned,
          which is the pre-E5-T4 behaviour and makes an assertion about a path readable.

        A key the resolver declines (storage unconfigured) is dropped from `on_model_urls`
        rather than emitted as `null`: that list is typed `list[str]` in the contract.
        """
        if url_for is None:
            return AssetPack(
                item_id=self.id,
                prettified_url=self.path_for("prettified"),
                cutout_url=self.path_for("cutout"),
                on_model_urls=self.on_model_paths(),
                attributes=self.attributes,
            )

        def resolve(path: str | None) -> str | None:
            return None if path is None else url_for(path)

        on_model: list[str] = []
        for path in self.on_model_paths():
            url = url_for(path)
            if url is not None:
                on_model.append(url)
        return AssetPack(
            item_id=self.id,
            prettified_url=resolve(self.path_for("prettified")),
            cutout_url=resolve(self.path_for("cutout")),
            on_model_urls=on_model,
            attributes=self.attributes,
        )


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: UUID
    item_id: UUID
    user_id: UUID
    kind: JobKind
    state: JobState
    attempts: int
    progress: float
    created_at: datetime
    updated_at: datetime
    reason_code: ReasonCode | None = None
    error_detail: str | None = None
    claimed_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_job(self) -> Job:
        return Job(
            id=self.id,
            item_id=self.item_id,
            kind=self.kind,
            state=self.state,
            reason_code=self.reason_code,
            attempts=self.attempts,
            progress=self.progress,
            error_detail=self.error_detail,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


@dataclass(frozen=True, slots=True)
class UsageEventRecord:
    user_id: UUID
    event: str
    units: int
    created_at: datetime
    job_id: UUID | None = None
    item_id: UUID | None = None
    plan: PlanTier | None = None
    marketplace: str | None = None
    fields_touched: int | None = None
    edited_field_count: int | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One row of `public.events` — the analytics funnel, not the quota ledger.

    Deliberately **not** `UsageEventRecord` and deliberately not merged with it:
    `usage_events` carries signed `units` and moves a quota counter, this table carries no
    money at all and is idempotent on a client-supplied `event_id`. One type holding both
    concepts would invite a funnel write to spend quota. The method names keep them apart
    too (`append_usage_event` / `append_analytics_event`).

    Every payload field is optional here even though the database CHECKs make it mandatory
    *for its own event*. The reason is the same one `ItemAttributes` has: the constraint is
    the authority, and a dataclass that also enforced the per-event matrix would be a second
    copy of it, in a third language, with no test forcing agreement.

    `platform`, `event` and the payload enums are `str`, not the router's `StrEnum`s: this
    module may not import `apps.api.routers.events` (the router imports this module), and a
    circular import to win a type is a bad trade. The router validates before it builds an
    `EventRecord`, and the CHECK constraints validate again.
    """

    event_id: UUID
    event: str
    client_ts: datetime
    received_at: datetime
    session_id: str
    subject: str
    app_version: str
    platform: str
    payload_sha256: str
    # 1. upload_started
    source: str | None = None
    capture_mode: str | None = None
    batch_index: int | None = None
    # 2. prettify_approved
    decision: str | None = None
    job_latency_ms: int | None = None
    prettify_latency_ms: int | None = None
    attrs_latency_ms: int | None = None
    # 3. attributes_edited
    edited_field_count: int | None = None
    fields_touched: int | None = None
    field_names: tuple[str, ...] | None = None
    # 4. export_clicked / 7. export_delivered
    marketplace: str | None = None
    row_count: int | None = None
    export_latency_ms: int | None = None
    bytes: int | None = None
    delivery_latency_ms: int | None = None
    # 5. subscription_started
    plan: str | None = None
    paywall_variant: str | None = None
    amount_cents: int | None = None
    currency: str | None = None
    # 6. paywall_impression
    paywall_trigger: str | None = None
    plan_offered: str | None = None


class RepositoryError(RuntimeError):
    """Base class for storage-level refusals."""


# ---------------------------------------------------------------------------------------
# The protocol every storage backend implements.
# ---------------------------------------------------------------------------------------
@runtime_checkable
class Repository(Protocol):
    """The whole storage surface the API needs. Every method is owned by the caller."""

    def create_item(self, *, user_id: UUID, status: str = "draft") -> ItemRecord: ...

    def get_item(
        self, item_id: UUID, *, user_id: UUID, include_deleted: bool = False
    ) -> ItemRecord | None: ...

    def update_item_attributes(
        self, item_id: UUID, *, user_id: UUID, attributes: ItemAttributes
    ) -> ItemRecord | None: ...

    def soft_delete_item(self, item_id: UUID, *, user_id: UUID) -> bool: ...

    def set_asset(
        self,
        item_id: UUID,
        *,
        user_id: UUID,
        kind: str,
        storage_path: str,
        position: int = 0,
        byte_size: int | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None: ...

    def create_job(self, *, item_id: UUID, user_id: UUID, kind: JobKind) -> JobRecord: ...

    def get_job(self, job_id: UUID, *, user_id: UUID) -> JobRecord | None: ...

    def update_job(self, job: JobRecord) -> JobRecord: ...

    def peek_next_queued_job(self) -> JobRecord | None: ...

    def has_finished_job(self, item_id: UUID) -> bool: ...

    def list_jobs_by_state(self, state: JobState) -> list[JobRecord]: ...

    def append_usage_event(self, event: UsageEventRecord) -> None: ...

    def quota_units_used(self, user_id: UUID, *, start: datetime, end: datetime) -> int: ...

    def count_events(
        self, user_id: UUID, event: str, *, start: datetime, end: datetime
    ) -> int: ...

    def has_event_for_job(self, job_id: UUID, event: str) -> bool: ...

    def get_plan(self, user_id: UUID) -> PlanTier: ...

    def set_plan(self, user_id: UUID, plan: PlanTier) -> None: ...

    def plan_limit(self, plan: PlanTier) -> int | None: ...

    def set_plan_limit(self, plan: PlanTier, items_per_month: int) -> None: ...

    def get_idempotent_item(
        self, key: str, *, user_id: UUID, endpoint: str
    ) -> tuple[UUID, UUID] | None: ...

    def store_idempotent_item(
        self, key: str, *, user_id: UUID, endpoint: str, item_id: UUID, job_id: UUID
    ) -> None: ...

    # -- analytics funnel (`public.events`) -------------------------------------------
    # Not to be confused with `append_usage_event` above: that one writes the quota ledger,
    # this one writes the funnel. `count_events` also counts `usage_events` and is unrelated
    # to these two, which is why the new names say `analytics_event` out loud.
    def append_analytics_event(self, event: EventRecord) -> bool: ...

    def get_analytics_event(self, event_id: UUID) -> EventRecord | None: ...

    @contextmanager
    def transaction(self) -> Iterator[None]: ...


_SCHEMA = """
create table if not exists profiles (
    user_id      text primary key,
    plan         text not null default 'free',
    -- ADR-003: anonymous sellers exist from first launch; conversion keeps the same user_id.
    is_anonymous integer not null default 1,
    merged_into  text references profiles (user_id),
    created_at   text not null
);

-- Runtime override of the compiled ceilings in apps/api/settings.py. Mirrors the Postgres
-- `plan_limits` table: the row is authoritative, Settings is the fallback when the row is
-- missing (see apps/api/usage.py::plan_ceiling). The seed below must equal the Settings
-- defaults — `test_usage.py::test_plan_limits_seed_matches_settings` fails if it does not.
create table if not exists plan_limits (
    plan            text primary key,
    items_per_month integer not null check (items_per_month > 0)
);
insert or ignore into plan_limits (plan, items_per_month)
    values ('free', 3), ('starter', 100), ('pro', 400);

create table if not exists items (
    id         text primary key,
    user_id    text not null,
    status     text not null,
    attributes text,
    deleted_at text,
    created_at text not null,
    updated_at text not null
);
create index if not exists items_user_created on items (user_id, created_at desc);

create table if not exists assets (
    item_id      text not null references items (id) on delete cascade,
    user_id      text not null,
    kind         text not null,
    position     integer not null default 0,
    storage_path text not null,
    -- measured by whoever wrote the bytes; NULL means unmeasured and never a fabricated 0
    byte_size    integer,
    -- stage metadata as JSON text (sha256, content_type, backend); mirrors `assets.meta jsonb`
    meta         text not null default '{}',
    created_at   text not null,
    primary key (item_id, kind, position)
);

create table if not exists jobs (
    id           text primary key,
    item_id      text not null references items (id) on delete cascade,
    user_id      text not null,
    kind         text not null,
    state        text not null,
    reason_code  text,
    attempts     integer not null default 0,
    progress     real not null default 0,
    error_detail text,
    claimed_by   text,
    -- cost auditability, mirroring infra/migrations/0001_init.sql: a stub self-reports 'stub',
    -- and cost_micros NULL means UNMEASURED (never 0)
    provider      text not null default 'stub',
    model_version text,
    cost_micros   integer,
    created_at   text not null,
    updated_at   text not null,
    started_at   text,
    finished_at  text,
    -- same three invariants as infra/migrations/0001_init.sql
    check ((state = 'failed') = (reason_code is not null)),
    check (attempts between 0 and 3),
    check (attempts < 3 or state in ('done', 'failed')),
    check (cost_micros is null or cost_micros >= 0)
);
create index if not exists jobs_user_created on jobs (user_id, created_at desc);
create index if not exists jobs_state_created on jobs (state, created_at);
-- `has_finished_job` runs on every direct upload (E5-T4) with `item_id = ? and state = ?`.
-- Without this, SQLite answers it by scanning the `state='queued'`/`done` slice and filtering
-- per row — fine at demo volume, wrong the moment one item has a job history. Postgres has
-- `jobs_item_idx` on (item_id, created_at desc) for the same lookup.
create index if not exists jobs_item on jobs (item_id);

create table if not exists usage_events (
    id                 integer primary key autoincrement,
    user_id            text not null,
    event              text not null,
    units              integer not null,
    job_id             text,
    item_id            text,
    plan               text,
    marketplace        text,
    fields_touched     integer,
    edited_field_count integer,
    metadata           text not null default '{}',
    created_at         text not null,
    check (units <> 0)
);
create index if not exists usage_events_user_created on usage_events (user_id, created_at desc);
create unique index if not exists usage_events_one_refund_per_job
    on usage_events (job_id) where event = 'job_refunded' and job_id is not null;
create unique index if not exists usage_events_one_spend_per_job
    on usage_events (job_id) where event = 'item_enqueued' and job_id is not null;

create table if not exists idempotency_keys (
    user_id    text not null,
    endpoint   text not null,
    key        text not null,
    item_id    text not null,
    job_id     text not null,
    created_at text not null,
    primary key (user_id, endpoint, key)
);

-- Analytics funnel. Mirror of `public.events` in infra/migrations/0002_events.sql, and the
-- table every request in this build actually writes to. Differences from Postgres, all forced
-- by SQLite and all narrower, never looser:
--   * `field_names` is JSON text instead of `text[]` (see `append_analytics_event`); the
--     per-element regex CHECK cannot be expressed here — it is enforced by the router
--     (pydantic `pattern`) and by `events_field_names_are_name_list()` on Postgres.
--   * No RLS: SQLite has no roles. Nothing in this process reads `events` except
--     `get_analytics_event`, which is called only by `POST /v1/events`.
-- Everything that *can* be enforced here is: the seven-name CHECK, the per-event required
-- columns, the "this column belongs to that event only" reverse CHECKs, and the shapes of the
-- four identifier columns (prefix + length + alphabet, which is GLOB's limit — it has no
-- bounded repetition). The one rule that genuinely cannot live here is `field_names`'
-- per-element regex, because it needs a subquery; that one is the router's pydantic pattern
-- plus `events_field_names_are_name_list()` on Postgres, and a length cap stands in for it
-- here. This comment used to claim the mirror was complete; QA inserted a subject of
-- `seller@example.com` and an `["color:red"]` name list straight through the repository to
-- show it was not.
create table if not exists events (
    event_id             text    primary key,
    event                text    not null check (event in (
        'upload_started', 'prettify_approved', 'attributes_edited', 'export_clicked',
        'subscription_started', 'paywall_impression', 'export_delivered'
    )),
    client_ts            text    not null,
    received_at          text    not null,
    -- shapes: prefix + length + alphabet. GLOB has no `{36}`, so the length is a separate
    -- clause; together they bound the column to the two subject forms the contract allows.
    session_id           text    not null
        check (length(session_id) between 1 and 64
               and session_id not glob '*[^A-Za-z0-9_-]*'),
    subject              text    not null
        -- The alphabet applies to the uuid part only: the prefix itself is letters outside
        -- [0-9a-f], so a whole-string GLOB would reject every legal subject.
        check ((length(subject) = 40 and substr(subject, 1, 4) = 'usr_'
                and substr(subject, 5) not glob '*[^0-9a-f-]*')
            or (length(subject) = 41 and substr(subject, 1, 5) = 'anon_'
                and substr(subject, 6) not glob '*[^0-9a-f-]*')),
    app_version          text    not null
        check (length(app_version) between 1 and 32
               and app_version not glob '*[^0-9A-Za-z.+-]*'),
    platform             text    not null check (platform in ('ios', 'android', 'web')),
    source               text    check (source is null or source in ('camera', 'library')),
    capture_mode         text    check (capture_mode is null
                                        or capture_mode in ('single', 'batch')),
    batch_index          integer check (batch_index is null or batch_index >= 0),
    decision             text    check (decision is null
                                        or decision in ('approve', 'retry', 'remove_bg_waitlist')),
    job_latency_ms       integer check (job_latency_ms is null or job_latency_ms >= 0),
    prettify_latency_ms  integer check (prettify_latency_ms is null or prettify_latency_ms >= 0),
    attrs_latency_ms     integer check (attrs_latency_ms is null or attrs_latency_ms >= 0),
    edited_field_count   integer check (edited_field_count is null or edited_field_count >= 0),
    fields_touched       integer check (fields_touched is null or fields_touched >= 0),
    -- JSON array of names, e.g. '["colors","size_label"]'. Element-wise validation needs a
    -- subquery (see the header); the length cap is what stops this becoming a free-text channel:
    -- 20 names of 64 characters fit comfortably, a 7 KB base64 blob does not.
    field_names          text    check (field_names is null or length(field_names) <= 2000),
    marketplace          text    check (marketplace is null
                                        or marketplace in ('ebay', 'poshmark', 'vinted')),
    row_count            integer check (row_count is null or row_count >= 1),
    export_latency_ms    integer check (export_latency_ms is null or export_latency_ms >= 0),
    bytes                integer check (bytes is null or bytes >= 0),
    delivery_latency_ms  integer check (delivery_latency_ms is null
                                        or delivery_latency_ms >= 0),
    plan                 text    check (plan is null or plan in ('starter', 'pro')),
    paywall_variant      text    check (paywall_variant is null
                                        or paywall_variant in ('a', 'b', 'c')),
    amount_cents         integer check (amount_cents is null or amount_cents >= 0),
    currency             text    check (currency is null or length(currency) = 3),
    paywall_trigger      text    check (paywall_trigger is null
                                        or paywall_trigger in ('upload_4th', 'on_model',
                                                               'export_limit')),
    plan_offered         text    check (plan_offered is null or plan_offered in ('starter', 'pro')),
    payload_sha256       text    not null check (length(payload_sha256) = 64),
    -- forward: this event carries these columns
    check (event <> 'upload_started'
           or (source is not null and capture_mode is not null)),
    check (event <> 'prettify_approved'
           or (decision is not null and job_latency_ms is not null
               and prettify_latency_ms is not null and attrs_latency_ms is not null)),
    check (event <> 'attributes_edited'
           or (edited_field_count is not null and fields_touched is not null)),
    check (event <> 'export_clicked'
           or (marketplace is not null and row_count is not null
               and export_latency_ms is not null)),
    check (event <> 'subscription_started'
           or (plan is not null and paywall_variant is not null
               and amount_cents is not null and currency is not null)),
    check (event <> 'paywall_impression'
           or (paywall_variant is not null and paywall_trigger is not null
               and plan_offered is not null)),
    check (event <> 'export_delivered'
           or (marketplace is not null and row_count is not null
               and bytes is not null and delivery_latency_ms is not null)),
    -- reverse: and no other event carries them
    check (event = 'upload_started'
           or (source is null and capture_mode is null and batch_index is null)),
    check (event = 'prettify_approved'
           or (decision is null and job_latency_ms is null
               and prettify_latency_ms is null and attrs_latency_ms is null)),
    check (event = 'attributes_edited'
           or (edited_field_count is null and fields_touched is null and field_names is null)),
    check (marketplace is null or event in ('export_clicked', 'export_delivered')),
    check (row_count is null or event in ('export_clicked', 'export_delivered')),
    check (export_latency_ms is null or event = 'export_clicked'),
    check ((bytes is null and delivery_latency_ms is null) or event = 'export_delivered'),
    check ((plan is null and amount_cents is null and currency is null)
           or event = 'subscription_started'),
    check (paywall_variant is null or event in ('subscription_started', 'paywall_impression')),
    check (paywall_trigger is null or event = 'paywall_impression'),
    check (plan_offered is null or event = 'paywall_impression')
);
create index if not exists events_event_received on events (event, received_at desc);
create index if not exists events_subject_received on events (subject, received_at desc);
"""


class SqliteRepository:
    """Single-connection SQLite storage. `":memory:"` for tests, a file path for `make dev`."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(
            self.database,
            check_same_thread=False,
            isolation_level=None,  # explicit BEGIN/COMMIT, never implicit transactions
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("pragma foreign_keys = on")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate_asset_columns()

    def _migrate_asset_columns(self) -> None:
        """Add `byte_size`/`meta` to an `assets` table created before E5-T4.

        `create table if not exists` does not touch an existing file, and `make dev` keeps
        one (`SNAPLIST_DB`), so a checkout that predates this ticket would otherwise fail on
        the first upload with "no such column". SQLite's `alter table ... add column` is the
        entire migration — the Postgres table already carries both columns.
        """
        columns = {row["name"] for row in self._query("pragma table_info(assets)")}
        if "byte_size" not in columns:
            self._execute("alter table assets add column byte_size integer")
        if "meta" not in columns:
            self._execute("alter table assets add column meta text not null default '{}'")

    # -- plumbing ----------------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """All-or-nothing block. Re-entrant: a nested call joins the outer transaction."""
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._conn.execute("begin immediate")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._conn.execute("rollback")
                raise
            else:
                self._depth -= 1
                if outermost:
                    self._conn.execute("commit")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))

    # -- items -------------------------------------------------------------------------
    def create_item(self, *, user_id: UUID, status: str = "draft") -> ItemRecord:
        now = utcnow()
        record = ItemRecord(
            id=uuid.uuid4(),
            user_id=user_id,
            status=status,
            attributes=None,
            created_at=now,
            updated_at=now,
        )
        self._execute(
            "insert into items (id, user_id, status, attributes, created_at, updated_at) "
            "values (?, ?, ?, null, ?, ?)",
            (str(record.id), str(user_id), status, to_wire(now), to_wire(now)),
        )
        return record

    def get_item(
        self, item_id: UUID, *, user_id: UUID, include_deleted: bool = False
    ) -> ItemRecord | None:
        rows = self._query(
            "select * from items where id = ? and user_id = ?",
            (str(item_id), str(user_id)),
        )
        if not rows:
            return None
        record = self._item_from_row(rows[0])
        if record.deleted_at is not None and not include_deleted:
            return None
        assets = self._query(
            "select kind, position, storage_path, byte_size, meta from assets "
            "where item_id = ? order by kind, position",
            (str(item_id),),
        )
        return ItemRecord(
            id=record.id,
            user_id=record.user_id,
            status=record.status,
            attributes=record.attributes,
            created_at=record.created_at,
            updated_at=record.updated_at,
            deleted_at=record.deleted_at,
            assets=tuple(
                AssetRecord(
                    kind=row["kind"],
                    position=int(row["position"]),
                    storage_path=row["storage_path"],
                    byte_size=int(row["byte_size"]) if row["byte_size"] is not None else None,
                    meta=json.loads(row["meta"]) if row["meta"] else {},
                )
                for row in assets
            ),
        )

    def update_item_attributes(
        self, item_id: UUID, *, user_id: UUID, attributes: ItemAttributes
    ) -> ItemRecord | None:
        current = self.get_item(item_id, user_id=user_id)
        if current is None:
            return None
        now = utcnow()
        self._execute(
            "update items set attributes = ?, updated_at = ? where id = ? and user_id = ?",
            (
                json.dumps(attributes.model_dump(mode="json"), sort_keys=True),
                to_wire(now),
                str(item_id),
                str(user_id),
            ),
        )
        return self.get_item(item_id, user_id=user_id)

    def soft_delete_item(self, item_id: UUID, *, user_id: UUID) -> bool:
        current = self.get_item(item_id, user_id=user_id)
        if current is None or current.deleted_at is not None:
            return False
        now = utcnow()
        # `status` and `deleted_at` move together: the Postgres CHECK requires it, and the
        # SQLite mirror keeps the same rule so a test cannot pass on one backend only.
        self._execute(
            "update items set status = 'deleted', deleted_at = ?, updated_at = ? "
            "where id = ? and user_id = ?",
            (to_wire(now), to_wire(now), str(item_id), str(user_id)),
        )
        return True

    def set_asset(
        self,
        item_id: UUID,
        *,
        user_id: UUID,
        kind: str,
        storage_path: str,
        position: int = 0,
        byte_size: int | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Upsert one `(item, kind, position)` slot. The newest write wins.

        `storage_path` is the canonical **object key**, never a signed URL: signing happens on
        the way out (`ItemRecord.to_asset_pack(url_for=...)`) because a URL embedded in a row
        expires while the row does not.

        `byte_size` and `meta` come from whoever measured the bytes; this method stores them
        verbatim and refuses to invent them. `None` byte_size is stored as NULL — unmeasured.
        """
        payload = json.dumps(dict(meta or {}), sort_keys=True)
        self._execute(
            "insert into assets (item_id, user_id, kind, position, storage_path, byte_size, "
            "meta, created_at) values (?, ?, ?, ?, ?, ?, ?, ?) "
            "on conflict (item_id, kind, position) do update "
            "set storage_path = excluded.storage_path, "
            "byte_size = excluded.byte_size, "
            "meta = excluded.meta",
            (
                str(item_id),
                str(user_id),
                kind,
                position,
                storage_path,
                byte_size,
                payload,
                to_wire(utcnow()),
            ),
        )

    @staticmethod
    def _item_from_row(row: sqlite3.Row) -> ItemRecord:
        raw = row["attributes"]
        attributes = ItemAttributes.model_validate(json.loads(raw)) if raw else None
        return ItemRecord(
            id=UUID(row["id"]),
            user_id=UUID(row["user_id"]),
            status=row["status"],
            attributes=attributes,
            created_at=from_wire(row["created_at"]) or utcnow(),
            updated_at=from_wire(row["updated_at"]) or utcnow(),
            deleted_at=from_wire(row["deleted_at"]),
        )

    # -- jobs --------------------------------------------------------------------------
    def create_job(self, *, item_id: UUID, user_id: UUID, kind: JobKind) -> JobRecord:
        now = utcnow()
        job = JobRecord(
            id=uuid.uuid4(),
            item_id=item_id,
            user_id=user_id,
            kind=kind,
            state=JobState.QUEUED,
            attempts=0,
            progress=0.0,
            created_at=now,
            updated_at=now,
        )
        self._execute(
            "insert into jobs (id, item_id, user_id, kind, state, attempts, progress, "
            "provider, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(job.id),
                str(item_id),
                str(user_id),
                kind.value,
                JobState.QUEUED.value,
                0,
                0.0,
                # Cost auditability: this build has no pipeline, so every job says so out loud.
                # A missing/stale 'stub' is what makes a real run identifiable later.
                "stub",
                to_wire(now),
                to_wire(now),
            ),
        )
        return job

    def get_job(self, job_id: UUID, *, user_id: UUID) -> JobRecord | None:
        rows = self._query(
            "select * from jobs where id = ? and user_id = ?", (str(job_id), str(user_id))
        )
        return self._job_from_row(rows[0]) if rows else None

    def update_job(self, job: JobRecord) -> JobRecord:
        """Persist a job record verbatim. The state machine decides legality, not this."""
        self._execute(
            "update jobs set state = ?, reason_code = ?, attempts = ?, progress = ?, "
            "error_detail = ?, claimed_by = ?, updated_at = ?, started_at = ?, finished_at = ? "
            "where id = ? and user_id = ?",
            (
                job.state.value,
                job.reason_code.value if job.reason_code else None,
                job.attempts,
                job.progress,
                job.error_detail,
                job.claimed_by,
                to_wire(job.updated_at),
                to_wire(job.started_at),
                to_wire(job.finished_at),
                str(job.id),
                str(job.user_id),
            ),
        )
        return job

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=UUID(row["id"]),
            item_id=UUID(row["item_id"]),
            user_id=UUID(row["user_id"]),
            kind=JobKind(row["kind"]),
            state=JobState(row["state"]),
            reason_code=ReasonCode(row["reason_code"]) if row["reason_code"] else None,
            attempts=int(row["attempts"]),
            progress=float(row["progress"]),
            error_detail=row["error_detail"],
            claimed_by=row["claimed_by"],
            created_at=from_wire(row["created_at"]) or utcnow(),
            updated_at=from_wire(row["updated_at"]) or utcnow(),
            started_at=from_wire(row["started_at"]),
            finished_at=from_wire(row["finished_at"]),
        )

    def peek_next_queued_job(self) -> JobRecord | None:
        """Oldest queued job whose item has its **source** bytes, without claiming it.

        Claiming is the caller's transaction plus the state machine's queued->running
        transition — deliberately NOT done here, so no repository backend can invent a sixth
        state.

        The `kind='source'` predicate is an invariant, not an optimisation: `POST /v1/items`
        creates the job *before* the bytes arrive (direct upload is a second request), so
        without it a worker would claim a job with no input, fail three times and refund a
        seller for work that never had a chance to run. An item with no source asset is
        "not ready to be claimed", which is exactly what `queued` plus missing bytes means.

        Postgres selects with `for update skip locked` (the claim query is documented in
        `infra/migrations/0001_init.sql`); SQLite relies on the single-writer
        `begin immediate` transaction, which is honest for this build: one process, one
        connection.
        """
        rows = self._query(
            "select * from jobs j where j.state = 'queued' "
            "and exists (select 1 from assets a "
            "            where a.item_id = j.item_id and a.kind = 'source') "
            "order by j.created_at, j.id limit 1"
        )
        return self._job_from_row(rows[0]) if rows else None

    def list_jobs_by_state(self, state: JobState) -> list[JobRecord]:
        rows = self._query(
            "select * from jobs where state = ? order by created_at, id", (state.value,)
        )
        return [self._job_from_row(row) for row in rows]

    def has_finished_job(self, item_id: UUID) -> bool:
        """True once any job for this item reached `done`.

        The direct-upload route asks this *before* overwriting the original photo. A finished
        job means the cutout/prettified/on-model assets in the bucket were derived from the
        bytes that are already there: replacing those bytes would leave a pack that describes
        a photo nobody has any more, with no error anywhere. 409 on the upload is the honest
        answer ("this item is processed; create a new one or re-run rendering"), and it is
        deliberately not "re-enqueue and wipe the downstream assets", which would be a change
        to the job model rather than to storage.
        """
        rows = self._query(
            "select 1 from jobs where item_id = ? and state = 'done' limit 1",
            (str(item_id),),
        )
        return bool(rows)

    # -- usage ledger ------------------------------------------------------------------
    def append_usage_event(self, event: UsageEventRecord) -> None:
        """Append-only. A duplicate refund raises `sqlite3.IntegrityError` by design."""
        self._execute(
            "insert into usage_events (user_id, event, units, job_id, item_id, plan, "
            "marketplace, fields_touched, edited_field_count, metadata, created_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(event.user_id),
                event.event,
                event.units,
                str(event.job_id) if event.job_id else None,
                str(event.item_id) if event.item_id else None,
                event.plan.value if event.plan else None,
                event.marketplace,
                event.fields_touched,
                event.edited_field_count,
                json.dumps(dict(event.metadata or {}), sort_keys=True),
                to_wire(event.created_at),
            ),
        )

    def quota_units_used(self, user_id: UUID, *, start: datetime, end: datetime) -> int:
        """Units spent in [start, end): spends are -1, refunds +1, so used = -sum."""
        rows = self._query(
            "select coalesce(sum(units), 0) as total from usage_events "
            "where user_id = ? and event in ('item_enqueued', 'job_refunded') "
            "and created_at >= ? and created_at < ?",
            (str(user_id), to_wire(start), to_wire(end)),
        )
        spent = -int(rows[0]["total"]) if rows else 0
        return max(spent, 0)

    def count_events(
        self, user_id: UUID, event: str, *, start: datetime, end: datetime
    ) -> int:
        rows = self._query(
            "select count(*) as n from usage_events where user_id = ? and event = ? "
            "and created_at >= ? and created_at < ?",
            (str(user_id), event, to_wire(start), to_wire(end)),
        )
        return int(rows[0]["n"]) if rows else 0

    def has_event_for_job(self, job_id: UUID, event: str) -> bool:
        rows = self._query(
            "select 1 from usage_events where job_id = ? and event = ? limit 1",
            (str(job_id), event),
        )
        return bool(rows)

    # -- analytics funnel (public.events) ----------------------------------------------
    def append_analytics_event(self, event: EventRecord) -> bool:
        """Insert one funnel row. True if written, False if that `event_id` already exists.

        `on conflict (event_id) do nothing` is the idempotency guarantee, and it is expressed
        here rather than in the router because a read-then-write check has a window: two
        retries of the same offline-queued event can arrive at the same time, and "did you
        already have it?" answered a millisecond ago is not an answer. `cursor.rowcount` is 0
        when the conflict was skipped, which is the only signal SQLite gives; Postgres would
        use `insert ... returning` and read zero rows for the same fact.

        A False here means **the row was not written** — it says nothing about *why*. The
        caller already read the existing row (`get_analytics_event`) to decide between 202
        duplicate and 409, and must re-read it after a False to cover the race above.

        `field_names` is stored as JSON text (SQLite has no array type). The order the client
        sent is preserved: it is not a set, and a report that wants "first three fields the
        seller touched" cannot recover it from a sorted copy.
        """
        with self._lock:
            cursor = self._conn.execute(
                "insert into events (event_id, event, client_ts, received_at, session_id, "
                "subject, app_version, platform, source, capture_mode, batch_index, decision, "
                "job_latency_ms, prettify_latency_ms, attrs_latency_ms, edited_field_count, "
                "fields_touched, field_names, marketplace, row_count, export_latency_ms, "
                "bytes, delivery_latency_ms, plan, paywall_variant, amount_cents, currency, "
                "paywall_trigger, plan_offered, payload_sha256) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?) "
                "on conflict (event_id) do nothing",
                (
                    str(event.event_id),
                    event.event,
                    to_wire(event.client_ts),
                    to_wire(event.received_at),
                    event.session_id,
                    event.subject,
                    event.app_version,
                    event.platform,
                    event.source,
                    event.capture_mode,
                    event.batch_index,
                    event.decision,
                    event.job_latency_ms,
                    event.prettify_latency_ms,
                    event.attrs_latency_ms,
                    event.edited_field_count,
                    event.fields_touched,
                    None if event.field_names is None else json.dumps(list(event.field_names)),
                    event.marketplace,
                    event.row_count,
                    event.export_latency_ms,
                    event.bytes,
                    event.delivery_latency_ms,
                    event.plan,
                    event.paywall_variant,
                    event.amount_cents,
                    event.currency,
                    event.paywall_trigger,
                    event.plan_offered,
                    event.payload_sha256,
                ),
            )
            return cursor.rowcount == 1

    def get_analytics_event(self, event_id: UUID) -> EventRecord | None:
        """The row for `event_id`, or None. No `user_id` predicate, on purpose.

        This is not a seller-facing read: it is the idempotency check inside a POST, where the
        caller has already proven it owns `subject` and the id is the client's own uuid. There
        is no `GET /v1/events`, so a foreign id cannot be probed for existence through any
        route (a 409 vs 202 on somebody else's id leaks nothing an attacker can use without
        already knowing the id *and* the exact payload digest — and they would have to be
        authenticated as the same subject anyway).
        """
        rows = self._query("select * from events where event_id = ?", (str(event_id),))
        return self._analytics_event_from_row(rows[0]) if rows else None

    @staticmethod
    def _analytics_event_from_row(row: sqlite3.Row) -> EventRecord:
        raw_names = row["field_names"]
        return EventRecord(
            event_id=UUID(row["event_id"]),
            event=row["event"],
            client_ts=from_wire(row["client_ts"]) or utcnow(),
            received_at=from_wire(row["received_at"]) or utcnow(),
            session_id=row["session_id"],
            subject=row["subject"],
            app_version=row["app_version"],
            platform=row["platform"],
            payload_sha256=row["payload_sha256"],
            source=row["source"],
            capture_mode=row["capture_mode"],
            batch_index=row["batch_index"],
            decision=row["decision"],
            job_latency_ms=row["job_latency_ms"],
            prettify_latency_ms=row["prettify_latency_ms"],
            attrs_latency_ms=row["attrs_latency_ms"],
            edited_field_count=row["edited_field_count"],
            fields_touched=row["fields_touched"],
            field_names=None if raw_names is None else tuple(json.loads(raw_names)),
            marketplace=row["marketplace"],
            row_count=row["row_count"],
            export_latency_ms=row["export_latency_ms"],
            bytes=row["bytes"],
            delivery_latency_ms=row["delivery_latency_ms"],
            plan=row["plan"],
            paywall_variant=row["paywall_variant"],
            amount_cents=row["amount_cents"],
            currency=row["currency"],
            paywall_trigger=row["paywall_trigger"],
            plan_offered=row["plan_offered"],
        )

    # -- plans -------------------------------------------------------------------------
    def get_plan(self, user_id: UUID) -> PlanTier:
        rows = self._query("select plan from profiles where user_id = ?", (str(user_id),))
        if not rows:
            return PlanTier.FREE
        try:
            return PlanTier(rows[0]["plan"])
        except ValueError:
            return PlanTier.FREE

    def set_plan(self, user_id: UUID, plan: PlanTier) -> None:
        self._execute(
            "insert into profiles (user_id, plan, created_at) values (?, ?, ?) "
            "on conflict (user_id) do update set plan = excluded.plan",
            (str(user_id), plan.value, to_wire(utcnow())),
        )

    # -- plan ceilings (runtime override of apps/api/settings.py) -----------------------
    def plan_limit(self, plan: PlanTier) -> int | None:
        """The `plan_limits` row, or None when there is none (caller falls back to Settings)."""
        rows = self._query(
            "select items_per_month from plan_limits where plan = ?", (plan.value,)
        )
        return int(rows[0]["items_per_month"]) if rows else None

    def set_plan_limit(self, plan: PlanTier, items_per_month: int) -> None:
        """Ops lever: raise or lower a ceiling without shipping a release."""
        if items_per_month <= 0:
            raise ValueError("items_per_month must be positive")
        self._execute(
            "insert into plan_limits (plan, items_per_month) values (?, ?) "
            "on conflict (plan) do update set items_per_month = excluded.items_per_month",
            (plan.value, items_per_month),
        )

    # -- idempotency -------------------------------------------------------------------
    def get_idempotent_item(
        self, key: str, *, user_id: UUID, endpoint: str
    ) -> tuple[UUID, UUID] | None:
        rows = self._query(
            "select item_id, job_id from idempotency_keys "
            "where user_id = ? and endpoint = ? and key = ?",
            (str(user_id), endpoint, key),
        )
        if not rows:
            return None
        return UUID(rows[0]["item_id"]), UUID(rows[0]["job_id"])

    def store_idempotent_item(
        self, key: str, *, user_id: UUID, endpoint: str, item_id: UUID, job_id: UUID
    ) -> None:
        self._execute(
            "insert or replace into idempotency_keys "
            "(user_id, endpoint, key, item_id, job_id, created_at) values (?, ?, ?, ?, ?, ?)",
            (str(user_id), endpoint, key, str(item_id), str(job_id), to_wire(utcnow())),
        )


def build_repository(database: str | Path | None = None) -> SqliteRepository:
    """Create the repository for a process. `SNAPLIST_DB` selects a file or `:memory:`.

    A file database is what `make dev` uses so a curl session survives an uvicorn reload;
    the test suite always passes `:memory:` explicitly through `create_app(repository=...)`.
    """
    import os

    target = database if database is not None else os.environ.get("SNAPLIST_DB", ":memory:")
    return SqliteRepository(target)
