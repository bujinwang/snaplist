"""The other half of the Stripe refusal: what happens when the credentials *are* configured.

`test_scaffold.py` proves the missing-credential branch — 501 naming `stripe_secret`. But the
branch that runs when `STRIPE_SECRET` and `STRIPE_WEBHOOK_SECRET` are both set is the one that
would ship a free-subscription bug, and it had no test at all:

    if both secrets are present and the code returns a fake checkout_url, or accepts an
    unsigned webhook as verified, every test written so far stays green.

So these tests configure a fictional key pair and assert the endpoint still refuses, with no
success-shaped payload anywhere. A 2xx here would be a bug, not a feature.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.repository import SqliteRepository
from apps.api.settings import Settings

USER = UUID("11111111-1111-4111-8111-111111111111")
HEADERS = {"x-snaplist-user-id": str(USER)}

CHECKOUT_BODY = {
    "plan": "pro",
    "success_url": "https://example.test/ok",
    "cancel_url": "https://example.test/cancel",
}


@pytest.fixture
def configured_client() -> TestClient:
    """An app whose Stripe credentials are both present — the dangerous configuration."""
    settings = Settings(
        env="local",
        supabase_jwt_secret="",
        stripe_secret="sk_test_qa_configured",
        stripe_webhook_secret="whsec_qa_configured",
    )
    repository = SqliteRepository(":memory:")
    app = create_app(repository=repository, settings=settings)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        repository.close()


def test_checkout_still_refuses_when_credentials_exist(configured_client: TestClient) -> None:
    response = configured_client.post("/v1/checkout", headers=HEADERS, json=CHECKOUT_BODY)
    assert response.status_code == 501, (
        f"checkout must not pretend with a real secret configured: {response.text}"
    )
    body = response.json()
    assert body["error"]["code"] == "not_implemented"
    # No success-shaped payload may leak out of a refusal.
    assert "checkout_url" not in body
    assert "session_id" not in body


def test_an_unsigned_webhook_is_never_accepted(configured_client: TestClient) -> None:
    """A webhook without a valid `Stripe-Signature` is the free-Pro exploit path."""
    response = configured_client.post(
        "/v1/webhooks/stripe",
        content=b'{"type":"checkout.session.completed","data":{"object":{"id":"cs_test"}}}',
    )
    assert response.status_code == 501
    assert response.status_code < 200 or response.status_code >= 300
    assert response.json()["error"]["code"] == "not_implemented"


def test_a_forged_signature_header_is_never_accepted(configured_client: TestClient) -> None:
    """Present-but-wrong signature must not be treated as 'verified'."""
    response = configured_client.post(
        "/v1/webhooks/stripe",
        content=b'{"type":"invoice.paid"}',
        headers={"stripe-signature": "t=1,v1=deadbeef"},
    )
    assert response.status_code == 501, (
        "a forged signature must never fall through to a success path: " + response.text
    )


def test_a_refused_webhook_grants_no_plan_change(configured_client: TestClient) -> None:
    """The side effect, not the status code: the caller's plan is untouched."""
    before = configured_client.get("/v1/usage", headers=HEADERS).json()["plan"]
    configured_client.post(
        "/v1/webhooks/stripe",
        content=b'{"type":"checkout.session.completed"}',
    )
    after = configured_client.get("/v1/usage", headers=HEADERS).json()["plan"]
    assert (before, after) == ("free", "free")
