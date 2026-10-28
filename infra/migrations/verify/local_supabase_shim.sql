-- =====================================================================================
-- LOCAL VERIFICATION SHIM — NOT A MIGRATION. Do not apply this to a Supabase project.
--
-- `0001_init.sql` is written for Supabase, where `auth.users`, `auth.uid()`, the
-- `authenticated` / `anon` roles and the default grants already exist. This file recreates
-- just enough of that surface on a **vanilla Postgres** so the migration can actually be
-- executed and its RLS behaviour proven, instead of being asserted in a README.
--
-- It lives in a subdirectory on purpose: Supabase applies only top-level `*.sql` files in
-- the migrations directory, so this can never be picked up by `supabase db reset`.
--
-- Run (see docs/HANDOFF-engineer.md for the transcript):
--   createdb snaplist_migcheck
--   psql -v ON_ERROR_STOP=1 -d snaplist_migcheck -f infra/migrations/verify/local_supabase_shim.sql
--   psql -v ON_ERROR_STOP=1 -d snaplist_migcheck -f infra/migrations/0001_init.sql
-- =====================================================================================

begin;

create schema if not exists auth;

create table if not exists auth.users (
    id    uuid primary key,
    email text
);

-- Supabase's auth.uid() reads the verified JWT claim that PostgREST puts into the session.
-- The shim reads the same GUC, so `set request.jwt.claim.sub = '<uuid>'` reproduces exactly
-- what a logged-in seller session looks like to RLS.
create or replace function auth.uid()
returns uuid
language sql
stable
as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;

comment on function auth.uid() is
    'LOCAL SHIM ONLY. Supabase provides the real implementation from the verified JWT.';

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin noinherit;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
        create role service_role nologin noinherit bypassrls;
    end if;
end
$$;

commit;
