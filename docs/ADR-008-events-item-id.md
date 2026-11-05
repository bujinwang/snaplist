# ADR-008 — `item_id` on the analytics envelope, graded per event

- **Status:** accepted
- **Date:** 2026-11-05
- **Deciders:** lead (architecture), backend (E5-T6b), mobile consumer (E6-T1), product (metrics)
- **Supersedes:** nothing. **Amends:** `docs/CONTRACTS.md` §7, `packages/shared/src/analytics.ts`,
  `infra/migrations/0002_events.sql` (by adding `0003_events_item_id.sql`), `docs/METRICS.md` §3/§5.
- **Version:** `EVENTS_VERSION` 1.3.0 → **1.4.0**. `CONTRACT_VERSION` does not move.
- **Corrigendum: 2026-11-05, no version bump.** A QA audit found that the storage mechanism
  decided in §Decision item 5 (`ON DELETE SET NULL`) is mechanically impossible alongside the
  required-item CHECK. See the Corrigendum at the end of this file. The *decision* (graded
  `item_id`, 404 ownership, append-only history) is unchanged; only the FK was removed. Wire
  shapes and both version numbers are untouched.

## Context

`docs/METRICS.md` §3 has assumed since the funnel was specified that `upload_started`,
`prettify_approved`, `attributes_edited`, `export_clicked` and `export_delivered` all carry an
`item_id`. The column did not exist in `public.events` (0002) and the field did not exist in the
frozen `Envelope` (`analytics.ts`). Nothing crashed, because nothing reads the table yet — which
is precisely why the gap survived two tickets.

Three things the product gates on are arithmetic on that field:

1. **`tool_time_ms` per item** — `prettify_latency_ms + attrs_latency_ms + export_latency_ms`,
   the number `docs/PLAN.md` gates at p50 ≤ 35 s / p90 ≤ 60 s. Without an item id the only
   computable number is a sum of latencies from unrelated events: it would be computed,
   reported, and would pass or fail for reasons that have nothing to do with speed.
2. **`activation_items_exported_7d`** — "3 **items** exported within 7 days", deduped to one row
   per item. Without the id it degrades into "3 export events"; a seller who re-downloads one
   file three times would register as activated. `CONTRACTS.md` §7 already binds activation to
   `export_delivered` *and* to dedup-by-item; only half of that binding was implementable.
3. **The export-failure rate** — `export_clicked` rows with no matching `export_delivered`,
   which §7 calls a **required observable**. "Matching" needs a key the two rows share.
   `subject + marketplace` does not match anything; it correlates.

So E0-T5 would have shipped a gate that prints numbers and cannot compare them — a gate that
looks green because it is empty.

Secondary motivation: the client generated an `event_id` per event (ADR-007) but had no way to
say *which item* the event was about, so even a correct client could not have produced a
reportable funnel.

## Decision

1. **`item_id` goes on the envelope, and is graded per event.** Not "optional globally":
   * **required** — `upload_started`, `prettify_approved`, `attributes_edited`,
     `export_clicked`, `export_delivered`.
   * **optional** — `paywall_impression`.
   * **forbidden** — `subscription_started`.
2. **The grade is expressed in four places, deliberately**: zod per-event schemas
   (`item_id: z.string().uuid()` / inherited / `z.null().optional()`), pydantic per-event models
   (`UUID` / inherited `UUID | None` / `None`), two CHECK constraints on `public.events`, and two
   table CHECKs in the SQLite mirror. A client mistake is caught by the schemas; a write that
   bypasses the route (a future background job, a migration backfill) is caught by the database.
3. **Ownership is enforced on write.** When `item_id` is present the route calls
   `repository.get_item(item_id, user_id=caller.user_id, include_deleted=True)` and answers
   **404** for anything else. 404 rather than 403, for the reason `routers/items.py` gives: a 403
   confirms that somebody else's item exists.
4. **`EVENTS_VERSION` 1.4.0.** A field, not an event. `CONTRACT_VERSION` does not move — the
   events vocabulary is versioned separately on purpose (§7), and mobile ships on its own
   schedule.
5. **New migration `0003_events_item_id.sql`**, forward-only: `add column item_id uuid`
   (**no foreign key** — see the Corrigendum), the two CHECKs, and
   `(item_id, event, received_at desc)`.

## Why the grade, and not "optional everywhere"

An optional field on a frozen envelope is how a metric dies quietly. Two possible worlds:

* **All-optional.** Every emitter has to remember. The emitter that forgets still gets a 202, so
  nothing is loud: the row lands with a NULL join key, the per-item report silently drops it, and
  the only symptom is a percentage that drifts for no visible reason. This is the same failure
  shape that let `docs/METRICS.md` assume a field existed for two quarters.
* **Graded.** An emitter that forgets `item_id` on `export_delivered` gets a 422 at emit time,
  in development, naming the field. The cost of the grade is that six of the seven events now
  have one more required key; the benefit is that the three gates above are computable by
  construction rather than by discipline.

`paywall_impression` is the one exception and the exception is factual, not a convenience: two of
its three triggers (`on_model`, `export_limit`) always have an item, but a paywall opened from
the settings screen has none, and no paywall metric segments by item. Requiring it would 422 a
legal impression; forbidding it would refuse the two triggers that do have one; either way the
emitter under pressure would invent an id, and an invented join key is worse than a missing one
(it is silently wrong rather than visibly absent).

`subscription_started` is forbidden because the event is written by the API from a Stripe
webhook, where no item is in scope. A subscription is not about one garment. A plausible id
attached here would attribute revenue to whichever item happened to be on the seller's screen —
the kind of error that looks like a good month.

## Consequences

- **Mobile (E6-T1) must pass the item id** on the five funnel events, and must not pass it on
  `subscription_started`. `analytics.ts` is the type-level source of truth; a client that
  compiles against 1.4.0 cannot get the five wrong.
- **`paywall_impression` from the settings screen** stays legal without an item. Nothing to do.
- **A retry of an event for an item that was hard-deleted is a 404.** Soft deletes are fine
  (`include_deleted=True`), which is the normal path: the app deletes by setting `deleted_at`.
  A hard delete (admin action, erasure) means the client's offline queue can be refused; the
  client should drop the event rather than retry forever. Recorded in
  `docs/HANDOFF-engineer.md`, not solved here. **Existing** event rows are a different matter:
  they keep their original `item_id` after the item disappears (Corrigendum).
- **No foreign key on `item_id`; `ON DELETE SET NULL` was withdrawn.** Events are append-only
  history, and deleting an item must not delete or rewrite the evidence that a funnel step
  happened, or historical activation shrinks / loses its join key every time somebody tidies
  up. An orphaned timestamp with its original id is still a datapoint; a deleted row is not,
  and a nulled id is only half of one. The withdrawn mechanism and the replacement are in the
  Corrigendum below.
- **Ownership is a point-in-time check** — "this item is yours now", not "it was yours when the
  event happened". Proving the latter needs the item's ownership at `client_ts`, which the schema
  does not record. The direction of the residual risk is favourable: an attacker can only attach
  events to items they own.
- **Any FK to `items` was rejected, composite included.** `references items (id, user_id)`
  would make ownership a database fact, but `items` has no unique constraint on that pair, and
  adding one changes a primary key in a migration whose subject is a nullable analytics column.
  More importantly, *every* ON DELETE action is wrong here (Corrigendum), so the composite-FK
  discussion is moot. Existence and ownership are proven in `bind_item`, which is strictly
  stronger than the single-column FK ever was.
- **`docs/METRICS.md` was corrected in the same change**, and with a header line saying the wire
  names come from `CONTRACTS.md` §7: the document had drifted on `price_cents` vs `amount_cents`,
  on `paywall_type` (a field that never existed; the contract carries `trigger` and
  `plan_offered`), and on a hashed `stripe_customer_id` that the PII ban forbids outright.

## Alternatives rejected

- **A separate `event_items` join table** — a second table for one nullable uuid, and every
  query would need the join to answer a question whose answer is already on the row.
- **`item_id` as free text, validated nowhere** — the id is a uuid and the ownership check is the
  only reason the field is safe to join on. Text would allow `item_id = "tee"` and a report that
  quietly groups unrelated items.
- **Deriving the item from the job** (`job_id` → `jobs.item_id`) — needs a `job_id` on the
  envelope, which the events contract does not have, and `attributes_edited` / `export_clicked`
  happen in no job at all. It would also make every report depend on a row that is mutable.
- **`ON DELETE CASCADE`** — see Consequences: it deletes the evidence.
- **`ON DELETE SET NULL` (originally chosen, withdrawn by the Corrigendum)** — it is an
  implicit `UPDATE events SET item_id = NULL`, and a CHECK is re-evaluated on UPDATE. Against
  the required-item CHECK it makes the DELETE fail for the five item events; on
  `paywall_impression` it silently rewrites an append-only row. No column can be both
  "required on these events" and "auto-nulled by the database" at the same time.
- **`ON DELETE RESTRICT` / `NO ACTION`** — refuses the delete, so a mutable `items` row is held
  hostage by an analytics table (a GDPR erasure would fail), produces an unwrapped
  `IntegrityError`, and in SQLite is per-connection and defaults OFF, so an admin script would
  bypass it entirely. It also changes, rather than implements, "an orphaned timestamp is still
  a datapoint".
- **Relaxing the forward CHECK to permit NULL** — that is the exact hole this ADR exists to
  close: `tool_time_ms` per item, `activation_items_exported_7d` deduped per item, and the
  export-failure rate all become uncomputable. A CHECK cannot distinguish "the emitter
  forgot the id" from "the FK nulled it".
- **Making `item_id` required on `subscription_started` "for symmetry"** — it would force the
  webhook handler to pick an item to satisfy a validator, i.e. to fabricate data so a schema
  looks tidy.

## Corrigendum — 2026-11-05 (QA audit; no version bump)

### What was wrong

The first implementation of `0003_events_item_id.sql` declared
`item_id uuid references public.items (id) on delete set null`, and the SQLite mirror in
`apps/api/repository.py` copied it. The same file also declared (and still declares) the
forward CHECK `events_item_id_required_for_item_events`, which requires `item_id is not null`
on five events. These two constraints are **mutually exclusive**: `ON DELETE SET NULL` is an
implicit `UPDATE`, and a CHECK must hold after an UPDATE just as after an INSERT.

QA measured it against a real `SqliteRepository(":memory:")` with `pragma foreign_keys = on`,
by inserting one legal event per event name and then issuing `delete from items`:

| event | `WITH FK on delete set null` | `WITHOUT FK` (fixed) |
|---|---|---|
| `upload_started` | DELETE **refused**, CHECK violation; row keeps `item_id` | DELETE OK; row keeps `item_id` |
| `prettify_approved` | DELETE **refused**, CHECK violation; row keeps `item_id` | DELETE OK; row keeps `item_id` |
| `attributes_edited` | DELETE **refused**, CHECK violation; row keeps `item_id` | DELETE OK; row keeps `item_id` |
| `export_clicked` | DELETE **refused**, CHECK violation; row keeps `item_id` | DELETE OK; row keeps `item_id` |
| `export_delivered` | DELETE **refused**, CHECK violation; row keeps `item_id` | DELETE OK; row keeps `item_id` |
| `paywall_impression` | DELETE OK, row **silently rewritten to `item_id = NULL`** | DELETE OK; row keeps `item_id` |
| `subscription_started` | DELETE OK, `item_id` stays NULL (no item in scope) | DELETE OK, `item_id` stays NULL |

So the FK never did what it said: on the five events that matter it degraded to `NO ACTION`
(which this ADR's own prose called wrong), and on the one event where it did fire it destroyed
an append-only row. `test_migration_0003_adds_item_id_with_a_set_null_foreign_key` asserted only
the **SQL text**, so 569 green tests proved nothing about the behaviour.

### Decision (replaces point 5's FK clause, and the Consequences bullet about it)

**Remove the FK entirely.** `item_id` becomes a bare `uuid` column on Postgres (the type is
the shape constraint) and a `text` column with a length + alphabet CHECK on SQLite. Existence
and ownership remain enforced by `POST /v1/events::bind_item` (404 for a foreign id). A hard
delete of an item now succeeds and leaves every event row **unchanged**, with its original
`item_id` — the orphan this ADR wanted. If a policy requires purging those rows too, it is an
explicit, auditable `delete from events where item_id = ?`, not a silent constraint side
effect.

`0003` was corrected **in place**: it was untracked, never committed, and the pre-fix version
was never applied to any database, so no environment has the bad FK and no 0004 is needed. The
corrected file has since been run twice on a local vanilla PostgreSQL 14.18 behind
`infra/migrations/verify/local_supabase_shim.sql` (see `infra/migrations/README.md`); the
Supabase-hosted environment, PG15, RLS role policies and PostgREST remain UNVERIFIED. `apps/api/repository.py` handles a *pre-0003* SQLite file by adding a plain
`item_id text` column, and refuses a *pre-fix* file that already carries the bad FK with a
loud `RuntimeError` (delete the dev DB; SQLite cannot drop a constraint with `ALTER TABLE`).

### Cost of being wrong, now

Static referential integrity is gone: `item_id` may be a dangling uuid at rest, by design.
Reports (E0-T5) must tolerate orphans, and any writer that bypasses the API must validate the
id itself — the shape CHECK proves it is a uuid, not that the item exists or belongs to the
subject. An upgraded pre-0003 SQLite file is also looser than a fresh one (SQLite cannot add a
CHECK with `ALTER TABLE`); that is a dev-only residual, recorded in
`docs/HANDOFF-engineer.md`.

### What changed on the wire: nothing

No event name, envelope field, nullability or grade changed. `EVENTS_VERSION` stays **1.4.0**;
`CONTRACT_VERSION` does not move — it is unchanged by this ADR, as in ADR-007.
`docs/CONTRACTS.md` §7 and `packages/shared/src/analytics.ts` are untouched. A client built
for 1.4.0 needs no change.
The fix is proven by **behaviour** tests in `apps/api/tests/test_events.py`: a real hard delete
followed by whole-row `EventRecord` equality, `pragma foreign_key_list(events)` introspection
showing no `from == 'item_id'` FK, and a direct `update events set item_id = null` that must
still raise `IntegrityError` — the last one is the anchor that fails if anybody tries the
rejected "relax the forward CHECK" alternative.
