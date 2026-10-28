-- =====================================================================================
-- CONSTRAINT PROOF — not a migration. Run after 0001_init.sql on the shimmed database.
--
-- A CHECK constraint nobody has seen reject anything is a comment with worse syntax.
-- Each block below asserts that the database *refuses* a row the contract forbids.
-- Any uncaught insert aborts the script under `psql -v ON_ERROR_STOP=1`.
-- Pass prints "CONSTRAINT PROOF: PASS (14/14)".
-- =====================================================================================

\set ON_ERROR_STOP on
\set seller '''33333333-3333-4333-8333-333333333333'''

insert into auth.users (id) values (:seller) on conflict (id) do nothing;

-- 1. jobs: state='failed' without a reason_code
do $$
begin
    begin
        insert into public.jobs (item_id, user_id, state)
        values ((select id from public.items limit 1), '33333333-3333-4333-8333-333333333333', 'failed');
        raise exception 'CONSTRAINT MISS: failed job without reason_code was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 2. jobs: a sixth reason code
do $$
begin
    begin
        insert into public.jobs (item_id, user_id, state, reason_code)
        values ((select id from public.items limit 1), '33333333-3333-4333-8333-333333333333',
                'failed', 'provider_said_no');
        raise exception 'CONSTRAINT MISS: a reason code outside the frozen five was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 3. jobs: reason_code on a job that is not failed
do $$
begin
    begin
        insert into public.jobs (item_id, user_id, state, reason_code)
        values ((select id from public.items limit 1), '33333333-3333-4333-8333-333333333333',
                'queued', 'internal');
        raise exception 'CONSTRAINT MISS: queued job carried a reason_code';
    exception when check_violation then null;
    end;
end
$$;

-- 4. jobs: attempts == 3 but a non-terminal state (CONTRACTS.md §3 invariant 3)
do $$
begin
    begin
        insert into public.jobs (item_id, user_id, state, attempts)
        values ((select id from public.items limit 1), '33333333-3333-4333-8333-333333333333',
                'running', 3);
        raise exception 'CONSTRAINT MISS: attempts=3 in a non-terminal state was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 5. jobs: finished_at before started_at
do $$
begin
    begin
        insert into public.jobs (item_id, user_id, state, started_at, finished_at)
        values ((select id from public.items limit 1), '33333333-3333-4333-8333-333333333333',
                'done', now(), now() - interval '1 hour');
        raise exception 'CONSTRAINT MISS: finished_at < started_at was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 6. subscriptions: overage enabled with no price
do $$
begin
    begin
        insert into public.subscriptions (user_id, plan, overage_enabled, overage_unit_price_cents)
        values ('33333333-3333-4333-8333-333333333333', 'pro', true, null);
        raise exception 'CONSTRAINT MISS: overage_enabled without a price was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 7. subscriptions: a plan outside the frozen three
do $$
begin
    begin
        insert into public.subscriptions (user_id, plan)
        values ('33333333-3333-4333-8333-333333333333', 'enterprise');
        raise exception 'CONSTRAINT MISS: an unknown plan was accepted';
    exception when foreign_key_violation then null;
    end;
end
$$;

-- 8. api_keys: a raw key instead of a sha256 hex digest
do $$
begin
    begin
        insert into public.api_keys (user_id, name, key_prefix, key_hash)
        values ('33333333-3333-4333-8333-333333333333', 'cli', 'sk_live_', 'sk_live_plaintext_key');
        raise exception 'CONSTRAINT MISS: a plaintext api key was stored';
    exception when check_violation then null;
    end;
end
$$;

-- 9. model_refs: a real likeness with no signed release
do $$
begin
    begin
        insert into public.model_refs (user_id, name, storage_path, is_synthetic, consent_signed_at)
        values ('33333333-3333-4333-8333-333333333333', 'real-person', 'models/x.png', false, null);
        raise exception 'CONSTRAINT MISS: a real likeness without consent was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 10. usage_events: attributes counters on the wrong event
do $$
begin
    begin
        insert into public.usage_events (user_id, event, units, fields_touched, edited_field_count)
        values ('33333333-3333-4333-8333-333333333333', 'export_created', 1, 4, 1);
        raise exception 'CONSTRAINT MISS: attributes counters rode along on export_created';
    exception when check_violation then null;
    end;
end
$$;

-- 11. usage_events: a double refund for one job
do $$
declare
    j uuid;
begin
    insert into public.items (id, user_id) values (gen_random_uuid(), '33333333-3333-4333-8333-333333333333')
        returning id into j;
    insert into public.jobs (id, item_id, user_id, state, reason_code)
        values (gen_random_uuid(), j, '33333333-3333-4333-8333-333333333333', 'failed', 'internal')
        returning id into j;
    insert into public.usage_events (user_id, event, units, job_id)
        values ('33333333-3333-4333-8333-333333333333', 'job_refunded', 1, j);
    begin
        insert into public.usage_events (user_id, event, units, job_id)
            values ('33333333-3333-4333-8333-333333333333', 'job_refunded', 1, j);
        raise exception 'CONSTRAINT MISS: a job was refunded twice';
    exception when unique_violation then null;
    end;
end
$$;

-- 12. jobs: negative money (cost is integer micro-USD, and spend is never negative)
do $$
begin
    begin
        insert into public.jobs (item_id, user_id, cost_micros)
        values ((select id from public.items limit 1), '33333333-3333-4333-8333-333333333333', -1);
        raise exception 'CONSTRAINT MISS: a negative cost_micros was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 13. jobs: a blank provider (a provider name that says nothing is not an audit trail)
do $$
begin
    begin
        insert into public.jobs (item_id, user_id, provider)
        values ((select id from public.items limit 1), '33333333-3333-4333-8333-333333333333', '  ');
        raise exception 'CONSTRAINT MISS: a blank provider was accepted';
    exception when check_violation then null;
    end;
end
$$;

-- 14. profiles: a row merged into itself (ADR-003 merge chain must stay readable)
do $$
begin
    insert into public.profiles (user_id) values ('33333333-3333-4333-8333-333333333333')
        on conflict (user_id) do nothing;
    begin
        update public.profiles set merged_into = '33333333-3333-4333-8333-333333333333'
        where user_id = '33333333-3333-4333-8333-333333333333';
        raise exception 'CONSTRAINT MISS: a profile was merged into itself';
    exception when check_violation then null;
    end;
end
$$;

select 'CONSTRAINT PROOF: PASS (14/14)' as result;
