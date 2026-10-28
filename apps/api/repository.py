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
from collections.abc import Iterator, Mapping, Sequence
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

    def to_asset_pack(self) -> AssetPack:
        """The five frozen keys. "Not ready yet" is `null`/`[]` inside a 200."""
        return AssetPack(
            item_id=self.id,
            prettified_url=self.path_for("prettified"),
            cutout_url=self.path_for("cutout"),
            on_model_urls=self.on_model_paths(),
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


class RepositoryError(RuntimeError):
    """Base class for storage-level refusals."""


# ---------------------------------------------------------------------------------------
# The protocol every storage backend implements.
# ---------------------------------------------------------------------------------------
@runtime_checkable
class Repository(Protocol):
    """The whole storage surface the API needs. Eleven methods, all owned by the caller."""

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
    ) -> None: ...

    def create_job(self, *, item_id: UUID, user_id: UUID, kind: JobKind) -> JobRecord: ...

    def get_job(self, job_id: UUID, *, user_id: UUID) -> JobRecord | None: ...

    def update_job(self, job: JobRecord) -> JobRecord: ...

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
            "select kind, position, storage_path from assets where item_id = ? "
            "order by kind, position",
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
    ) -> None:
        self._execute(
            "insert into assets (item_id, user_id, kind, position, storage_path, created_at) "
            "values (?, ?, ?, ?, ?, ?) "
            "on conflict (item_id, kind, position) do update "
            "set storage_path = excluded.storage_path",
            (str(item_id), str(user_id), kind, position, storage_path, to_wire(utcnow())),
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
