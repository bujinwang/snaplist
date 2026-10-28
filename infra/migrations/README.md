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
