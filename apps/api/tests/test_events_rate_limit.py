"""`POST /v1/events` — the in-process rate limiter (R8, docs/QA.md §10).

CI-ONLY. `apps/api/tests/conftest.py` imports `fastapi`, so the full `make test` cannot run in
the local lean checkout (no fastapi); these assertions are verified by CI. `py_compile` only
checks that the file parses, not that it passes.

What is defended
----------------
The limiter is a per-caller token bucket created on `app.state` inside `create_app()` and keyed
on `caller.user_id` (not `subject`): burst 120, refill 1 token / 12 s, 429 with
`ErrorCode.INVALID_REQUEST`, `retry_after_seconds` and a `Retry-After` header. The 401 check is
the `current_caller` dependency, which runs before the route body, so an unauthenticated request
is refused (401) before the limiter is ever reached — no anonymous fallback is needed.

Fixture-scope note (the high-risk investigation from the ticket): `conftest.py`'s `app` fixture
is **function-scoped** (default scope), so every test builds its own `create_app()` and therefore
its own `event_rate_limiter` with a full bucket of 120. No test in the suite posts >= 120 events
as a single user, and even if one did, the per-test isolation resets the bucket. The rate limiter
is therefore left at full production strength; the tests below swap the limiter for a fake-clock
instance purely to make refill deterministic, which does not touch production code.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.ratelimit import (
    EVENT_BURST_CAPACITY,
    EVENT_REFILL_SECONDS,
    STATUS_TOO_MANY_REQUESTS,
    EventRateLimiter,
)
from apps.api.repository import SqliteRepository
from apps.api.routers import events as ev
from apps.api.settings import Settings
from apps.api.tests.conftest import USER_A, USER_B, create_item

ENDPOINT = "/v1/events"


class _FakeClock:
    """A controllable clock for the limiter: returns a fixed `t` that tests advance by hand."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def fake_clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture
def rl_client(client: TestClient, fake_clock: _FakeClock) -> TestClient:
    """`client` is function-scoped, so it already builds a fresh app (and limiter) per test.

    Swap its limiter for a fake-clock one so refill is deterministic. Production code is
    untouched; the per-test app simply gets a different clock source.
    """
    client.app.state.event_rate_limiter = EventRateLimiter(now=fake_clock)
    return client


def _event_body(
    event_name: str = "paywall_impression", *, user: UUID = USER_A
) -> dict[str, object]:
    """A fresh, valid event body. `paywall_impression` needs no item, so it is the default.

    Each call regenerates `event_id` (via `ev.BUILDERS`), so two calls never collide.
    """
    body = ev.BUILDERS[event_name]()
    body["subject"] = f"usr_{user}"
    return body


def _post(client: TestClient, headers: dict[str, str], body: dict[str, object]):
    return client.post(ENDPOINT, headers=headers, json=body)


@contextmanager
def _file_backed_client(fake_clock: _FakeClock) -> Iterator[tuple[TestClient, Path]]:
    """An app on a file database so a second connection can count rows independently."""
    directory = tempfile.mkdtemp(prefix="snaplist-rl-")
    database = Path(directory) / "events.db"
    repository = SqliteRepository(database)
    app = create_app(
        repository=repository,
        settings=Settings(env="local", supabase_jwt_secret=""),
    )
    app.state.event_rate_limiter = EventRateLimiter(now=fake_clock)
    try:
        with TestClient(app) as client:
            yield client, database
    finally:
        repository.close()
        shutil.rmtree(directory, ignore_errors=True)


def _row_count(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        return int(connection.execute("select count(*) from events").fetchone()[0])


# ---------------------------------------------------------------------------------------
# N+1 exhaustion
# ---------------------------------------------------------------------------------------
def test_n_plus_one_requests_hit_429(rl_client: TestClient) -> None:
    headers = {"x-snaplist-user-id": str(USER_A)}
    statuses: list[int] = []
    for _ in range(EVENT_BURST_CAPACITY + 1):
        statuses.append(_post(rl_client, headers, _event_body()).status_code)
    # first 120 are accepted, the 121st is throttled
    assert statuses[:EVENT_BURST_CAPACITY].count(202) == EVENT_BURST_CAPACITY
    assert statuses[EVENT_BURST_CAPACITY] == STATUS_TOO_MANY_REQUESTS


def test_429_writes_no_event_row(fake_clock: _FakeClock) -> None:
    with _file_backed_client(fake_clock) as (client, database):
        headers = {"x-snaplist-user-id": str(USER_A)}
        for _ in range(EVENT_BURST_CAPACITY):
            assert _post(client, headers, _event_body()).status_code == 202
        assert _row_count(database) == EVENT_BURST_CAPACITY
        # the throttled request must not create a row
        assert _post(client, headers, _event_body()).status_code == STATUS_TOO_MANY_REQUESTS
        assert _row_count(database) == EVENT_BURST_CAPACITY


def test_429_consumes_no_token(rl_client: TestClient, fake_clock: _FakeClock) -> None:
    """Refusal must not mutate the bucket, so one refill regains exactly one token."""
    headers = {"x-snaplist-user-id": str(USER_A)}
    for _ in range(EVENT_BURST_CAPACITY):
        assert _post(rl_client, headers, _event_body()).status_code == 202
    assert _post(rl_client, headers, _event_body()).status_code == STATUS_TOO_MANY_REQUESTS
    fake_clock.t += EVENT_REFILL_SECONDS
    assert _post(rl_client, headers, _event_body()).status_code == 202


def test_limit_is_per_caller_not_global(rl_client: TestClient) -> None:
    headers_a = {"x-snaplist-user-id": str(USER_A)}
    for _ in range(EVENT_BURST_CAPACITY):
        assert _post(rl_client, headers_a, _event_body()).status_code == 202
    assert _post(rl_client, headers_a, _event_body()).status_code == STATUS_TOO_MANY_REQUESTS
    # user B has an independent, still-full bucket
    headers_b = {"x-snaplist-user-id": str(USER_B)}
    assert _post(rl_client, headers_b, _event_body(user=USER_B)).status_code == 202


def test_401_precedes_429(rl_client: TestClient) -> None:
    """No auth -> 401, never 429: the limiter runs after the auth dependency."""
    response = rl_client.post(ENDPOINT, json=_event_body())
    assert response.status_code == 401


# ---------------------------------------------------------------------------------------
# The limiter sits at the TOP of the route; the other refusals must still reach the caller
# before the limit is hit (each of these posts well under the burst, so the bucket allows).
# ---------------------------------------------------------------------------------------
def test_oversized_body_is_still_413(rl_client: TestClient) -> None:
    headers = {"x-snaplist-user-id": str(USER_A)}
    body = _event_body()
    body["padding"] = "x" * (8192 + 512)
    assert _post(rl_client, headers, body).status_code == 413


def test_bad_shape_is_still_422(rl_client: TestClient) -> None:
    headers = {"x-snaplist-user-id": str(USER_A)}
    body = _event_body(event_name="upload_started")
    body["event"] = "item_created"  # unknown tag -> discriminated-union 422
    assert _post(rl_client, headers, body).status_code == 422


def test_foreign_subject_is_still_403(rl_client: TestClient) -> None:
    headers = {"x-snaplist-user-id": str(USER_A)}
    body = _event_body()
    body["subject"] = f"usr_{USER_B}"
    assert _post(rl_client, headers, body).status_code == 403


def test_foreign_item_is_still_404(rl_client: TestClient) -> None:
    headers = {"x-snaplist-user-id": str(USER_A)}
    # a real item owned by B, filed by A
    foreign_item = create_item(rl_client, {"x-snaplist-user-id": str(USER_B)})["item_id"]
    body = _event_body(event_name="upload_started", user=USER_A)
    body["item_id"] = foreign_item
    assert _post(rl_client, headers, body).status_code == 404


def test_replay_different_payload_is_still_409(rl_client: TestClient) -> None:
    headers = {"x-snaplist-user-id": str(USER_A)}
    item = create_item(rl_client, headers)["item_id"]
    body = _event_body(event_name="export_delivered", user=USER_A)
    body["item_id"] = item
    assert _post(rl_client, headers, body).status_code == 202
    changed = dict(body, row_count=99)
    assert _post(rl_client, headers, changed).status_code == 409


def test_replay_same_event_id_consumes_a_token(rl_client: TestClient) -> None:
    """A 202-duplicate still draws a token, because the limiter runs before the idempotency read.

    One new event + 119 replays = 120 accepted requests (each replay is a genuine 202-duplicate,
    so each consumed a token). The 121st replay hits the now-empty bucket and is refused with 429
    instead of 202-duplicate — proving the duplicate path did not bypass the limiter.
    """
    headers = {"x-snaplist-user-id": str(USER_A)}
    original = _event_body()
    assert _post(rl_client, headers, original).status_code == 202
    for _ in range(EVENT_BURST_CAPACITY - 1):
        response = _post(rl_client, headers, dict(original))
        assert response.status_code == 202
        assert response.json()["duplicate"] is True
    # 121st request (replay) is throttled, not a duplicate
    assert _post(rl_client, headers, dict(original)).status_code == STATUS_TOO_MANY_REQUESTS


def test_100_well_spaced_requests_all_202(rl_client: TestClient, fake_clock: _FakeClock) -> None:
    """Headroom: steady, well-spaced traffic never trips the limit."""
    headers = {"x-snaplist-user-id": str(USER_A)}
    for index in range(100):
        response = _post(rl_client, headers, _event_body())
        assert response.status_code == 202, f"request {index} returned {response.status_code}"
        # one token back per request: the bucket stays full for arbitrarily many spaced requests
        fake_clock.t += EVENT_REFILL_SECONDS


def test_client_ts_cannot_buy_tokens(rl_client: TestClient, fake_clock: _FakeClock) -> None:
    """`client_ts` is stored as sent and never grants capacity; only the server clock does."""
    headers = {"x-snaplist-user-id": str(USER_A)}
    for _ in range(EVENT_BURST_CAPACITY):
        assert _post(rl_client, headers, _event_body()).status_code == 202
    assert _post(rl_client, headers, _event_body()).status_code == STATUS_TOO_MANY_REQUESTS
    # a far-future client_ts does NOT buy a token
    future = _event_body()
    future["client_ts"] = "2099-01-01T00:00:00Z"
    assert _post(rl_client, headers, future).status_code == STATUS_TOO_MANY_REQUESTS
    # only advancing the server (limiter) clock restores capacity
    fake_clock.t += EVENT_REFILL_SECONDS
    assert _post(rl_client, headers, _event_body()).status_code == 202


def test_429_uses_invalid_request_code_and_emits_retry_after(
    rl_client: TestClient, fake_clock: _FakeClock
) -> None:
    """Decision pin: 429 reuses `invalid_request` (NOT a new code, NOT `quota_exceeded`)."""
    headers = {"x-snaplist-user-id": str(USER_A)}
    for _ in range(EVENT_BURST_CAPACITY):
        assert _post(rl_client, headers, _event_body()).status_code == 202
    response = _post(rl_client, headers, _event_body())
    assert response.status_code == STATUS_TOO_MANY_REQUESTS

    body = response.json()
    assert body["error"]["code"] == "invalid_request"  # reuse, not a new code / quota_exceeded
    assert "retry_after_seconds" in body["error"]
    assert body["error"]["retry_after_seconds"] is not None
    assert body["error"]["retry_after_seconds"] == EVENT_REFILL_SECONDS
    # the header is a string form of the same number
    assert response.headers.get("retry-after") == str(float(EVENT_REFILL_SECONDS))
    assert body["error"]["details"]["limit"] == str(EVENT_BURST_CAPACITY)
    assert body["error"]["details"]["window_seconds"] == str(
        int(EVENT_REFILL_SECONDS * EVENT_BURST_CAPACITY)
    )
