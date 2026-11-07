"""E8-T2 Stripe webhook receiver — signature verification, idempotency, and plan updates.

Every test signs its own events with HMAC-SHA256 using a **known, local test secret** (the exact
Stripe `t=<ts>,v1=<sig>` scheme). No live Stripe account, key, or network call is ever used: the
verifier in `apps/api/routers/webhooks.py` is pure local crypto, so these tests exercise the real
code path honestly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.repository import SqliteRepository
from apps.api.settings import Settings

TEST_SECRET = "whsec_test_known_secret"
USER = UUID("33333333-3333-4333-8333-333333333333")
USER_HEADERS = {"x-snaplist-user-id": str(USER)}


@pytest.fixture
def webhook_client() -> TestClient:
    """An app whose webhook secret is configured — the dangerous configuration to defend."""
    settings = Settings(env="local", supabase_jwt_secret="", stripe_webhook_secret=TEST_SECRET)
    repository = SqliteRepository(":memory:")
    app = create_app(repository=repository, settings=settings)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        repository.close()


@pytest.fixture
def no_secret_client() -> TestClient:
    """Default settings: no Stripe secret, so the webhook must refuse with 503."""
    settings = Settings(env="local", supabase_jwt_secret="")
    repository = SqliteRepository(":memory:")
    app = create_app(repository=repository, settings=settings)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        repository.close()


def _sign(payload: bytes, *, timestamp: int | None = None) -> str:
    """Produce a `Stripe-Signature` header for `payload` using the known test secret."""
    stamp = timestamp if timestamp is not None else int(time.time())
    signed = f"{stamp}.".encode("ascii") + payload
    digest = hmac.new(TEST_SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _checkout_completed(*, user_id: UUID, plan: str, event_id: str | None = None) -> bytes:
    event = {
        "id": event_id or f"evt_{uuid4().hex}",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test",
                "client_reference_id": str(user_id),
                "metadata": {"user_id": str(user_id), "plan": plan},
            }
        },
    }
    return json.dumps(event).encode("utf-8")


def _subscription_deleted(*, user_id: UUID, event_id: str | None = None) -> bytes:
    event = {
        "id": event_id or f"evt_{uuid4().hex}",
        "type": "customer.subscription.deleted",
        "data": {"object": {"id": "sub_test", "metadata": {"user_id": str(user_id)}}},
    }
    return json.dumps(event).encode("utf-8")


def test_missing_secret_returns_503_and_names_credential(no_secret_client: TestClient) -> None:
    response = no_secret_client.post("/v1/webhooks/stripe", content=b"{}")
    assert response.status_code == 503, response.text
    assert response.json()["error"]["details"]["missing_credential"] == "stripe_webhook_secret"


def test_signed_checkout_grants_plan_and_updates_quota(webhook_client: TestClient) -> None:
    payload = _checkout_completed(user_id=USER, plan="pro")
    response = webhook_client.post(
        "/v1/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": _sign(payload)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"] == "pro"
    # The grant is readable through the quota surface: the plan drives the ceiling.
    usage = webhook_client.get("/v1/usage", headers=USER_HEADERS).json()
    assert usage["plan"] == "pro"


def test_idempotent_replay_is_a_noop(webhook_client: TestClient) -> None:
    payload = _checkout_completed(user_id=USER, plan="pro")
    first = webhook_client.post(
        "/v1/webhooks/stripe", content=payload, headers={"stripe-signature": _sign(payload)}
    )
    assert first.status_code == 200
    assert first.json().get("replayed") is None
    second = webhook_client.post(
        "/v1/webhooks/stripe", content=payload, headers={"stripe-signature": _sign(payload)}
    )
    assert second.status_code == 200
    assert second.json().get("replayed") is True


def test_bad_signature_is_400_and_grants_nothing(webhook_client: TestClient) -> None:
    payload = _checkout_completed(user_id=USER, plan="pro")
    response = webhook_client.post(
        "/v1/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_request"
    usage = webhook_client.get("/v1/usage", headers=USER_HEADERS).json()
    assert usage["plan"] == "free"


def test_subscription_deleted_downgrades_to_free(webhook_client: TestClient) -> None:
    grant = _checkout_completed(user_id=USER, plan="pro", event_id="evt_grant")
    webhook_client.post(
        "/v1/webhooks/stripe", content=grant, headers={"stripe-signature": _sign(grant)}
    )
    assert webhook_client.get("/v1/usage", headers=USER_HEADERS).json()["plan"] == "pro"

    cancel = _subscription_deleted(user_id=USER, event_id="evt_cancel")
    response = webhook_client.post(
        "/v1/webhooks/stripe", content=cancel, headers={"stripe-signature": _sign(cancel)}
    )
    assert response.status_code == 200, response.text
    assert response.json()["plan"] == "free"
    assert webhook_client.get("/v1/usage", headers=USER_HEADERS).json()["plan"] == "free"
