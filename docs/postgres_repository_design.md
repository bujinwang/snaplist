# PostgresRepository — Implementation Design (SnapList backend)

**Scope.** This document is the precise, reviewable design for the missing `PostgresRepository`
class in `apps/api/repository.py`. It is the sibling of `SqliteRepository` and implements the
**same `Repository` protocol** (lines 275–353) with the **same method signatures**, so no router
changes are required. It does **not** contain implementation code — it is the spec an Engineer
executes verbatim.

**Source of truth read for this design.**

- `apps/api/repository.py` — the `Repository` protocol (L275), `SqliteRepository` (L624, the
  reference implementation), the frozen record dataclasses, and `build_repository` (L1268).
- `infra/migrations/0001_init.sql`, `0002_events.sql`, `0003_events_item_id.sql` — the Postgres
  schema (8 product tables + RLS + the `updated_at` trigger + indexes).
- `infra/migrations/verify/local_supabase_shim.sql` — the vanilla-PG stand-in that defines
  `auth.uid()` as `current_setting('request.jwt.claim.sub', true)` and the
  `anon`/`authenticated`/`service_role` roles.
- `infra/migrations/verify/{rls_isolation_check,constraints_check}.sql` — the local proofs.
- `apps/api/tests/{conftest,isolation,events,usage,items,jobs,worker}.py` — the existing suite.

---

## 0. TL;DR — the two decisions the lead must confirm

1. **RLS + connection strategy → RECOMMEND CANDIDATE 2 (refined).** Connect as the trusted
   backend role (`service_role`, or a superuser-equivalent locally) which **bypasses RLS**, and
   enforce tenant isolation with the **explicit `user_id` predicate** that every seller-scoped
   method already carries — exactly mirroring `SqliteRepository`. The frozen `auth.uid()` RLS is
   *not* thrown away: it stays as the production defense-in-depth for any client connecting
   directly as `authenticated` (e.g. a phone via PostgREST), and it is independently proven
   locally by `rls_isolation_check.sql`. Rationale and the rejection of candidates 1 & 3 are in
   §1.

2. **A new migration `infra/migrations/0004_idempotency_keys.sql` is REQUIRED (not optional).**
   `get_idempotent_item` / `store_idempotent_item` are on the protocol, SQLite has the table, and
   **no Postgres migration defines it** (verified: `grep -r idempotency_keys infra/` → none).
   This is a functional gap independent of RLS, so the class cannot be written without it.

Everything else is mechanical translation of SQLite SQL to Postgres-flavoured SQL.

---

## 1. Connection + RLS strategy (the genuine design decision)

### 1.1 What the frozen schema actually permits

Reading `0001_init.sql` end-to-end, the RLS surface is:

| Table | `authenticated` policy | `service_role` policy |
|-------|----------------------|----------------------|
| `profiles`, `items`, `assets`, `jobs`, `usage_events`, `subscriptions`, `model_refs`, `api_keys` | `SELECT` (+ `INSERT`/`UPDATE` on owner rows for profiles/items/assets/model_refs/api_keys) | *(none — granted via `bypassrls`)* |
| `plan_limits` | `SELECT` only | *(none)* |
| `public.events` | **none** | `FOR ALL` (`events_service_role_all`) |

Two facts are decisive:

- **`jobs` and `usage_events` have NO `INSERT`/`UPDATE` policy for `authenticated`.** They are
  `SELECT`-only. `create_job`, `update_job`, and `append_usage_event` (the `item_enqueued` spend
  and the `job_refunded` refund) would be **RLS-denied** if the connection were `authenticated`.
- **`public.events` has ONLY a `service_role` policy.** `append_analytics_event`,
  `get_analytics_event` and the funnel writes can only run as `service_role`.

So the trusted API/worker backend, which creates jobs, appends the ledger, and writes the funnel,
**must** connect as `service_role` (or a `bypassrls` superuser-equivalent). A backend that tried
to route seller reads through `auth.uid()` via the `authenticated` role (candidate 3) would then
*also* need a second `service_role` connection for every write — i.e. you pay the cost of two
roles/connection-pools and still end up at `service_role` for the writes. Candidates 1 and 3 are
both therefore rejected as the backend's *primary* mechanism (details below).

### 1.2 Recommendation — CANDIDATE 2 (refined): trusted backend, explicit predicate, RLS bypassed

> **Connect as `service_role`** (production: the Supabase `SUPABASE_SERVICE_ROLE_KEY`; local test:
> the `postgres` superuser, which bypasses RLS by default). **Enforce tenant isolation with the
> explicit `user_id` predicate** that every seller-scoped method already carries, exactly as
> `SqliteRepository` does. Keep the "one connection + one re-entrant lock" model.

**Why this is correct for the Supabase production target (`auth.uid()`):**
- The frozen `auth.uid()` RLS is *not* removed or altered. It remains the protection for any
  client that connects **directly** as `authenticated` (a phone using the Supabase SDK /
  PostgREST). This is exactly the threat model the migrations describe ("a client that can insert
  `export_delivered` can inflate activation").
- The trusted backend using `service_role` + explicit predicate is the **standard Supabase
  backend pattern**: the service role is the server's "god" role, and ownership filtering is done
  in application code — which is precisely what this codebase already does for SQLite. No
  behavior changes; the method surfaces and the router are untouched.

**Why this is verifiable against the local vanilla PG:**
- The existing suite (`test_isolation.py`, `test_items.py`, `test_events.py`, `test_usage.py`,
  `test_jobs.py`, `test_worker.py`) passes **unchanged** against `PostgresRepository`, because
  isolation comes from the explicit `where user_id = ?` predicate — the same mechanism that makes
  them green on SQLite.
- The frozen `auth.uid()` RLS itself is proven locally by
  `infra/migrations/verify/rls_isolation_check.sql`, which runs as `set role authenticated; set
  request.jwt.claim.sub = <uuid>` — i.e. it already uses candidate 3's exact GUC. So "RLS works"
  is established even though the trusted backend bypasses it.
- A dedicated `apps/api/tests/test_rls_isolation.py` (§3) re-runs that proof from pytest, so the
  repo's "no router changes" claim and the RLS claim are both continuously tested.

**Local connection setup (exact SQL):**

```sql
-- local PG (PG 14.18), already running on localhost:5432 and /tmp:5432
-- 1. apply the schema (shim is what makes auth.uid() / roles exist on vanilla PG)
psql -v ON_ERROR_STOP=1 -d snaplist -f infra/migrations/verify/local_supabase_shim.sql
psql -v ON_ERROR_STOP=1 -d snaplist -f infra/migrations/0001_init.sql
psql -v ON_ERROR_STOP=1 -d snaplist -f infra/migrations/0002_events.sql
psql -v ON_ERROR_STOP=1 -d snaplist -f infra/migrations/0003_events_item_id.sql
psql -v ON_ERROR_STOP=1 -d snaplist -f infra/migrations/0004_idempotency_keys.sql   -- NEW
-- 2. local test connection uses the superuser (bypasses RLS): postgres://postgres@localhost:5432/snaplist
--    production uses the service_role DSN; both bypass RLS, so the explicit predicate is the guard.
```

**Python connection (sketch, in `PostgresRepository.__init__`):**

```python
import psycopg  # v3, sync — from the existing optional `pg` group (psycopg[binary]>=3.2)
self._conn = psycopg.connect(dsn, autocommit=True)   # explicit BEGIN/COMMIT, like isolation_level=None
self._lock = threading.RLock()                        # mirrors SqliteRepository's single-writer model
self._depth = 0
```

### 1.3 Why not candidate 1 (`SET LOCAL my.app_user` + a new policy)

It requires a **new migration** that adds a second, divergent identity mechanism (`my.app_user`)
alongside the frozen `auth.uid()`. That touches the frozen policy surface, drifts from what
Supabase actually does (`auth.uid()`), and duplicates the predicate logic. The frozen policies
already express "owner = caller" via `auth.uid()`; a parallel `my.app_user` is pure redundancy
that will eventually disagree with production. Rejected.

### 1.4 Why not candidate 3 (`auth.uid()` via `request.jwt.claim.sub`) as the backend's sole mechanism

Candidate 3's *GUC* (`request.jwt.claim.sub`) is exactly what the shim's `auth.uid()` reads and
what `rls_isolation_check.sql` uses — so it is the right way to **verify** the frozen policies.
But it cannot be the **backend's sole** mechanism: as shown in §1.1, an `authenticated`
connection is forbidden to create jobs, append the ledger, or write `events`. You would need a
second `service_role` connection for those writes anyway, which defeats the "one process, one
connection" assumption and adds role-switching. Candidate 2's single `service_role` connection
with the explicit predicate is simpler and identical in effect (isolation still guaranteed, now
by app code as in SQLite). **If the lead specifically wants the repository to exercise `auth.uid()`
RLS on seller reads, the blend is: keep `service_role` for all writes, and additionally run the
seller-READ paths under a second `authenticated` + `request.jwt.claim.sub` connection. This is a
stated change to the "one connection" assumption and is NOT recommended as the baseline.**

### 1.5 What stays UNVERIFIED (honesty, per the migrations' own notes)

- A **real Supabase project** hosting the schema (the shim + vanilla PG is not a Supabase
  project). The local run proves the DDL + the `auth.uid()`-based RLS reproduce the design;
  Supabase's hosted `auth.uid()` reads the same `request.jwt.claim.sub` GUC, so behaviour is
  expected to be identical.
- **Postgres 15** (local is 14.18). No 14/15-specific feature is used; low risk.
- **PostgREST** and the hosted `anon`/`authenticated`/`service_role` *grants* (the shim grants
  `authenticated` broad DML only to make `rls_isolation_check.sql` runnable; production grants
  follow the migration policies, not the shim's verification grant).
- **Asymmetric JWT verification** (ES256/RS256 via JWKS) — `apps/api/auth.py` returns 501 for
  those today, so it is out of scope for this backend class regardless of DB.

---

## 2. Method-by-method mapping (every protocol method → Postgres SQL)

**Conventions used below.**
- `%s` is the psycopg paramstyle. `PostgresRepository._execute` / `_query` MUST normalise `?` →
  `%s` (the existing tests call `repository._execute("delete from items where id = ?", ...)` with
  SQLite `?`; rewriting in the helper keeps those raw calls working unchanged against Postgres).
- Timestamps are passed as **tz-aware `datetime`** (UTC) and read back as `datetime`; the shared
  `to_wire`/`from_wire` helpers are only used when a value is a `str`.
- `jsonb` columns (`items.attributes`, `assets.meta`, `usage_events.metadata`) are passed as a
  Python `dict` and read back as a `dict` (psycopg adapts both). The row adapters must tolerate
  `dict` **and** `str` (SQLite returns JSON strings) so the shared `_from_row` logic is reused
  safely.
- `text[]` (`events.field_names` only — **not** present on `usage_events` in the protocol record)
  is passed as a `list[str]` / `None`.
- `updated_at` is **never written** on UPDATE (the `set_updated_at` trigger maintains it); it is
  written only on INSERTs where we also set `created_at` explicitly (the trigger fires on UPDATE
  only).
- All IDs are `uuid` columns → passed as `UUID` / `str`, read back as `UUID`.
- RLS is bypassed by the connection, so isolation is carried by the explicit `user_id` predicate
  on every seller-scoped query — byte-for-byte the same as `SqliteRepository`.

| # | Protocol method | Postgres SQL (sketch) | Materially different from SQLite? |
|---|-----------------|----------------------|-----------------------------------|
| 1 | `create_item(user_id, status="draft")` | `INSERT INTO public.items (id, user_id, status, attributes, created_at, updated_at) VALUES (%s,%s,'draft',NULL,%s,%s)` — `attributes` is `jsonb`, written `NULL` (never a JSON string). `updated_at` written explicitly (trigger only on UPDATE). | **Yes (jsonb / no `to_wire`)** |
| 2 | `get_item(item_id, user_id, include_deleted=False)` | `SELECT * FROM public.items WHERE id=%s AND user_id=%s`; if `deleted_at IS NOT NULL` and not `include_deleted` → `None`; then `SELECT kind, position, storage_path, byte_size, meta FROM public.assets WHERE item_id=%s ORDER BY kind, position`. `meta` read as `dict`. | **Yes (jsonb meta)** |
| 3 | `update_item_attributes(item_id, user_id, attributes)` | `UPDATE public.items SET attributes=%s WHERE id=%s AND user_id=%s` (attributes = `dict(attributes.model_dump(mode="json"))`; **omit `updated_at`** → trigger). Then `return self.get_item(...)`. | **Yes (jsonb; no `updated_at` write)** |
| 4 | `soft_delete_item(item_id, user_id)` | `UPDATE public.items SET status='deleted', deleted_at=%s WHERE id=%s AND user_id=%s` (**omit `updated_at`**). Returns bool. The PG CHECK `(deleted_at is null) = (status <> 'deleted')` is satisfied. | **Yes (no `updated_at` write; CHECK enforces agreement)** |
| 5 | `set_asset(item_id, user_id, kind, storage_path, position, byte_size, meta)` | `INSERT INTO public.assets (item_id, user_id, kind, position, storage_path, byte_size, meta, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (item_id, kind, position) DO UPDATE SET storage_path=excluded.storage_path, byte_size=excluded.byte_size, meta=excluded.meta` (meta=`dict(meta or {})`; **omit `updated_at`**). | **Yes (jsonb meta; upsert keeps original `created_at` on conflict — SQLite reset it; no behavioral contract depends on this)** |
| 6 | `create_job(item_id, user_id, kind)` | `INSERT INTO public.jobs (id, item_id, user_id, kind, state, attempts, progress, provider, created_at, updated_at) VALUES (%s,%s,%s,%s,'queued',0,0.0,'stub',%s,%s)`. **`provider='stub'` is written explicitly** (PG has no `NOT NULL`/`'stub'` default on `provider`, unlike SQLite). | **Yes (`provider='stub'` explicit; `updated_at` written on insert only)** |
| 7 | `get_job(job_id, user_id)` | `SELECT * FROM public.jobs WHERE id=%s AND user_id=%s`. `progress` (`numeric(4,3)`) read as `Decimal` → `float`; `cost_micros` (`bigint`) → `int`/`None`. | Minor (numeric→float, bigint→int) |
| 8 | `update_job(job)` | `UPDATE public.jobs SET state=%s, reason_code=%s, attempts=%s, progress=%s, error_detail=%s, claimed_by=%s, started_at=%s, finished_at=%s WHERE id=%s AND user_id=%s`. **Omit `updated_at`** (trigger). Returns the input `job` record unchanged (mirrors SQLite's returned `updated_at`). | **Yes (no `updated_at` write)** |
| 9 | `peek_next_queued_job()` *(worker, global)* | **THE behavioral advantage.** `SELECT j.* FROM public.jobs j WHERE j.state='queued' AND EXISTS (SELECT 1 FROM public.assets a WHERE a.item_id=j.item_id AND a.kind='source') ORDER BY j.created_at, j.id FOR UPDATE SKIP LOCKED LIMIT 1`. Must run inside `transaction()` (a real PG transaction). SQLite has no `FOR UPDATE` and relies on `begin immediate`. | **Yes (FOR UPDATE SKIP LOCKED — the production single-worker guarantee)** |
| 10 | `list_jobs_by_state(state)` *(worker, global)* | `SELECT * FROM public.jobs WHERE state=%s ORDER BY created_at, id`. | No |
| 11 | `has_finished_job(item_id)` | `SELECT 1 FROM public.jobs WHERE item_id=%s AND state='done' LIMIT 1`. | No |
| 12 | `append_usage_event(event: UsageEventRecord)` | `INSERT INTO public.usage_events (user_id, event, units, job_id, item_id, plan, marketplace, fields_touched, edited_field_count, metadata, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)`. `metadata` = `dict(metadata or {})`; `plan` = `plan.value if plan else None`; `created_at` = `event.created_at` (tz-aware). **No `field_names` column is written** (the protocol record has none; `usage_events.field_names` stays NULL). A duplicate `item_enqueued`/`job_refunded` raises `psycopg.errors.UniqueViolation` (partial unique indexes) — caught by `usage.refund_one`'s `except Exception`. | **Yes (jsonb metadata; stricter CHECKs — see §2 flags)** |
| 13 | `quota_units_used(user_id, start, end)` | `SELECT coalesce(sum(units),0)::bigint AS total FROM public.usage_events WHERE user_id=%s AND event IN ('item_enqueued','job_refunded') AND created_at >= %s AND created_at < %s`; `spent = -total`; `max(spent,0)`. | No |
| 14 | `count_events(user_id, event, start, end)` | `SELECT count(*)::bigint AS n FROM public.usage_events WHERE user_id=%s AND event=%s AND created_at >= %s AND created_at < %s`. | No |
| 15 | `has_event_for_job(job_id, event)` | `SELECT 1 FROM public.usage_events WHERE job_id=%s AND event=%s LIMIT 1`. | No |
| 16 | `get_plan(user_id)` | `SELECT plan FROM public.profiles WHERE user_id=%s`; no row → `PlanTier.FREE`. | No |
| 17 | `set_plan(user_id, plan)` | `INSERT INTO public.profiles (user_id, plan) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET plan=excluded.plan` (**omit `updated_at`** — trigger; no `created_at` → default). | **Yes (no `updated_at`; upsert)** |
| 18 | `get_paywall_variant(user_id)` | `SELECT paywall_variant FROM public.profiles WHERE user_id=%s`; null/none → `'a'`. | No |
| 19 | `assign_paywall_variant(user_id)` | `INSERT INTO public.profiles (user_id, plan, paywall_variant) VALUES (%s,'free',%s) ON CONFLICT (user_id) DO UPDATE SET paywall_variant=excluded.paywall_variant` (**omit `updated_at`**). Returns the deterministic variant (reuse `_paywall_variant_for`). | **Yes (upsert; no `updated_at`)** |
| 20 | `plan_limit(plan)` | `SELECT items_per_month FROM public.plan_limits WHERE plan=%s` → `int` or `None`. | No |
| 21 | `set_plan_limit(plan, items_per_month)` | `if items_per_month <= 0: raise ValueError` (parity guard, pre-CHECK). `INSERT INTO public.plan_limits (plan, items_per_month) VALUES (%s,%s) ON CONFLICT (plan) DO UPDATE SET items_per_month=excluded.items_per_month`. | **Yes (ValueError guard kept for parity; PG CHECK also rejects <=0)** |
| 22 | `get_idempotent_item(key, user_id, endpoint)` | `SELECT item_id, job_id FROM public.idempotency_keys WHERE user_id=%s AND endpoint=%s AND key=%s` → `(UUID, UUID)` or `None`. *(needs 0004)* | New table |
| 23 | `store_idempotent_item(key, user_id, endpoint, item_id, job_id)` | `INSERT INTO public.idempotency_keys (user_id, endpoint, key, item_id, job_id, created_at) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, endpoint, key) DO UPDATE SET item_id=excluded.item_id, job_id=excluded.job_id, created_at=excluded.created_at`. *(needs 0004)* | New table |
| 24 | `append_analytics_event(event: EventRecord)` → bool | `INSERT INTO public.events (<all 30 columns>) VALUES (<30 %s>) ON CONFLICT (event_id) DO NOTHING RETURNING event_id`. Return `cur.fetchone() is not None` (True=written, None=duplicate-skipped). `field_names` → `list(event.field_names) or None`; `client_ts`/`received_at` → tz-aware `datetime`; `item_id` → `UUID` or `None`. | **Yes (RETURNING + rowcount; text[]; timestamptz; no `created_at` column on PG `events`)** |
| 25 | `get_analytics_event(event_id)` | `SELECT * FROM public.events WHERE event_id=%s`. `field_names` (`text[]`) → `tuple`; timestamps → `datetime`; `item_id` → `UUID`/`None`. | **Yes (list→tuple; datetime; no user_id predicate — correct, it's the idempotency read)** |
| 26 | `transaction()` | Re-entrant context manager mirroring SQLite: outermost issues explicit `BEGIN` (with `autocommit=True`), nested increments `_depth`; on success outermost `COMMIT`, on exception outermost `ROLLBACK`. Required for `FOR UPDATE SKIP LOCKED` and for the create-item atomic block. | **Yes (explicit BEGIN/COMMIT vs SQLite `begin immediate`)** |

### 2.1 Flags the Engineer must not miss

- **`updated_at` is trigger-maintained in PG.** Never write it on UPDATE. Writes that do UPDATE
  (`update_item_attributes`, `soft_delete_item`, `update_job`, `set_plan`, `assign_paywall_variant`,
  `set_plan_limit`, `set_asset` DO UPDATE branch) must omit `updated_at`. `update_job` returns the
  caller's `updated_at` (mirrors SQLite) rather than the trigger's `now()`; if any test asserts
  exact `updated_at` equality after an update it may differ by sub-second — keep return-as-input.
- **`provider='stub'` on `create_job` is mandatory** in PG (no NOT NULL/`'stub'` default there).
- **`append_usage_event` never writes `usage_events.field_names`** — the protocol's
  `UsageEventRecord` has no such field; leave it NULL.
- **PG is stricter than SQLite via CHECKs.** The PG `usage_events.event` CHECK, the
  `plan`/`marketplace` CHECKs, and `usage_events_counters_only_on_attributes_edited` + the
  `edited_field_count <= fields_touched` CHECK are enforced only in Postgres. Every current caller
  in `usage.py` / `routers/items.py` respects them (the only counters ever set are on
  `attributes_edited`); a future caller passing a counter on a non-`attributes_edited` event will
  get `CheckViolation` where SQLite silently accepted. Tests cover today's callers.
- **Idempotency on the ledger** uses the partial unique indexes `usage_events_one_spend_per_job`
  / `usage_events_one_refund_per_job`; a duplicate raises `psycopg.errors.UniqueViolation`, caught
  by `usage.refund_one`. No change to `usage.py` needed.
- **Hard delete.** `PostgresRepository` must **never** issue `DELETE FROM items`. The API only soft-
  deletes. The no-FK design on `events.item_id` means a hard delete of an item (the admin/erasure
  path exercised by `test_events.py`'s `hard_delete_item`) leaves `events.item_id` intact — the
  desired append-only behaviour. `assets`/`jobs` cascade (FK `on delete cascade`) as in SQLite.

---

## 3. Test plan (run the existing suite against `PostgresRepository` on local PG)

**Goal:** the same `apps/api/tests/test_*.py` files pass against *both* repositories, plus one new
RLS isolation test that works under the local strategy.

### 3.1 Fixture / env switch (minimal change to `conftest.py`)

Add an env-driven repository factory so the existing `repository` / `app` fixtures build a
`PostgresRepository` when a Postgres DSN is supplied, and otherwise keep `SqliteRepository` (the
default, unchanged):

```python
# apps/api/tests/conftest.py (add)
import os
from apps.api.repository import PostgresRepository  # new sibling

def _postgres_dsn() -> str | None:
    dsn = os.environ.get("SNAPLIST_TEST_DB")
    return dsn if dsn and dsn.startswith(("postgres://", "postgresql://")) else None

@pytest.fixture
def repository():
    dsn = _postgres_dsn()
    if dsn is not None:
        store = PostgresRepository(dsn)          # ← RLS-bypassing trusted connection
    else:
        store = SqliteRepository(":memory:")
    try:
        yield store
    finally:
        store.close()
```

The `app` and `client` fixtures already depend on `repository`, so they follow automatically.

### 3.2 Run

```bash
# one-time local schema setup (§1.2), then:
SNAPLIST_TEST_DB=postgres://postgres@localhost:5432/snaplist \
  python -m pytest apps/api/tests -q
# parity: also keep the default run (SQLite) green:
python -m pytest apps/api/tests -q
```

### 3.3 SQLite-only introspection tests must be skipped under Postgres

Four tests in `test_events.py` assert SQLite-specific invariants and call `repository._conn.execute("pragma …")`
or expect `sqlite3.IntegrityError`. They cannot run against Postgres verbatim. Guard them:

- `test_foreign_keys_are_enabled_and_events_item_id_has_no_delete_action` — asserts SQLite `pragma foreign_keys` and `pragma foreign_key_list(events)`. **Skip** when Postgres (the PG equivalents are covered by `constraints_check.sql` + the migrations: FK enforcement is always-on and `events` has no delete action by construction of 0003).
- `test_the_sqlite_mirror_refuses_a_subscription_with_an_item` and
  `test_the_sqlite_mirror_refuses_an_item_event_without_an_item` — expect `sqlite3.IntegrityError`.
  **Skip** under Postgres (PG raises `psycopg.errors.CheckViolation`; the same refusal is proven
  by `constraints_check.sql`). The route-level `422`/`404` coverage of these rules is unaffected,
  so the behaviour is still tested.
- `test_the_sqlite_mirror_refuses_a_non_uuid_item_id` — expects `sqlite3.IntegrityError`. **Skip**
  under Postgres (the `uuid` column type enforces it; no test needed).

Add a module-level `IS_POSTGRES = _postgres_dsn() is not None` and
`@pytest.mark.skipif(IS_POSTGRES, reason="sqlite-mirror introspection is sqlite-only")` to those.
Everything else (`test_isolation.py`, the happy-path `test_each_of_the_seven_events_is_accepted`,
`test_hard_deleting_an_item_never_rewrites_an_event_row`, `test_usage.py`, `test_items.py`,
`test_jobs.py`, `test_worker.py`) passes **unchanged** because isolation is predicate-driven and
the row adapters tolerate `datetime`/`dict`.

### 3.4 New RLS isolation test — `apps/api/tests/test_rls_isolation.py`

This satisfies "an RLS isolation test that works under your local strategy." Because the trusted
backend bypasses RLS, the test opens a **raw `authenticated` connection** (mirroring
`rls_isolation_check.sql`) and proves the frozen `auth.uid()` policies, using the shim's
`request.jwt.claim.sub` GUC. It does **not** go through the repository (the repo is service_role).

```python
# apps/api/tests/test_rls_isolation.py (sketch)
import pytest, psycopg
from apps.api.tests.conftest import USER_A, USER_B

@pytest.fixture
def auth_conn():
    dsn = os.environ.get("SNAPLIST_TEST_DB")
    assert dsn, "SNAPLIST_TEST_DB required for RLS test"
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute("set role authenticated")           # the seller-facing role
    yield conn
    conn.close()

def test_seller_b_cannot_see_seller_a_item(auth_conn):
    auth_conn.execute("set request.jwt.claim.sub = %s", (str(USER_A),))
    auth_conn.execute("insert into public.items (user_id, status) values (%s,'draft')", (str(USER_A),))
    auth_conn.execute("set request.jwt.claim.sub = %s", (str(USER_B),))
    assert auth_conn.execute("select count(*) from public.items").fetchone()[0] == 0

def test_seller_cannot_write_the_ledger(auth_conn):
    auth_conn.execute("set request.jwt.claim.sub = %s", (str(USER_B),))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        auth_conn.execute(
            "insert into public.usage_events (user_id, event, units) values (%s,'item_enqueued',-1)",
            (str(USER_B),))
```

This is the local, pytest-runnable form of `rls_isolation_check.sql` (claims 1, 2, 4 there);
claim 3 (cannot insert a row owned by another) is covered by the `WITH CHECK` and the
`test_seller_b_cannot_see_seller_a_item` pattern. It runs only when `SNAPLIST_TEST_DB` is set.

---

## 4. Implementation order (smallest first) and exact files

> Order chosen so each step is independently verifiable. **No router code changes.**

1. **`infra/migrations/0004_idempotency_keys.sql`** *(new, REQUIRED — functional gap)*.
   Mirror the SQLite `idempotency_keys` table; add RLS (defense-in-depth) to match `items`:
   ```sql
   begin;
   create table if not exists public.idempotency_keys (
       user_id    uuid not null,
       endpoint   text not null,
       key        text not null,
       item_id    uuid not null,
       job_id     uuid not null,
       created_at timestamptz not null default now(),
       primary key (user_id, endpoint, key)
   );
   alter table public.idempotency_keys enable row level security;
   drop policy if exists idempotency_keys_owner on public.idempotency_keys;
   create policy idempotency_keys_owner on public.idempotency_keys
       for all to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id);
   commit;
   ```
   *Justification:* methods 22–23 are on the protocol and have no Postgres backing today.
   Apply locally (§1.2) and confirm `SELECT`/upsert works.

2. **`apps/api/repository.py`** — add `PostgresRepository` (implement all 26 protocol methods per
   §2), reusing the **existing frozen record dataclasses** (`ItemRecord`, `JobRecord`, `AssetRecord`,
   `UsageEventRecord`, `EventRecord`) and the existing helpers `utcnow`, `to_wire`, `from_wire`,
   `_paywall_variant_for`. Add PG-tolerant `_from_row` helpers (datetime/dict aware). Optionally
   extend `build_repository(database)` to return `PostgresRepository` when `database` is a
   `postgres://`/`postgresql://` DSN (non-breaking: the `:memory:`/file default is unchanged) so
   the §3.1 test switch and a future prod selection have a seam. **Keep `SqliteRepository` and the
   protocol untouched.**

3. **`apps/api/tests/conftest.py`** — add the env-driven `repository` fixture (§3.1) and the
   `IS_POSTGRES` flag. **`apps/api/tests/test_events.py`** — add `skipif(IS_POSTGRES)` to the four
   SQLite-only introspection tests (§3.3).

4. **`apps/api/tests/test_rls_isolation.py`** *(new)* — the RLS proof from pytest (§3.4).

5. **`Makefile`** *(optional)* — add a `test-postgres` target that sets `SNAPLIST_TEST_DB` and runs
   pytest; documents the one-time schema setup. Non-blocking.

---

## 5. Risks / open questions for the lead to confirm

1. **RLS strategy (§1).** I recommend **candidate 2** (trusted backend + explicit predicate +
   RLS bypassed). The decisive reason: the frozen policies forbid `authenticated` from writing
   `jobs`/`usage_events`/`events`, so candidate 3 cannot be the backend's sole mechanism. **Confirm
   candidate 2 is acceptable**, or tell me to build the `authenticated`+`service_role` blend
   (stated change to the "one connection" assumption).

2. **`idempotency_keys` gap (§0/§4).** Confirm a new `0004` migration is in scope (it is required
   to implement the protocol at all). I've justified it as a functional gap, not an RLS concern.

3. **`updated_at` parity.** PG uses the trigger; `update_job` returns the caller's `updated_at`
   (mirrors SQLite). If any downstream test asserts exact `updated_at` equality post-update, it may
   be off by sub-second. Recommend keeping return-as-input (no re-read) — flag if you want the
   trigger's value surfaced instead.

4. **PG stricter CHECKs (§2.1).** PG enforces `usage_events.event` vocabulary, `plan`/`marketplace`
   enums, and counter-on-`attributes_edited`-only, where SQLite silently accepted. Current callers
   comply; a future non-conforming caller will get `CheckViolation`. Worth a one-line note in the
   handoff, not a code change.

5. **psycopg v3 not yet installed in the default venv.** The `pg` optional group exists
   (`psycopg[binary]>=3.2,<4`). The Engineer must `uv sync --group pg`. Use the **sync** API
   (the repository and routers are synchronous; FastAPI runs sync deps in a threadpool) — not
   async.

6. **Connection model.** Single psycopg `Connection` + `threading.RLock` + re-entrant
   `transaction()` retained (mirrors SQLite). A small pool is the production upgrade but is out of
   scope; the design keeps the "one process, one connection" assumption as-is.

7. **UNVERIFIED (§1.5).** Real Supabase hosting, PG 15, PostgREST, hosted role *grants*, and
   asymmetric-JWT verification. The local shim + `rls_isolation_check.sql` establish that the
   frozen `auth.uid()`-based RLS reproduces the design; production `auth.uid()` reads the same GUC.

---

## 6. Supporting diagrams

- Class structure: `docs/postgres_repo_class.mermaid`
- Key sequences (create-item atomic block; claim-query with `FOR UPDATE SKIP LOCKED`):
  `docs/postgres_repo_sequence.mermaid`
