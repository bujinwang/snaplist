-- =====================================================================================
-- SnapList — 0003_events_item_id.sql
-- Owner: backend role (E5-T6b). Target: Supabase (Postgres 15) / Postgres 14+.
--
-- WHY THIS FILE EXISTS
--   `docs/METRICS.md` assumed, from the day the funnel was specified, that
--   `upload_started`, `prettify_approved`, `attributes_edited`, `export_clicked` and
--   `export_delivered` all carry an `item_id`. `public.events` (0002_events.sql) did not have
--   the column. Nothing failed, because nothing could read it — but three things the product
--   gates on are arithmetic on that field:
--
--     * `tool_time_ms` = `prettify_latency_ms + attrs_latency_ms + export_latency_ms`
--       **for one item** (docs/PLAN.md's p50 <= 35 s / p90 <= 60 s gate). Without an item id
--       the only computable number is a sum of latencies from unrelated events, which measures
--       nothing and would have passed or failed for reasons having nothing to do with speed.
--     * `activation_items_exported_7d` — "3 **items** exported in 7 days", deduped on one row
--       per item. Without the id it degrades into "3 export events", and a seller who
--       re-downloads one file three times would count as activated.
--     * the export-failure rate (`export_clicked` with no matching `export_delivered`), which
--       docs/CONTRACTS.md §7 calls a **required observable**. Matching the two rows requires a
--       key they share; `subject` + `marketplace` matches nothing, it only correlates.
--
--   So the column is not a nice-to-have login detail: it is the join key the funnel is, in
--   practice, indexed on. ADR-008 records the decision and the grading.
--
-- THE RULE, IN ONE PLACE (and again in the other three)
--   `item_id` is **graded per event**, never globally optional:
--     * required  — upload_started, prettify_approved, attributes_edited, export_clicked,
--                   export_delivered
--     * optional  — paywall_impression (trigger=on_model/export_limit always have an item; a
--                   paywall raised from the settings screen does not, and no paywall metric
--                   segments by item)
--     * forbidden — subscription_started (written from a Stripe webhook; no item is in scope)
--
--   Postgres is the second lock, not the first: `apps/api/routers/events.py` enforces the same
--   matrix in pydantic (`UUID` / optional `UUID` / `None`) and
--   `packages/shared/src/analytics.ts` in zod. The duplication is the design — a client-side
--   mistake is caught by the schema, a server-side one by the CHECK, and a bug in both by the
--   test that calls `append_analytics_event()` directly.
--
-- NO FOREIGN KEY ON `item_id` — BY DESIGN, AND FOR A MECHANICAL REASON
--   The first version of this file declared
--     `item_id uuid references public.items (id) on delete set null`.
--   That is **mutually exclusive** with the forward CHECK below, and QA proved it against a
--   real SQLite table: `ON DELETE SET NULL` is an implicit `UPDATE events SET item_id = NULL`,
--   and a CHECK is re-evaluated on UPDATE, not only on INSERT. So deleting an item produced
--   exactly the wrong answer for every event:
--     * the five item events (which the forward CHECK requires to be NOT NULL) → the implicit
--       UPDATE violates the CHECK → **the whole delete is refused**. The FK silently degrades
--       to `NO ACTION`, which this file's own earlier prose called wrong.
--     * `paywall_impression` (the one event allowed a NULL id) → the delete succeeds and the
--       FK **silently rewrites an append-only history row**, destroying its join key.
--     * `subscription_started` carries no item, so it is unaffected.
--   There is no FK action that is correct for both halves. `CASCADE` deletes the evidence;
--   `RESTRICT` / `NO ACTION` refuses the delete (and in SQLite is per-connection, defaulting
--   OFF, so an admin script would bypass it); `SET NULL` rewrites history. **The only shape
--   that preserves "an orphaned timestamp is still a datapoint" is no FK at all.**
--
--   What the FK used to guarantee — "the item existed when the row was written" — is already
--   guaranteed more strongly by `apps/api/routers/events.py::bind_item`, which additionally
--   proves the item belongs to the caller (404 otherwise) and accepts soft-deleted rows
--   (`include_deleted=True`). The cost of dropping it is the loss of *static* referential
--   integrity for writes that bypass the API: `item_id` may become a dangling uuid at rest.
--   That is intended, and ADR-008's Corrigendum records what a report has to tolerate.
--   A GDPR erasure that must remove an item AND its events is now an explicit, auditable
--   `delete from public.events where item_id = ?` — not a silent constraint side effect.
--
--   The `uuid` type is the shape constraint on this side: no `text`, no free-form value can
--   land here. SQLite has no uuid type, so its mirror spells the equivalent out as a GLOB +
--   length CHECK in `apps/api/repository.py`.
--
-- HONESTY NOTE
--   **0003 was corrected before it was ever applied to a Supabase project.** It was an
--   untracked, never-committed file, and no environment ever ran the `ON DELETE SET NULL`
--   version: no deployed row needs repair and no 0004 is needed.
--
--   The old "never executed against a real Postgres" line is **withdrawn**. `0001` → `0002`
--   → `0003` have been applied clean, with `0003` run **twice** (`-v ON_ERROR_STOP=1`),
--   against a local vanilla **PostgreSQL 14.18** behind
--   `infra/migrations/verify/local_supabase_shim.sql`. Both grade CHECKs, the per-item
--   index and the hard-delete behaviour (a real `delete from public.items` leaves the
--   event row's `item_id` intact) were measured on that database.
--   `apps/api/tests/test_events.py` still pins the text and exercises the SQLite mirror
--   in `apps/api/repository.py`, which is the table this build actually writes to.
--
--   Still **UNVERIFIED**: the Supabase-hosted environment, Postgres 15, the RLS role
--   policies on a real project (`anon` / `authenticated` / `service_role` grants) and
--   PostgREST. A local vanilla server plus a shim is not a Supabase project.
-- =====================================================================================

begin;

-- -------------------------------------------------------------------------------------
-- The column. `add column if not exists` so running this file twice is a no-op, per the
-- migration-hygiene rule in infra/migrations/README.md ("every migration runs clean twice").
--
-- Deliberately **no `references` clause**. See "NO FOREIGN KEY ON `item_id`" in the header:
-- every ON DELETE action either rewrites an append-only row, deletes the evidence, or refuses
-- the delete. Existence **and** ownership are checked in the API (404 for a foreign id), not
-- by the database. The `uuid` type alone constrains the shape; there is no FK to add back.
-- -------------------------------------------------------------------------------------
alter table public.events
    add column if not exists item_id uuid;

comment on column public.events.item_id is
    'The item this event is about (ADR-008). Required for the five per-item events, optional on paywall_impression, forbidden on subscription_started — enforced by two CHECKs below, by the pydantic models in apps/api/routers/events.py and by packages/shared/src/analytics.ts. NO foreign key by design: an ON DELETE action would rewrite append-only history, so event rows deliberately outlive their items and keep the original id. Ownership (it must belong to `subject`) is checked by POST /v1/events, which answers 404 for a foreign id.';

-- -------------------------------------------------------------------------------------
-- The grade, both directions. A forward CHECK alone would let a Stripe webhook row carry an
-- item id; a reverse CHECK alone would let an `export_delivered` row arrive with none, which
-- is the exact hole this migration exists to close.
--
-- `alter table ... add constraint` has no `if not exists` in Postgres, so each one is guarded
-- by a `pg_constraint` lookup. Without the guard, running this file twice (the hygiene rule
-- in infra/migrations/README.md) dies with `duplicate object`. The constraint bodies below
-- are verbatim the contract's — the wrapper changes nothing about what is enforced.
-- -------------------------------------------------------------------------------------
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'events_item_id_required_for_item_events'
          and conrelid = 'public.events'::regclass
    ) then
        alter table public.events
            add constraint events_item_id_required_for_item_events
                check (item_id is not null
                       or event not in ('upload_started', 'prettify_approved', 'attributes_edited',
                                        'export_clicked', 'export_delivered'));
    end if;
end $$;

-- `subscription_started` must NOT carry one: revenue is not about a garment, and a
-- plausible id here would attribute a subscription to whichever item was on screen.
do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'events_item_id_forbidden_on_subscription_started'
          and conrelid = 'public.events'::regclass
    ) then
        alter table public.events
            add constraint events_item_id_forbidden_on_subscription_started
                check (item_id is null or event <> 'subscription_started');
    end if;
end $$;

-- -------------------------------------------------------------------------------------
-- The third index on this table, and the only one that answers a per-item question.
-- The two existing ones cover "one event, newest first" and "one seller's timeline"; this one
-- covers "one item's timeline, filtered by step", which is what `activation_items_exported_7d`
-- (one row per item, last `export_delivered`) and the tool-time percentiles both run.
--
-- `received_at desc` and not `client_ts`: the client's clock is stored but never ordered by
-- (see the 0002 header). A partial index (`where item_id is not null`) would be smaller, but
-- the NULL rows are one event out of seven and the reader would have to remember the
-- predicate; not worth it.
-- -------------------------------------------------------------------------------------
create index if not exists events_item_event_received_idx
    on public.events (item_id, event, received_at desc);

commit;
