"""Shared fixtures for the API tests.

Every test gets a **fresh in-memory repository** and an app built around it, so one test can
never see another's rows. That is not a convenience: `test_isolation.py` asserts cross-user
behaviour, and it would be meaningless if the two "users" shared state left over from a previous
test.

No credentials, no network, no filesystem database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.repository import PostgresRepository, SqliteRepository
from apps.api.settings import Settings

USER_A = UUID("11111111-1111-4111-8111-111111111111")
USER_B = UUID("22222222-2222-4222-8222-222222222222")
MISSING = UUID("00000000-0000-4000-8000-000000000000")

# The Postgres backend is opt-in: set SNAPLIST_TEST_PG to a `postgresql://` URL and the whole
# API suite runs against both backends. Without it, only SQLite runs — keeping `make test`
# offline and fast (the lean checkout has no psycopg). The URL points at a scratch database
# built from infra/migrations behind verify/local_supabase_shim.sql.
_PG_URL = os.environ.get("SNAPLIST_TEST_PG")
_PG_AVAILABLE = bool(_PG_URL)
_REPOSITORY_PARAMS = ["sqlite"] + (["postgres"] if _PG_AVAILABLE else [])


@pytest.fixture
def settings() -> Settings:
    """Local-dev settings: no JWT secret, so the self-declared header mode is active."""
    return Settings(env="local", supabase_jwt_secret="")


@pytest.fixture(params=_REPOSITORY_PARAMS)
def repository(request) -> Iterator[SqliteRepository | PostgresRepository]:
    if request.param == "sqlite":
        store: SqliteRepository | PostgresRepository = SqliteRepository(":memory:")
        try:
            yield store
        finally:
            store.close()
        return
    # Postgres: a fresh connection per test, wiped between tests so two "users" never share state
    # (test_isolation.py depends on this). See PostgresRepository.truncate_all.
    store = PostgresRepository(_PG_URL)
    try:
        store.truncate_all()
        yield store
    finally:
        store.close()


@pytest.fixture
def app(settings: Settings, repository: SqliteRepository):
    return create_app(repository=repository, settings=settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def headers_a() -> dict[str, str]:
    return {"x-snaplist-user-id": str(USER_A)}


@pytest.fixture
def headers_b() -> dict[str, str]:
    return {"x-snaplist-user-id": str(USER_B)}


def create_item(client: TestClient, headers: dict[str, str], **body: object) -> dict[str, str]:
    response = client.post("/v1/items", headers=headers, json=body or {"filename": "tee.jpg"})
    assert response.status_code == 202, response.text
    payload = response.json()
    return {"item_id": payload["item_id"], "job_id": payload["job_id"]}
