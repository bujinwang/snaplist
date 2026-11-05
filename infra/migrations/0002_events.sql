-- =====================================================================================
-- SnapList — 0002_events.sql
-- Owner: backend role (E5-T6 in docs/PLAN.md). Target: Supabase (Postgres 15) / Postgres 14+.
--
-- WHAT THIS FILE IS
--   The analytics ingest table behind `POST /v1/events`. One row per event the mobile
--   client reports. The vocabulary is **frozen at seven names** in docs/CONTRACTS.md §7 and
--   typed in packages/shared/src/analytics.ts; this migration is where that frozen list is
--   enforced a second time, so a bug in the API cannot widen it.
--
-- WHAT THIS FILE IS NOT
--   It is not a dashboard, it is not a copy of `usage_events`, and it is not a place to park
--   "extra context". `usage_events` is the *quota ledger* (money); this is the *product
--   funnel*. They share no rows and no vocabulary, and `apps/api/usage.py` reads only the
--   ledger. `apps/api/repository.py` keeps the two method families apart by name
--   (`append_usage_event` vs `append_analytics_event`) for the same reason.
--
-- THE ONE DESIGN RULE THAT MATTERS
--   **No column in this table can hold free text.** docs/CONTRACTS.md §7 bans PII (no email,
--   no attribute values, no photo content, no advertising id), and the way that ban survives
--   six months of "just one more field" is structural, not cultural: every `text` column below
--   is either a closed enum pinned by a CHECK, or a strict pattern, or a hex digest. There is
--   no `jsonb`, no `metadata`, no `note`, no `payload`. Adding one is a schema change a
--   reviewer can see in a diff, which is the whole point — the alternative (a jsonb catch-all)
--   would let PII in with no diff that looks like a PII change.
--
--   `field_names text[]` is the single list-valued column, and it is a **name** list:
--     * no NULL elements (an unnamed field is not a field),
--     * at most 20 names (a garment has ~16 contract attributes; 20 is slack, not a bucket),
--     * each element matches `^[a-z][a-z0-9_]*$`, which is exactly the zod regex in
--       `packages/shared/src/analytics.ts`. That regex is what makes "a value glued onto a
--       name fails validation" true: `color:red` and `Size_Label:XL` cannot be stored.
--   The per-element test needs `unnest`, and a CHECK expression may not contain a subquery,
--   so it lives in `public.events_field_names_are_name_list(text[])` — a three-line immutable
--   SQL function the constraint calls. Putting the logic in a function also gives the rule a
--   name, which a 200-character regex inside a DDL statement does not have.
--
-- HONESTY NOTE (same as 0001_init.sql, and it applies with more force here)
--   This file has **not** been executed against a real Postgres or a real Supabase project:
--   this environment has no Postgres, no docker and no supabase CLI. What *is* tested is
--   (a) the text of this file, asserted against the frozen vocabulary and the column
--   whitelist by `apps/api/tests/test_events.py`, and (b) the SQLite mirror of the same table
--   in `apps/api/repository.py`, which is what every request in this build actually writes
--   to. Treat "it compiles on Postgres" as UNVERIFIED. Every CHECK below is written twice for
--   that reason — the SQLite copy is the one that runs, this one is the one that ships.
-- =====================================================================================

begin;

-- -------------------------------------------------------------------------------------
-- public.events_field_names_are_name_list — the only place a subquery is allowed.
--
-- A CHECK constraint may not contain a subquery, and "every element matches this regex"
-- needs `unnest`. So the predicate lives here, marked `immutable` because it is a pure
-- function of its argument (regex matching on `text` is immutable), which is what makes it
-- legal to call from a constraint and safe to inline in a plan.
--
-- NULL arrays return true: `field_names` is optional (`attributes_edited` fires on sheet
-- dismissal and the history list may be empty or absent). A NULL *element* is not "absent",
-- it is a name-less field, and it is rejected — as is `['']`, which the regex refuses.
-- -------------------------------------------------------------------------------------
create or replace function public.events_field_names_are_name_list(names text[])
returns boolean
language sql
immutable
as $$
    select names is null
        or (cardinality(names) <= 20
            and array_position(names, null) is null
            and not exists (
                select 1
                  from unnest(names) as one_name
                 where one_name !~ '^[a-z][a-z0-9_]*$'
                    or length(one_name) > 64
            ));
$$;

comment on function public.events_field_names_are_name_list(text[]) is
    'True when every element is a snake_case field NAME of at most 64 characters (^[a-z][a-z0-9_]*$), at most 20 of them, none NULL. Values cannot pass: `color:red` fails. The 64-character cap is not in the shared schema: the alphabet alone leaves a multi-kilobyte lowercase channel inside a column documented as names only.';

-- -------------------------------------------------------------------------------------
-- public.events — append-only funnel rows.
--
-- `event_id` is a **client-generated uuid and the primary key**. It is the idempotency key:
-- a phone that retries an offline-queued event sends the same id, and the insert is a no-op.
-- It is deliberately not `default gen_random_uuid()`: a server-generated id would make every
-- retry a new row, which is how a funnel report ends up counting one seller's flaky network
-- as three paywall impressions.
--
-- `client_ts` is the client's clock and `received_at` is ours. Both are stored, neither is
-- trusted: the skew is a *fact worth measuring*, not an error to reconcile (a phone with a
-- wrong clock is a real thing and the funnel must still be readable). Every report orders by
-- `received_at` and uses `client_ts` only for duration arithmetic inside one session.
--
-- `payload_sha256` is the digest of the canonicalised JSON payload. It exists to tell two
-- very different situations apart:
--   * same `event_id`, same digest   -> a genuine retry            -> 202, duplicate: true
--   * same `event_id`, other digest  -> the client reused an id    -> 409
-- Silently accepting the second one would make the funnel quietly wrong (one id, one row,
-- whichever payload happened to arrive first), and nobody would ever see it.
-- -------------------------------------------------------------------------------------
create table if not exists public.events (
    -- identity / idempotency
    event_id              uuid        primary key,
    -- envelope (packages/shared/src/analytics.ts `Envelope`)
    event                 text        not null,
    client_ts             timestamptz not null,
    received_at           timestamptz not null default now(),
    session_id            text        not null,
    -- `anon_<uuid>` before signup, `usr_<uuid>` after. NEVER an email (docs/CONTRACTS.md §7).
    subject               text        not null,
    app_version           text        not null,
    platform              text        not null,
    -- 1. upload_started
    source                text,
    capture_mode          text,
    batch_index           integer,
    -- 2. prettify_approved
    decision              text,
    job_latency_ms        integer,
    prettify_latency_ms   integer,
    attrs_latency_ms      integer,
    -- 3. attributes_edited
    edited_field_count    integer,
    fields_touched        integer,
    field_names           text[],
    -- 4. export_clicked + 7. export_delivered
    marketplace           text,
    row_count             integer,
    export_latency_ms     integer,
    bytes                 bigint,
    delivery_latency_ms   integer,
    -- 5. subscription_started
    plan                  text,
    paywall_variant       text,
    amount_cents          integer,
    currency              text,
    -- 6. paywall_impression
    paywall_trigger       text,
    plan_offered          text,
    -- replay-vs-misuse discriminator
    payload_sha256        text        not null,

    -- ===== the frozen vocabulary =====================================================
    -- docs/CONTRACTS.md §7, packages/shared/src/analytics.ts (EVENTS_VERSION 1.4.0).
    -- An eighth name is an ADR + a version bump, not a commit to this list.
    constraint events_event_check check (event in (
        'upload_started',
        'prettify_approved',
        'attributes_edited',
        'export_clicked',
        'subscription_started',
        'paywall_impression',
        'export_delivered'
    )),

    -- ===== envelope shapes ===========================================================
    constraint events_platform_check check (platform in ('ios', 'android', 'web')),
    -- `subject` is opaque and namespaced. The API additionally proves the suffix equals the
    -- authenticated caller's uuid (403 otherwise, apps/api/routers/events.py); the CHECK here
    -- can only prove the shape, which is why the binding lives in code AND the shape lives here.
    constraint events_subject_shape check (subject ~ '^(anon|usr)_[0-9a-f-]{36}$'),
    constraint events_session_id_shape check (session_id ~ '^[A-Za-z0-9_-]{1,64}$'),
    constraint events_app_version_shape check (app_version ~ '^[0-9A-Za-z.+-]{1,32}$'),
    constraint events_payload_sha256_is_hex check (payload_sha256 ~ '^[0-9a-f]{64}$'),

    -- ===== 1. upload_started =========================================================
    constraint events_upload_started_required
        check (event <> 'upload_started'
               or (source is not null and capture_mode is not null)),
    constraint events_source_check
        check (source is null or source in ('camera', 'library')),
    constraint events_capture_mode_check
        check (capture_mode is null or capture_mode in ('single', 'batch')),
    constraint events_batch_index_non_negative
        check (batch_index is null or batch_index >= 0),

    -- ===== 2. prettify_approved ======================================================
    -- All three latency proxies are required here on purpose: a seller who never opens the
    -- attribute sheet must still report `attrs_latency_ms`, or tool time is under-counted
    -- (docs/CONTRACTS.md §7 "Latency proxies").
    constraint events_prettify_approved_required
        check (event <> 'prettify_approved'
               or (decision is not null
                   and job_latency_ms is not null
                   and prettify_latency_ms is not null
                   and attrs_latency_ms is not null)),
    constraint events_decision_check
        check (decision is null or decision in ('approve', 'retry', 'remove_bg_waitlist')),
    constraint events_job_latency_non_negative
        check (job_latency_ms is null or job_latency_ms >= 0),
    constraint events_prettify_latency_non_negative
        check (prettify_latency_ms is null or prettify_latency_ms >= 0),
    constraint events_attrs_latency_non_negative
        check (attrs_latency_ms is null or attrs_latency_ms >= 0),

    -- ===== 3. attributes_edited ======================================================
    -- `fields_touched` may be 0: the event fires when the sheet is dismissed, and "seller
    -- opened it, read it, changed nothing" is the blind-accept bucket. Do not add `> 0`.
    constraint events_attributes_edited_required
        check (event <> 'attributes_edited'
               or (edited_field_count is not null and fields_touched is not null)),
    constraint events_edited_field_count_non_negative
        check (edited_field_count is null or edited_field_count >= 0),
    constraint events_fields_touched_non_negative
        check (fields_touched is null or fields_touched >= 0),
    -- name list, not a value list: no NULLs, at most 20, every element a snake_case name
    constraint events_field_names_are_names
        check (public.events_field_names_are_name_list(field_names)),

    -- ===== 4. export_clicked =========================================================
    constraint events_export_clicked_required
        check (event <> 'export_clicked'
               or (marketplace is not null
                   and row_count is not null
                   and export_latency_ms is not null)),

    -- ===== 7. export_delivered =======================================================
    constraint events_export_delivered_required
        check (event <> 'export_delivered'
               or (marketplace is not null
                   and row_count is not null
                   and bytes is not null
                   and delivery_latency_ms is not null)),

    constraint events_marketplace_check
        check (marketplace is null or marketplace in ('ebay', 'poshmark', 'vinted')),
    -- one row is a real export; zero rows is a no-op that must not become an activation
    constraint events_row_count_positive check (row_count is null or row_count >= 1),
    constraint events_export_latency_non_negative
        check (export_latency_ms is null or export_latency_ms >= 0),
    constraint events_bytes_non_negative check (bytes is null or bytes >= 0),
    constraint events_delivery_latency_non_negative
        check (delivery_latency_ms is null or delivery_latency_ms >= 0),

    -- ===== 5. subscription_started ===================================================
    constraint events_subscription_started_required
        check (event <> 'subscription_started'
               or (plan is not null
                   and paywall_variant is not null
                   and amount_cents is not null
                   and currency is not null)),
    constraint events_plan_check check (plan is null or plan in ('starter', 'pro')),
    constraint events_amount_cents_non_negative
        check (amount_cents is null or amount_cents >= 0),
    -- zod says `z.string().length(3)`. Length is all this column may claim: an ISO-4217
    -- pattern (`^[A-Z]{3}$`) would be stricter than the frozen shared schema and would reject
    -- a payload the client validated as legal. Three characters is the ceiling of what this
    -- column can carry, and that is stated in the handoff rather than papered over.
    constraint events_currency_length check (currency is null or length(currency) = 3),

    -- ===== 6. paywall_impression =====================================================
    constraint events_paywall_impression_required
        check (event <> 'paywall_impression'
               or (paywall_variant is not null
                   and paywall_trigger is not null
                   and plan_offered is not null)),
    constraint events_paywall_trigger_check
        check (paywall_trigger is null
               or paywall_trigger in ('upload_4th', 'on_model', 'export_limit')),
    constraint events_plan_offered_check
        check (plan_offered is null or plan_offered in ('starter', 'pro')),

    -- ===== shared enums ==============================================================
    -- `paywall_variant` is the one field two events legitimately share: an impression is
    -- attributed to a variant and so is the subscription it converts into. Splitting it into
    -- two columns would destroy the join that makes the A/B report possible.
    constraint events_paywall_variant_check
        check (paywall_variant is null or paywall_variant in ('a', 'b', 'c')),

    -- ===== payload cannot be smuggled onto the wrong event ============================
    -- The forward CHECKs above say "this event has these columns". These say the converse:
    -- "no other event has them". Without the second half, `upload_started` could carry a
    -- `marketplace` or a `paywall_variant` and the column would become a general-purpose
    -- pocket — which is how a closed vocabulary grows a free-text field by accident.
    constraint events_upload_fields_only_on_upload_started
        check (event = 'upload_started'
               or (source is null and capture_mode is null and batch_index is null)),
    constraint events_prettify_fields_only_on_prettify_approved
        check (event = 'prettify_approved'
               or (decision is null
                   and job_latency_ms is null
                   and prettify_latency_ms is null
                   and attrs_latency_ms is null)),
    constraint events_attribute_fields_only_on_attributes_edited
        check (event = 'attributes_edited'
               or (edited_field_count is null and fields_touched is null and field_names is null)),
    constraint events_marketplace_only_on_export_events
        check (marketplace is null or event in ('export_clicked', 'export_delivered')),
    constraint events_row_count_only_on_export_events
        check (row_count is null or event in ('export_clicked', 'export_delivered')),
    constraint events_export_latency_only_on_export_clicked
        check (export_latency_ms is null or event = 'export_clicked'),
    constraint events_delivery_fields_only_on_export_delivered
        check ((bytes is null and delivery_latency_ms is null) or event = 'export_delivered'),
    constraint events_plan_fields_only_on_subscription_started
        check ((plan is null and amount_cents is null and currency is null)
               or event = 'subscription_started'),
    constraint events_paywall_variant_only_on_paywall_events
        check (paywall_variant is null
               or event in ('subscription_started', 'paywall_impression')),
    constraint events_paywall_trigger_only_on_paywall_impression
        check (paywall_trigger is null or event = 'paywall_impression'),
    constraint events_plan_offered_only_on_paywall_impression
        check (plan_offered is null or event = 'paywall_impression')
);

comment on table public.events is
    'Analytics funnel rows (docs/CONTRACTS.md §7, EVENTS_VERSION 1.4.0). Backend-only table: RLS is enabled and the only policy is for the service role. `anon` and `authenticated` have NO permission of any kind — clients write through POST /v1/events and never read this table, because a client that can read the funnel can read other sellers'' behaviour. No column here can hold free text; see the header of 0002_events.sql.';
comment on column public.events.event_id is
    'Client-generated uuid, and the idempotency key. Not server-generated: a retry must reuse the id or the funnel counts one flaky network as several events.';
comment on column public.events.subject is
    'Opaque: anon_<uuid> / usr_<uuid>. Never an email. POST /v1/events additionally requires the suffix to equal the authenticated caller id (403 otherwise).';
comment on column public.events.client_ts is
    'Client clock, UTC. Stored for intra-session duration arithmetic only; reports order by received_at. Skew is expected and is itself measurable.';
comment on column public.events.payload_sha256 is
    'sha256 of the canonicalised payload. Same id + same digest = retry (202 duplicate); same id + different digest = 409.';
comment on column public.events.field_names is
    'attributes_edited: field NAMES only, snake_case, max 20, no NULLs. Values are banned by CHECK, not by convention.';
comment on column public.events.paywall_trigger is
    'paywall_impression only: upload_4th | on_model | export_limit.';

-- -------------------------------------------------------------------------------------
-- Indexes. Two read patterns exist and nothing else:
--   * the funnel: one event, newest first           -> (event, received_at desc)
--   * one seller's session timeline                 -> (subject, received_at desc)
-- No index on `client_ts`: nobody orders by a clock the client controls.
-- -------------------------------------------------------------------------------------
create index if not exists events_event_received_idx
    on public.events (event, received_at desc);
create index if not exists events_subject_received_idx
    on public.events (subject, received_at desc);

-- -------------------------------------------------------------------------------------
-- Row Level Security. ON, with exactly one policy, for the service role.
--
-- Why so blunt: the events table is a *backend* table. `authenticated` has no business
-- reading the funnel (it would expose other sellers' behaviour through any aggregate query a
-- future report endpoint forgets to filter), and `anon`/`authenticated` must never INSERT —
-- a client that can insert `export_delivered` can inflate activation, which is the one number
-- the whole price experiment rests on. Writes arrive through `POST /v1/events`, which is
-- served by the API's service-role connection.
--
-- `force row level security` is not used here: on Supabase the table owner is the migration
-- role, which is a trusted backend role, and forcing RLS on it would break the same service
-- role this policy is written for. The policy is the statement of intent either way.
-- -------------------------------------------------------------------------------------
alter table public.events enable row level security;

drop policy if exists events_service_role_all on public.events;
create policy events_service_role_all on public.events
    for all to service_role using (true) with check (true);

commit;
