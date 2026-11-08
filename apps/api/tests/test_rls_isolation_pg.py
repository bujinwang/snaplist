"""Database-level Row Level Security, proven as the `authenticated` role.

`PostgresRepository` connects as a privileged (service-role-equivalent) user and relies on the
explicit `user_id` predicate every query carries for isolation — exactly like the production API
process behind Supabase's service role, and exactly what `test_isolation.py` already proves at the
application layer. RLS is the *second* layer: it protects anything that could reach the tables
directly (PostgREST, a future report endpoint), not the backend. This test exercises that second
layer the only honest way — by impersonating a logged-in seller (`set role authenticated` +
`request.jwt.claim.sub`) and asserting the four claims from
`infra/migrations/verify/rls_isolation_check.sql`.

It is skipped unless `SNAPLIST_TEST_PG` points at a scratch database built from `infra/migrations`
behind `verify/local_supabase_shim.sql`. Supabase-hosted RLS / PG15 / PostgREST stays UNVERIFIED:
this proves the policy logic on a vanilla Postgres 14, which is the most that can be claimed here.

The `authenticated` role is given `select/insert/update/delete` on every table (as PostgREST would),
so a pass here means "the *policies* enforce isolation", not "the *grants* do". A leaked row is a
policy failure, and the test fails loudly.
"""

from __future__ import annotations

import os
import uuid

import pytest

from apps.api.repository import PostgresRepository

PG_URL = os.environ.get("SNAPLIST_TEST_PG")
pytestmark = pytest.mark.skipif(not PG_URL, reason="SNAPLIST_TEST_PG not set")

# `psycopg` is only present when the optional `pg` dependency group is installed, and the whole
# module is skipped without SNAPLIST_TEST_PG anyway — so import it lazily to keep collection working
# on a lean checkout (CI / `make test`) that has no psycopg.
if PG_URL:
    import psycopg
    from psycopg import sql as psql

SELLER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
SELLER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture(scope="module")
def auth_conn() -> psycopg.Connection:
    conn = psycopg.connect(PG_URL, autocommit=True)
    # PostgREST would grant these; the test proves the *policies* still win.
    conn.execute("grant usage on schema public to authenticated")
    conn.execute(
        "grant select, insert, update, delete on all tables in schema public to authenticated"
    )
    conn.execute("grant usage, select on all sequences in schema public to authenticated")
    yield conn
    conn.close()


def _as(conn: psycopg.Connection, user: uuid.UUID, sql: str, params: tuple = ()) -> None:
    # `set request.jwt.claim.sub` is a GUC assignment and cannot take a bind parameter (psycopg
    # raises "syntax error at or near $1"). The claim is a UUID we control, so composing it as a
    # literal via `sql.Literal` is injection-safe; this mirrors rls_isolation_check.sql.
    conn.execute(
        psql.SQL("set request.jwt.claim.sub = {}").format(psql.Literal(str(user)))
    )
    conn.execute(sql, params)


def test_rls_isolation(auth_conn: psycopg.Connection) -> None:
    # Start from a clean slate so the counts below are deterministic.
    PostgresRepository(PG_URL).truncate_all()
    try:
        auth_conn.execute("set role authenticated")

        # 1 + 2: seller A inserts and sees exactly their own row; seller B sees zero.
        _as(
            auth_conn,
            SELLER_A,
            "insert into public.items (user_id, status) values (%s, 'draft')",
            (str(SELLER_A),),
        )
        assert (
            auth_conn.execute("select count(*) from public.items").fetchone()[0] == 1
        ), "seller A should see their own item"

        _as(auth_conn, SELLER_B, "select 1")
        assert (
            auth_conn.execute("select count(*) from public.items").fetchone()[0] == 0
        ), "RLS LEAK: seller B can see seller A's row"

        # 3: seller B cannot insert a row owned by seller A (WITH CHECK refuses).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(
                auth_conn,
                SELLER_B,
                "insert into public.items (user_id) values (%s)",
                (str(SELLER_A),),
            )

        # 4: the quota ledger is not writable by a seller (no INSERT policy exists).
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(
                auth_conn,
                SELLER_B,
                "insert into public.usage_events (user_id, event, units) "
                "values (%s, 'item_enqueued', -1)",
                (str(SELLER_B),),
            )
    finally:
        auth_conn.execute("reset role")
        auth_conn.execute("reset request.jwt.claim.sub")
