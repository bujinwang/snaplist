# infra/migrations

Owned by the **backend** role (E5-T1). The first migration is `0001_init.sql` and must create,
with RLS on every table:

`profiles`, `items`, `assets`, `jobs`, `usage_events`, `subscriptions`, `model_refs`,
`api_keys`

Empty on purpose: this run only scaffolds. The constraints below are already decided elsewhere
and are not open for re-litigation when the migration is written.

## From `docs/CONTRACTS.md`

- `jobs.state` — CHECK over exactly `queued|running|done|failed`.
- `jobs.reason_code` — CHECK over the five-value enum, and NULL unless `state='failed'`;
  NOT NULL when `state='failed'` (both directions, or the invariant is decorative).
- `jobs.attempts integer not null default 0` with CHECK `attempts <= 3`.
- indexes: `(user_id, created_at desc)` on `items`; `(state, created_at)` on `jobs` for
  `FOR UPDATE SKIP LOCKED` polling.
- `updated_at` maintained by a trigger, not by application code.
- quota is decremented atomically at enqueue and refunded on terminal failure; the ledger is
  `usage_events`, never a mutable counter alone.

## Cost auditability — R8 (product request, accepted)

The two ceilings in BRIEF ($0.03/item, $0.10/render) are unauditable if a job row does not
remember what it spent. Add to `jobs`:

```sql
provider       text          -- 'fashn' | 'catvton' | 'sam2_hosted' | 'birefnet' | <vlm id> | 'stub'
model_version  text          -- adapter + model id, echoed into ItemAttributes.model_version
cost_micros    bigint        -- integer micro-USD; never float money
```

`cost_micros` is nullable and is only written on success paths that actually spent money. A NULL
means *unmeasured*, not zero — the same rule the eval harness follows.

## Anonymous sessions — ADR-003

```sql
-- profiles
is_anonymous    boolean not null default true,
merged_into     uuid null references profiles(user_id),
paywall_variant text not null default 'a' check (paywall_variant in ('a','b','c')),
check (merged_into is null or merged_into <> user_id),
```

**Why the FK points at `profiles(user_id)` and not `profiles(id)`.** `profiles` has no `id`
column: its primary key *is* `user_id`. That is a deliberate schema-wide decision, not an
oversight — it lets every table in `0001_init.sql` be filtered by the same RLS predicate,
`auth.uid() = user_id`, with no exceptions. One exception to remember is one policy away from a
leak, so the identity column is named the same everywhere. The FK references the PK it actually
has; `references profiles(id)` would not compile.

**`check (merged_into <> user_id)`** (added during implementation, not in the original brief):
a row that merges into itself makes the merge chain unreadable, and any traversal of it loops
forever. Cheap to forbid, expensive to debug.

`is_anonymous` flips to false when Supabase converts the user in place (`updateUser`); the
`user_id` does not change, so no row ownership moves. A real merge (anonymous row into an
existing account) sets `merged_into` and re-points `items.user_id`; it must be explicit and
logged, never silent.

## Billing headroom — ADR-005 §3

```sql
-- subscriptions
overage_enabled          boolean not null default false,
overage_unit_price_cents integer null,
```

v1 keeps `overage_enabled = false` and hard-blocks with 402 `quota_exceeded`. These columns exist
so the first 400-item boutique does not require a migration under pressure. **Do not implement
overage arithmetic** — that is a pricing product, not a column.

## Migration hygiene

- One file per change, `NNNN_description.sql`, forward-only. No down-migrations in v1; a bad
  migration is fixed by the next one. **That rule applies to published migrations.** `0003` was
  corrected *in place, before it was ever applied to a Supabase project*: it was an untracked,
  uncommitted file, so no hosted environment ever ran the withdrawn FK. No 0004 is needed and no
  deployed row needs repair (ADR-008 Corrigendum). The corrected file was later run twice on a
  local vanilla PostgreSQL 14.18 — see Verification status below.
- Every migration runs clean twice in a row (`supabase db reset` twice) before it is merged.
- RLS policy per table with the seller's own rows as the only readable set, plus an explicit test
  with two JWTs proving user B cannot read user A's item.

## `0002_events.sql` — the analytics funnel table (E5-T6)

Adds `public.events`: one row per event the mobile client reports through `POST /v1/events`.
It is **not** an extension of `usage_events` — that table is the quota ledger (money, signed
`units`, one spend/refund per job) and this one is the product funnel (no `units`, idempotent on
a client-generated id). Two vocabularies, two tables, two sets of repository methods
(`append_usage_event` vs `append_analytics_event`). Merging them would let a funnel row move a
quota counter, which is the one thing `0001_init.sql` is built to prevent.

What the migration pins, and why each pin exists:

- `event` — CHECK over the seven frozen names in `docs/CONTRACTS.md` §7 / `EVENTS_VERSION
  1.2.0`. An eighth name is an ADR plus a version bump, and the database is the second place
  that says so.
- `event_id uuid primary key` — **client-generated**, and the idempotency key. Not
  `default gen_random_uuid()`: a server-generated id makes every retry a new row, and a phone
  in a bad signal area would look like three paywall impressions.
- One narrow, nullable column per payload field, each with its own CHECK (enum / non-negative /
  length). **No `jsonb`, no `metadata`, no free text.** There is nowhere for PII to land, and
  adding a landing spot is a diff a reviewer can see.
- Forward CHECKs (`event <> 'export_clicked' or (marketplace is not null and …)`) make the
  required fields required; reverse CHECKs (`marketplace is null or event in ('export_clicked',
  'export_delivered')`) make every payload column exist on **only** its own event. Without the
  second half a shared column becomes a general-purpose pocket.
- `field_names text[]` — a **name** list: no NULL elements, ≤ 20, every element matches
  `^[a-z][a-z0-9_]*$` (the zod regex, verbatim). This is what makes `color:red` impossible,
  i.e. it is the implementation of "a value glued onto a name fails validation".
- `payload_sha256` — canonical payload digest, so "same id, same content" (202 duplicate) can
  be told apart from "same id, different content" (409, i.e. the client reused an id).
- RLS enabled with a single policy for `service_role`. `anon` and `authenticated` get nothing:
  clients write through the API and never read the funnel.

**Verification status.** `0001_init.sql` → `0002_events.sql` → `0003_events_item_id.sql` have
been applied clean (with `0003` run **twice**, `-v ON_ERROR_STOP=1`) to a local vanilla
**PostgreSQL 14.18** behind `infra/migrations/verify/local_supabase_shim.sql`. On that
database the seven-name CHECK, the column whitelist, both `item_id` grade CHECKs, the
per-item index and the hard-delete behaviour were measured; `apps/api/tests/test_events.py`
still asserts the text and exercises the SQLite mirror in `apps/api/repository.py`.
Still **UNVERIFIED**: the Supabase-hosted environment, Postgres 15, the RLS role policies
on a real project and PostgREST — the shim recreates just enough of `auth.*` and the roles
to run the DDL, it is not a Supabase project.

## `0003_events_item_id.sql` — the item a funnel event is about (E5-T6b)

Adds `public.events.item_id uuid` — **a bare column, with no foreign key** — plus two CHECKs
and one index. It exists because `docs/METRICS.md` assumed for two quarters that five of the
seven events carry an item id while the table had no such column: three numbers the product
gates on — `tool_time_ms` per item, `activation_items_exported_7d` deduped per item, and the
`export_clicked` − `export_delivered` failure gap — are arithmetic on that field, and would have
been reported as counts of *events*, which look similar and mean something else. ADR-008.

- **Graded per event, never globally optional**: required on `upload_started`,
  `prettify_approved`, `attributes_edited`, `export_clicked`, `export_delivered`; optional on
  `paywall_impression` (a settings-screen paywall has no item); **forbidden** on
  `subscription_started` (written from a Stripe webhook, where no item is in scope). Both
  directions are CHECKs, because a forward CHECK alone lets a webhook row carry an item and a
  reverse one alone lets an `export_delivered` arrive without one.
- **No FK on `item_id`, and no ON DELETE action of any kind.** That is a correction, not an
  omission (ADR-008 Corrigendum, 2026-11-05). The first version of this file used
  `references public.items (id) on delete set null`. `SET NULL` is an implicit `UPDATE`, and a
  CHECK is re-evaluated on UPDATE: the five item events, which the forward CHECK requires to be
  NOT NULL, then make the whole `DELETE` fail (the FK degrades to `NO ACTION`), while
  `paywall_impression`, which permits a NULL id, gets its append-only row **silently rewritten**.
  QA measured both against a real SQLite table. `CASCADE` deletes the evidence; `RESTRICT` /
  `NO ACTION` refuses the delete and in SQLite is per-connection (defaults OFF). With no FK a
  hard delete succeeds and the event keeps its original `item_id`: an orphaned timestamp with
  its join key intact is still a datapoint. Purging events for an erasure is then an explicit
  `delete from events where item_id = ?`, not a hidden side effect.
- **Ownership is not a database constraint, and neither is existence.** The `uuid` type (a
  length + GLOB CHECK in the SQLite mirror) proves the *shape*;
  `apps/api/routers/events.py::bind_item` proves existence **and** ownership at write time (404
  for a foreign id, soft-deleted items accepted). That is strictly stronger than the old
  single-column FK. The cost is the loss of static referential integrity: reports must tolerate
  dangling `item_id`s, and a writer that bypasses the API must validate the id itself. A
  composite FK to `(id, user_id)` would need a new unique constraint on `items`, and is moot
  anyway — every ON DELETE action is wrong here.
- **`(item_id, event, received_at desc)`** — the only per-item read pattern, and `received_at`
  not `client_ts`, because the client's clock is stored but never ordered by.

**Verification status.** Same as 0002: `0003` **has been executed** — twice, with
`-v ON_ERROR_STOP=1` — against a local vanilla **PostgreSQL 14.18** behind the shim, so
"never been executed" no longer applies. Both grade CHECKs, the per-item index and the
hard-delete behaviour were measured on that database; the Supabase-hosted environment,
PG15, RLS role policies and PostgREST remain **UNVERIFIED**. What is additionally proven
is the SQLite mirror, which is the table this build actually writes to:
`apps/api/tests/test_events.py` now asserts the *behaviour* — a real `delete from items`
must leave every `EventRecord` byte-for-byte equal (whole frozen-dataclass equality across all
seven events), a direct `update events set item_id = null` must raise `IntegrityError`, and
`pragma foreign_key_list(events)` must show no `from == 'item_id'` FK.
`pragma foreign_keys == 1` is asserted too, plus a positive control (an `assets` row must
cascade away on hard delete) so the pragma cannot be decorative. The text assertions that
remain are a cheap guard against re-adding the string, nothing more.

**SQLite is per-connection.** `pragma foreign_keys = on` in `SqliteRepository.__init__` is the
only place it is enabled; an external `sqlite3` connection to a dev file has FK enforcement
OFF by default. That is another reason the correct behaviour must not depend on an FK action.

**Old file databases.** A genuinely *pre-0003* `SNAPLIST_DB` gets `item_id text` added by
`SqliteRepository._migrate_event_item_column()` (no FK), but SQLite cannot add a CHECK with
`ALTER TABLE`, so that database still lacks the uuid shape CHECK and the two grade CHECKs — it
enforces the grade only in the router and is **looser than a fresh database**. A file built by
the *pre-fix* version of 0003, which already carries the bad `ON DELETE SET NULL` FK, is
**refused with a loud `RuntimeError`** telling the operator to delete the dev DB and let
`make dev` rebuild it; SQLite cannot drop a constraint with `ALTER TABLE`, and silently
keeping it would contradict the migration that ships. No production database ever had that
shape — 0003 was corrected before it was applied.
