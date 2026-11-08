-- =====================================================================================
-- SnapList — 0004_repository_parity.sql
-- Owner: backend role (E5-T4). Target: Supabase (Postgres 15) / Postgres 14+.
--
-- WHY THIS FILE EXISTS
--   `apps/api/repository.py::SqliteRepository` (the build that actually runs today) keeps two
--   tables/columns that `0001_init.sql` does not ship, because they were added to the SQLite
--   mirror after `0001` was written and never ported back:
--
--     1. `profiles.plan` — `get_plan()` / `set_plan()` read and write `profiles.plan` on
--        SQLite. `0001` puts `plan` on `public.subscriptions` instead and omits it from
--        `profiles`. The repository cannot reach `subscriptions.plan` without diverging the
--        code path between backends, so the honest fix is to give Postgres the same column
--        `0001` should have had. This keeps `PostgresRepository` byte-for-byte equivalent to
--        `SqliteRepository` for the plan methods (no router changes, no backend branching).
--
--     2. `public.idempotency_keys` — `get_idempotent_item()` / `store_idempotent_item()` are
--        real repository methods used by `POST /v1/items` idempotency. The SQLite mirror has
--        the table; `0001` never created it on Postgres. Without it `PostgresRepository`
--        cannot implement those two methods at all, so the table is added here, mirroring the
--        SQLite shape (composite PK on (user_id, endpoint, key)).
--
-- Both additions are additive and idempotent so this file can be re-applied (the migration
-- hygiene rule: every migration runs clean twice).
--
-- HONESTY NOTE (inherits 0001/0002/0003)
--   Applied + measured against local vanilla PostgreSQL 14.18 behind
--   `infra/migrations/verify/local_supabase_shim.sql`. Supabase-hosted RLS role policies,
--   PG15 and PostgREST remain UNVERIFIED.
-- =====================================================================================

begin;

-- -------------------------------------------------------------------------------------
-- 1. `plan` on profiles, matching the SQLite mirror and `public.subscriptions.plan`.
-- -------------------------------------------------------------------------------------
do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'profiles' and column_name = 'plan'
    ) then
        alter table public.profiles
            add column plan text not null default 'free';
        alter table public.profiles
            add constraint profiles_plan_check
                check (plan in ('free', 'starter', 'pro'));
    end if;
end $$;

-- -------------------------------------------------------------------------------------
-- 2. `idempotency_keys` — the SQLite mirror's table, ported to Postgres.
--    `created_at` defaults to now(); the repository also writes it explicitly.
-- -------------------------------------------------------------------------------------
create table if not exists public.idempotency_keys (
    user_id    uuid        not null,
    endpoint   text        not null,
    key        text        not null,
    item_id    uuid        not null,
    job_id     uuid        not null,
    created_at timestamptz not null default now(),
    primary key (user_id, endpoint, key)
);

create index if not exists idempotency_keys_user_idx
    on public.idempotency_keys (user_id);

comment on table public.idempotency_keys is
    'Request idempotency. Mirrors apps/api/repository.py::SqliteRepository''s idempotency_keys. PK (user_id, endpoint, key) scopes a key to its owner, so a reused key on a shared device cannot hand one user another''s item (test_an_idempotency_key_is_scoped_to_its_owner).';

commit;
