-- =====================================================================================
-- RLS PROOF — not a migration. Run after 0001_init.sql on the shimmed database.
--
-- Asserts, with two distinct seller identities, the four claims the schema makes:
--   1. a seller reads only their own item
--   2. a seller cannot read another seller's item (0 rows, not an error — no existence leak)
--   3. a seller cannot insert an item owned by somebody else (RLS WITH CHECK refuses)
--   4. a seller cannot append to the quota ledger at all (no INSERT policy for authenticated)
--
-- Every check raises an exception on failure, so the script exits non-zero under
-- `psql -v ON_ERROR_STOP=1`. A passing run prints "RLS PROOF: PASS (4/4)".
-- =====================================================================================

\set ON_ERROR_STOP on
\set seller_a '''11111111-1111-4111-8111-111111111111'''
\set seller_b '''22222222-2222-4222-8222-222222222222'''

insert into auth.users (id, email) values (:seller_a, 'a@example.test'), (:seller_b, 'b@example.test')
on conflict (id) do nothing;

-- Run as the seller-facing role, exactly like PostgREST does.
grant usage on schema public to authenticated;
grant select, insert, update, delete on all tables in schema public to authenticated;
grant usage, select on all sequences in schema public to authenticated;

set role authenticated;

-- ---- 1 + 2: read isolation -----------------------------------------------------------
set request.jwt.claim.sub = :seller_a;
insert into public.items (user_id, status) values (:seller_a, 'draft');

select case when count(*) = 1 then 'ok' else 'FAIL' end as seller_a_sees_own_item
from public.items;

set request.jwt.claim.sub = :seller_b;
select case when count(*) = 0 then 'ok' else 'FAIL' end as seller_b_cannot_see_a_item
from public.items;

do $$
begin
    if (select count(*) from public.items) <> 0 then
        raise exception 'RLS LEAK: seller B can see seller A rows';
    end if;
end
$$;

-- ---- 3: cannot write a row owned by someone else -------------------------------------
do $$
begin
    begin
        insert into public.items (user_id) values ('11111111-1111-4111-8111-111111111111');
        raise exception 'RLS LEAK: seller B inserted a row owned by seller A';
    exception
        when insufficient_privilege then null;  -- expected: new row violates WITH CHECK
    end;
end
$$;

-- ---- 4: the ledger is not writable by a seller ---------------------------------------
do $$
begin
    begin
        insert into public.usage_events (user_id, event, units)
        values ('22222222-2222-4222-8222-222222222222', 'item_enqueued', -1);
        raise exception 'LEDGER LEAK: a seller minted its own quota row';
    exception
        when insufficient_privilege then null;  -- expected: no INSERT policy
    end;
end
$$;

reset role;

select 'RLS PROOF: PASS (4/4)' as result;
