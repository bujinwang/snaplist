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
  migration is fixed by the next one.
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

**Verification status.** No Postgres, docker or supabase CLI exists in this environment, so
this file has been checked at the **text** level only (`apps/api/tests/test_events.py` asserts
the seven names, the RLS statement and the column whitelist) and the behaviour of the table is
exercised through its SQLite mirror in `apps/api/repository.py`. "Applies cleanly on Postgres"
is UNVERIFIED, exactly as the honesty note in `0001_init.sql` says.
