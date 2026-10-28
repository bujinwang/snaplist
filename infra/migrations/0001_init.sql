-- =====================================================================================
-- SnapList — 0001_init.sql
-- Owner: backend role (E5-T1 in docs/PLAN.md). Target: Supabase (Postgres 15) / Postgres 14+.
--
-- WHAT THIS FILE IS
--   The whole v1 schema: 8 product tables, RLS on every one of them, the indexes the
--   workers poll on, and the `updated_at` trigger. It is idempotent (every CREATE is
--   guarded) so it can be re-applied to a scratch database while iterating.
--
-- WHAT THIS FILE IS NOT
--   It is not an application. Nothing here computes a quota; the ledger lives in
--   `usage_events` and the *only* legal way to move a counter is to append a row (§6).
--   Nothing here verifies a Stripe signature or a VTON provider response — those are
--   E8-T2 and E4-T3.
--
-- HONESTY NOTE (read this before trusting the file)
--   This file was executed against a **local vanilla Postgres 14** database using the
--   shim in `infra/migrations/verify/local_supabase_shim.sql`, which creates
--   `auth.users`, `auth.uid()` and the `authenticated` / `anon` roles. It has **not**
--   been run against a real Supabase project (`supabase db reset` needs a project ref and
--   credentials we do not have). See docs/HANDOFF-engineer.md for the verbatim psql
--   transcript, and treat "works on Supabase" as UNVERIFIED until someone runs it there.
--
--   `gen_random_uuid()` is in Postgres core since 13, so no `create extension pgcrypto`
--   is needed. On Supabase it is also available; keeping the extension out means this
--   file applies cleanly to a role that is not superuser.
-- =====================================================================================

begin;

-- -------------------------------------------------------------------------------------
-- 0. Reference data: plan limits
--    BRIEF.md / docs/PRD.md pricing: free 3, starter 100, pro 400 items per month.
--    The numbers live in a table, not in a CHECK list, so a pricing change is a data
--    migration instead of a schema migration. `apps/api/settings.py` mirrors these three
--    values for the API process; a mismatch between the two is a bug, and
--    `apps/api/tests/test_usage.py` pins the mirror.
-- -------------------------------------------------------------------------------------
create table if not exists public.plan_limits (
    plan                        text        primary key,
    items_per_month             integer     not null check (items_per_month > 0),
    on_model_included_per_month integer     not null default 0 check (on_model_included_per_month >= 0),
    constraint plan_limits_plan_check check (plan in ('free', 'starter', 'pro'))
);

comment on table public.plan_limits is
    'Pricing ceilings. The API enforces them at the enqueue point (E8-T3), never in a client.';

insert into public.plan_limits (plan, items_per_month, on_model_included_per_month)
values ('free', 3, 0),
       ('starter', 100, 0),
       ('pro', 400, 0)
on conflict (plan) do nothing;

-- -------------------------------------------------------------------------------------
-- 1. profiles — one row per auth user. Supabase idiom is `profiles.id = auth.users.id`;
--    this file uses `user_id` instead so that *every* table in the schema is filtered by
--    the same predicate (`auth.uid() = user_id`). One rule, no exceptions to remember.
-- -------------------------------------------------------------------------------------
create table if not exists public.profiles (
    user_id                uuid        primary key,
    display_name           text,
    locale                 text        not null default 'en',
    -- server-assigned, sticky for the life of the account (docs/METRICS.md §4)
    paywall_variant        text        not null default 'a',
    -- ADR-003: a seller can start without an account. `is_anonymous` flips to false when
    -- Supabase converts the user in place (updateUser); `user_id` never changes, so no row
    -- ownership moves. A real merge into an existing account sets `merged_into` and re-points
    -- items.user_id explicitly — never silently.
    is_anonymous           boolean     not null default true,
    merged_into            uuid        references public.profiles (user_id) on delete set null,
    onboarding_completed_at timestamptz,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),
    constraint profiles_paywall_variant_check check (paywall_variant in ('a', 'b', 'c')),
    -- a row cannot be its own merge target; that would make the merge chain unreadable
    constraint profiles_merged_into_not_self check (merged_into is null or merged_into <> user_id)
);

comment on column public.profiles.paywall_variant is
    'A/B assignment used by paywall_impression / subscription_started attribution.';
comment on column public.profiles.is_anonymous is
    'ADR-003: true until the anonymous seller is converted in place. Ownership never moves on conversion.';
comment on column public.profiles.merged_into is
    'Set only by an explicit, logged merge of an anonymous profile into an existing account.';

-- -------------------------------------------------------------------------------------
-- 2. items — one garment. `attributes` is the ItemAttributes JSON, validated by
--    apps/api/schemas.py before it is written; the CHECK here only guarantees it is an
--    object, because duplicating 16 field rules in SQL would drift from the contract.
-- -------------------------------------------------------------------------------------
create table if not exists public.items (
    id            uuid        primary key default gen_random_uuid(),
    user_id       uuid        not null,
    status        text        not null default 'draft',
    source_path   text,
    attributes    jsonb,
    deleted_at    timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    constraint items_status_check
        check (status in ('draft', 'processing', 'ready', 'failed', 'deleted')),
    constraint items_attributes_is_object
        check (attributes is null or jsonb_typeof(attributes) = 'object'),
    -- soft delete and status must agree, otherwise a "deleted" item can keep exporting
    constraint items_deleted_agrees_with_status
        check ((deleted_at is null) = (status <> 'deleted'))
);

comment on table public.items is
    'One row per garment. Contract shape of `attributes`: docs/CONTRACTS.md §1 (frozen v1.0.0).';

-- -------------------------------------------------------------------------------------
-- 3. assets — every image we keep. `user_id` is denormalised from items on purpose: an
--    RLS policy that has to join is a policy that can be written wrong once and leak.
-- -------------------------------------------------------------------------------------
create table if not exists public.assets (
    id           uuid        primary key default gen_random_uuid(),
    item_id      uuid        not null references public.items (id) on delete cascade,
    user_id      uuid        not null,
    kind         text        not null,
    storage_path text        not null,
    position     integer     not null default 0,
    width        integer     check (width  is null or width  > 0),
    height       integer     check (height is null or height > 0),
    byte_size    integer     check (byte_size is null or byte_size >= 0),
    meta         jsonb       not null default '{}'::jsonb,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    constraint assets_kind_check
        check (kind in ('source', 'mask', 'cutout', 'prettified', 'on_model')),
    constraint assets_position_non_negative check (position >= 0),
    constraint assets_one_slot_per_kind unique (item_id, kind, position)
);

comment on column public.assets.meta is
    'Stage metadata (backend, device, seconds, cost_usd). Values are measured or absent — never 0.0 as a placeholder.';

-- -------------------------------------------------------------------------------------
-- 4. jobs — the state machine from docs/CONTRACTS.md §3, enforced in the database
--    because the API and the Modal worker are two processes that must not disagree.
-- -------------------------------------------------------------------------------------
create table if not exists public.jobs (
    id            uuid        primary key default gen_random_uuid(),
    item_id       uuid        not null references public.items (id) on delete cascade,
    user_id       uuid        not null,
    kind          text        not null default 'full',
    state         text        not null default 'queued',
    reason_code   text,
    attempts      integer     not null default 0,
    progress      numeric(4, 3) not null default 0.000,
    error_detail  text,
    claimed_by    text,
    heartbeat_at  timestamptz,
    -- Cost auditability (infra/migrations/README.md "Cost auditability — R8"). The two ceilings
    -- in BRIEF ($0.03/item, $0.10/render) are unauditable if a job row forgets what it spent.
    --   provider      who ran the stage: 'fashn' | 'catvton' | 'sam2_hosted' | 'birefnet' |
    --                 <vlm id> | 'stub'. A stub MUST self-report 'stub', or nobody can tell a
    --                 real run from a fake one six weeks from now.
    --   model_version adapter + model id, echoed into ItemAttributes.model_version
    --   cost_micros   integer micro-USD (never float money). NULL means UNMEASURED, not zero —
    --                 the same rule evals/run.py follows for every metric.
    provider      text,
    model_version text,
    cost_micros   bigint,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    constraint jobs_kind_check
        check (kind in ('full', 'prettify', 'attributes', 'vton', 'export')),
    constraint jobs_state_check
        check (state in ('queued', 'running', 'done', 'failed')),
    -- exactly the five reason codes; a sixth value is a contract violation, not a typo
    constraint jobs_reason_code_check
        check (reason_code is null
               or reason_code in ('bad_photo', 'no_garment', 'low_confidence',
                                  'vton_rejected', 'internal')),
    -- CONTRACTS.md §3 invariant 1 + 2
    constraint jobs_reason_code_iff_failed
        check ((state = 'failed') = (reason_code is not null)),
    -- CONTRACTS.md §3 invariant 3
    constraint jobs_attempts_range check (attempts between 0 and 3),
    constraint jobs_attempts_exhausted_is_terminal
        check (attempts < 3 or state in ('done', 'failed')),
    -- CONTRACTS.md §3 invariant 5
    constraint jobs_progress_range check (progress >= 0 and progress <= 1),
    constraint jobs_timestamps_ordered
        check ((started_at  is null or started_at  >= created_at)
           and (finished_at is null or (started_at is not null and finished_at >= started_at))),
    -- money is stored as integer micro-USD and is never negative; NULL = unmeasured
    constraint jobs_cost_micros_non_negative check (cost_micros is null or cost_micros >= 0),
    -- `provider` stays open-ended for VLM ids, but an empty/blank value is not a provider
    constraint jobs_provider_not_blank
        check (provider is null or length(btrim(provider)) between 2 and 64)
);

comment on column public.jobs.provider is
    'Who ran the stage. Stubs write ''stub'' so a real run is distinguishable from a fake one.';
comment on column public.jobs.model_version is
    'Adapter + model id; echoed into ItemAttributes.model_version.';
comment on column public.jobs.cost_micros is
    'Integer micro-USD. NULL means UNMEASURED, never 0. Written only on paths that actually spent money.';

comment on column public.jobs.claimed_by is
    'Worker identity holding the row lock. Only one non-null value at a time by convention + the claim query, not by constraint.';
comment on column public.jobs.reason_code is
    'NULL unless state = failed. Five values, closed set: docs/CONTRACTS.md §4.';

-- -------------------------------------------------------------------------------------
-- 5. usage_events — append-only ledger. Quota is *derived* from this table, never read
--    from a mutable counter: a counter can be double-decremented by a retry, a ledger
--    cannot (the partial unique index below makes the double refund impossible).
--
--    Event vocabulary, and what each one is allowed to carry:
--      item_enqueued               units=-1  quota spent at enqueue (E8-T3)
--      job_refunded                units=+1  terminal failure refund, at most once per job
--      export_created              units=+1  one marketplace export produced
--      on_model_rendered           units=+1  one VTON render produced
--      attributes_edited           units=+1  fields_touched + edited_field_count (both)
--      activation_items_exported_7d units=+1 metric sample, 7-day cohort
--      session_export_burst_60m    units=+1  metric sample, 60-minute window
--      free_tier_item_limit        units=+1  metric sample, a free seller hit the ceiling
-- -------------------------------------------------------------------------------------
create table if not exists public.usage_events (
    id                 bigint      generated always as identity primary key,
    user_id            uuid        not null,
    event              text        not null,
    -- signed: negative spends, positive refunds / records an occurrence
    units              integer     not null,
    job_id             uuid        references public.jobs (id) on delete set null,
    item_id            uuid        references public.items (id) on delete set null,
    plan               text,
    marketplace        text,
    session_id         text,
    -- attributes_edited only. `fields_touched` counts reading as well as changing;
    -- `edited_field_count` counts only values that actually changed (docs/METRICS.md §3).
    fields_touched     integer     check (fields_touched is null or fields_touched >= 0),
    edited_field_count integer     check (edited_field_count is null or edited_field_count >= 0),
    field_names        text[],
    metadata           jsonb       not null default '{}'::jsonb,
    created_at         timestamptz not null default now(),
    constraint usage_events_event_check check (event in (
        'item_enqueued',
        'job_refunded',
        'export_created',
        'on_model_rendered',
        'attributes_edited',
        'activation_items_exported_7d',
        'session_export_burst_60m',
        'free_tier_item_limit'
    )),
    constraint usage_events_units_not_zero check (units <> 0),
    constraint usage_events_plan_check
        check (plan is null or plan in ('free', 'starter', 'pro')),
    constraint usage_events_marketplace_check
        check (marketplace is null or marketplace in ('ebay', 'poshmark', 'vinted')),
    -- the two counters belong to one event and nowhere else: that is what makes the
    -- "touched vs edited" distinction impossible to blur
    constraint usage_events_counters_only_on_attributes_edited
        check ((fields_touched is null and edited_field_count is null)
               or event = 'attributes_edited'),
    constraint usage_events_edited_le_touched
        check (edited_field_count is null or fields_touched is null
               or edited_field_count <= fields_touched),
    constraint usage_events_field_names_are_names
        check (field_names is null or array_position(field_names, null) is null)
);

comment on table public.usage_events is
    'Append-only ledger. Free allowance = -sum(units) over the current period for item_enqueued + job_refunded.';
comment on column public.usage_events.fields_touched is
    'attributes_edited: fields the seller interacted with, confirm-only included. Honest workload number.';
comment on column public.usage_events.edited_field_count is
    'attributes_edited: fields whose value actually changed. A lower bound on fields_touched.';

-- one spend and one refund per job, ever. This is the idempotency guarantee in
-- CONTRACTS.md §3 invariant 4, expressed where a retry cannot bypass it.
create unique index if not exists usage_events_one_spend_per_job
    on public.usage_events (job_id)
    where event = 'item_enqueued' and job_id is not null;

create unique index if not exists usage_events_one_refund_per_job
    on public.usage_events (job_id)
    where event = 'job_refunded' and job_id is not null;

-- -------------------------------------------------------------------------------------
-- 6. subscriptions — one row per user. Overage is opt-in and priced separately, per the
--    cost ceiling in BRIEF.md: a seller must not be able to burn unbounded VTON spend.
-- -------------------------------------------------------------------------------------
create table if not exists public.subscriptions (
    user_id                uuid        primary key,
    plan                   text        not null default 'free' references public.plan_limits (plan),
    status                 text        not null default 'inactive',
    stripe_customer_id     text,
    stripe_subscription_id text,
    current_period_start   timestamptz,
    current_period_end     timestamptz,
    cancel_at_period_end   boolean     not null default false,
    -- E8: overage is off unless someone deliberately turned it on *and* priced it
    overage_enabled        boolean     not null default false,
    overage_unit_price_cents integer,
    created_at             timestamptz not null default now(),
    updated_at             timestamptz not null default now(),
    constraint subscriptions_status_check
        check (status in ('inactive', 'trialing', 'active', 'past_due', 'canceled')),
    constraint subscriptions_overage_price_check
        check (overage_unit_price_cents is null or overage_unit_price_cents >= 0),
    -- an enabled overage with no price is free money for the seller and unbounded cost for
    -- us; refuse the row instead of discovering it on an invoice
    constraint subscriptions_overage_needs_a_price
        check (overage_enabled = false or overage_unit_price_cents is not null),
    constraint subscriptions_period_ordered
        check (current_period_start is null or current_period_end is null
               or current_period_end > current_period_start)
);

create unique index if not exists subscriptions_stripe_customer_key
    on public.subscriptions (stripe_customer_id)
    where stripe_customer_id is not null;

create unique index if not exists subscriptions_stripe_subscription_key
    on public.subscriptions (stripe_subscription_id)
    where stripe_subscription_id is not null;

comment on column public.subscriptions.overage_enabled is
    'Opt-in overage billing. False for every plan until E8-T1 turns it on deliberately.';

-- -------------------------------------------------------------------------------------
-- 7. model_refs — the on-model reference images. E9-T1 (legal) requires a signed release
--    for a real person; synthetic references are the default so a missing release cannot
--    silently ship a real likeness.
-- -------------------------------------------------------------------------------------
create table if not exists public.model_refs (
    id                        uuid        primary key default gen_random_uuid(),
    user_id                   uuid        not null,
    name                      text        not null,
    provider                  text        not null default 'fashn',
    storage_path              text        not null,
    is_synthetic              boolean     not null default true,
    consent_signed_at         timestamptz,
    consent_document_version  text,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now(),
    constraint model_refs_provider_check check (provider in ('fashn', 'catvton')),
    constraint model_refs_name_not_blank check (length(btrim(name)) between 1 and 120),
    -- E9-T1: a real person's likeness needs a release on file. Synthetic references do not.
    constraint model_refs_consent_or_synthetic
        check (is_synthetic or consent_signed_at is not null),
    constraint model_refs_consent_needs_a_version
        check (consent_signed_at is null or consent_document_version is not null),
    constraint model_refs_unique_name_per_user unique (user_id, name)
);

comment on constraint model_refs_consent_or_synthetic on public.model_refs is
    'Legal guard (E9-T1): real likeness requires consent_signed_at; synthetic is the default.';

-- -------------------------------------------------------------------------------------
-- 8. api_keys — machine credentials. Only a SHA-256 hex digest is stored: the CHECK makes
--    it impossible to insert a raw key by accident.
-- -------------------------------------------------------------------------------------
create table if not exists public.api_keys (
    id           uuid        primary key default gen_random_uuid(),
    user_id      uuid        not null,
    name         text        not null,
    key_prefix   text        not null,
    key_hash     text        not null,
    scopes       text[]      not null default array['read']::text[],
    last_used_at timestamptz,
    revoked_at   timestamptz,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    constraint api_keys_hash_is_sha256_hex check (key_hash ~ '^[0-9a-f]{64}$'),
    constraint api_keys_prefix_len check (length(key_prefix) between 4 and 16),
    constraint api_keys_name_not_blank check (length(btrim(name)) between 1 and 120),
    constraint api_keys_unique_name_per_user unique (user_id, name)
);

comment on column public.api_keys.key_hash is
    'sha256 hex of the key. The plaintext key is shown once at creation and never stored.';

-- -------------------------------------------------------------------------------------
-- 9. updated_at — maintained by a trigger, never by application code. An API route that
--    forgets to bump updated_at is a cache bug we would rather not be able to write.
-- -------------------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

comment on function public.set_updated_at() is
    'BEFORE UPDATE trigger body. Keeps updated_at honest without trusting every call site.';

drop trigger if exists set_updated_at on public.profiles;
create trigger set_updated_at before update on public.profiles
    for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at on public.items;
create trigger set_updated_at before update on public.items
    for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at on public.assets;
create trigger set_updated_at before update on public.assets
    for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at on public.jobs;
create trigger set_updated_at before update on public.jobs
    for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at on public.subscriptions;
create trigger set_updated_at before update on public.subscriptions
    for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at on public.model_refs;
create trigger set_updated_at before update on public.model_refs
    for each row execute function public.set_updated_at();

drop trigger if exists set_updated_at on public.api_keys;
create trigger set_updated_at before update on public.api_keys
    for each row execute function public.set_updated_at();

-- -------------------------------------------------------------------------------------
-- 10. Indexes
--     (user_id, created_at desc) is the seller-facing list query in every table.
--     The jobs indexes are the ones the worker claim actually uses (E5-T3):
--       select ... from jobs where state = 'queued' order by created_at
--         for update skip locked;
-- -------------------------------------------------------------------------------------
create index if not exists items_user_created_idx
    on public.items (user_id, created_at desc);
create index if not exists items_user_live_idx
    on public.items (user_id, created_at desc) where deleted_at is null;

create index if not exists assets_item_idx on public.assets (item_id, kind, position);
create index if not exists assets_user_created_idx on public.assets (user_id, created_at desc);

create index if not exists jobs_user_created_idx on public.jobs (user_id, created_at desc);
create index if not exists jobs_state_created_idx on public.jobs (state, created_at);
create index if not exists jobs_queued_idx on public.jobs (created_at) where state = 'queued';
create index if not exists jobs_item_idx on public.jobs (item_id, created_at desc);
create index if not exists jobs_running_heartbeat_idx
    on public.jobs (heartbeat_at) where state = 'running';

create index if not exists usage_events_user_created_idx
    on public.usage_events (user_id, created_at desc);
create index if not exists usage_events_event_created_idx
    on public.usage_events (event, created_at desc);
create index if not exists usage_events_item_idx
    on public.usage_events (item_id) where item_id is not null;

create index if not exists model_refs_user_created_idx
    on public.model_refs (user_id, created_at desc);
create index if not exists api_keys_user_idx on public.api_keys (user_id);
create unique index if not exists api_keys_hash_key on public.api_keys (key_hash);

-- -------------------------------------------------------------------------------------
-- 11. Row Level Security — ON for every table, with a stated policy each.
--     Seller-facing roles (authenticated) never write the ledger, never write jobs and
--     never write subscriptions: those are service-role operations, because a client that
--     can insert `item_enqueued` can mint free quota.
-- -------------------------------------------------------------------------------------
alter table public.plan_limits   enable row level security;
alter table public.profiles      enable row level security;
alter table public.items         enable row level security;
alter table public.assets        enable row level security;
alter table public.jobs          enable row level security;
alter table public.usage_events  enable row level security;
alter table public.subscriptions enable row level security;
alter table public.model_refs    enable row level security;
alter table public.api_keys      enable row level security;

-- plan_limits: reference data, readable by any authenticated seller, writable by nobody
-- through the API (service role / migrations only).
drop policy if exists plan_limits_read_all on public.plan_limits;
create policy plan_limits_read_all on public.plan_limits
    for select to authenticated using (true);

-- profiles: own row only, read + update. Insert happens in the auth trigger (service role).
drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
    for update to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- items: full CRUD on own rows. DELETE is a soft delete in the API, but a hard delete of
-- your own junk row is not a security event.
drop policy if exists items_select_own on public.items;
create policy items_select_own on public.items
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists items_insert_own on public.items;
create policy items_insert_own on public.items
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists items_update_own on public.items;
create policy items_update_own on public.items
    for update to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists items_delete_own on public.items;
create policy items_delete_own on public.items
    for delete to authenticated using (auth.uid() = user_id);

-- assets: read own. Writes come from the worker via the service role, because the storage
-- path is signed server-side (E5-T4).
drop policy if exists assets_select_own on public.assets;
create policy assets_select_own on public.assets
    for select to authenticated using (auth.uid() = user_id);

-- jobs: read own, nothing else. A seller who could UPDATE jobs could set state='failed' to
-- trigger a refund, which is exactly the fraud this policy exists to prevent.
drop policy if exists jobs_select_own on public.jobs;
create policy jobs_select_own on public.jobs
    for select to authenticated using (auth.uid() = user_id);

-- usage_events: read own, append nothing. Same rationale as jobs, one step stronger: the
-- ledger *is* the money.
drop policy if exists usage_events_select_own on public.usage_events;
create policy usage_events_select_own on public.usage_events
    for select to authenticated using (auth.uid() = user_id);

-- subscriptions: read own. Stripe webhooks (service role) are the only writer.
drop policy if exists subscriptions_select_own on public.subscriptions;
create policy subscriptions_select_own on public.subscriptions
    for select to authenticated using (auth.uid() = user_id);

-- model_refs: the seller owns these images, so full CRUD on own rows.
drop policy if exists model_refs_select_own on public.model_refs;
create policy model_refs_select_own on public.model_refs
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists model_refs_insert_own on public.model_refs;
create policy model_refs_insert_own on public.model_refs
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists model_refs_update_own on public.model_refs;
create policy model_refs_update_own on public.model_refs
    for update to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists model_refs_delete_own on public.model_refs;
create policy model_refs_delete_own on public.model_refs
    for delete to authenticated using (auth.uid() = user_id);

-- api_keys: the seller can manage their own keys, but never sees another's digest.
drop policy if exists api_keys_select_own on public.api_keys;
create policy api_keys_select_own on public.api_keys
    for select to authenticated using (auth.uid() = user_id);

drop policy if exists api_keys_insert_own on public.api_keys;
create policy api_keys_insert_own on public.api_keys
    for insert to authenticated with check (auth.uid() = user_id);

drop policy if exists api_keys_update_own on public.api_keys;
create policy api_keys_update_own on public.api_keys
    for update to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

drop policy if exists api_keys_delete_own on public.api_keys;
create policy api_keys_delete_own on public.api_keys
    for delete to authenticated using (auth.uid() = user_id);

commit;
