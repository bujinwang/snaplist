"""Ops surface and the error-envelope guarantee.

`/health` must stay dependency-free (a container probe has to pass before any credential exists),
`/readyz` must report only credential *names*, and every non-2xx from any route must speak the one
envelope in docs/CONTRACTS.md §4 — including FastAPI's own 404 for an unknown path.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

ENVELOPE_KEYS = {"code", "message", "retry_after_seconds", "details"}


def test_health_is_200_and_credential_free(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["contract_version"] == "1.1.0"


def test_readyz_lists_missing_credentials_without_values(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["missing_credentials"], list)
    assert "supabase_service_key" in body["missing_credentials"] or body["ready"] is True
    # No secret material may ever appear in this payload: the list holds names, not values.
    for credential_name in body["missing_credentials"]:
        assert credential_name.islower(), f"{credential_name!r} does not look like a config name"
    for key, value in body.items():
        assert not (isinstance(value, str) and len(value) > 60), f"{key} looks like a secret"


def test_readyz_reports_repository_and_storage_as_separate_facts(
    client: TestClient,
) -> None:
    """Before E5-T4 the single `storage` key said `SqliteRepository` — a field that read like
    an answer and was not one. The value must match whichever backend is actually wired in."""
    body = client.get("/readyz").json()
    expected_repo = type(client.app.state.repository).__name__
    assert body["repository"] == expected_repo, (
        f"/readyz must report the actually-wired repository, got {body['repository']!r}, "
        f"expected {expected_repo!r}"
    )
    assert expected_repo in ("SqliteRepository", "PostgresRepository")
    assert body["storage"] == "LocalStorage", "an empty .env must land on the filesystem backend"
    assert body["storage_configured"] is True
    assert body["repository"] != body["storage"]


def test_an_unknown_path_uses_the_error_envelope(client: TestClient) -> None:
    response = client.get("/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) <= ENVELOPE_KEYS
    assert body["error"]["code"] == "not_found"


def test_unauthenticated_v1_calls_use_the_error_envelope(client: TestClient) -> None:
    for path in ("/v1/usage", "/v1/items/00000000-0000-4000-8000-000000000000", "/v1/jobs/x"):
        response = client.get(path)
        assert response.status_code == 401, (path, response.text)
        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) <= ENVELOPE_KEYS


def test_credential_blocked_routes_answer_501_with_the_same_envelope(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    response = client.post(
        "/v1/checkout",
        headers=headers_a,
        json={"plan": "pro", "success_url": "https://a", "cancel_url": "https://b"},
    )
    assert response.status_code == 501, response.text
    body = response.json()
    assert body["error"]["code"] == "not_implemented"
    assert body["error"]["details"]["missing_credential"] == "stripe_secret"


def test_the_stripe_webhook_is_503_without_secret(client: TestClient) -> None:
    # Default settings have no STRIPE_WEBHOOK_SECRET, so the receiver is disabled (not faked).
    response = client.post("/v1/webhooks/stripe", content=b"{}")
    assert response.status_code == 503
    # 503 + a named missing credential: an unauthenticated surface with no side effects is the
    # safe order, and BLOCKED.md documents the Stripe-key requirement.
    assert response.json()["error"]["details"]["missing_credential"] == "stripe_webhook_secret"


def test_no_route_returns_fastapi_default_detail_bodies(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    """A `{"detail": ...}` body would be a second error contract for the mobile client."""
    probes = [
        client.get("/v1/nope"),
        client.get("/v1/items/nope", headers=headers_a),
        client.post("/v1/items", headers={**headers_a, "idempotency-key": "x"}, json={}),
        client.patch(
            "/v1/items/00000000-0000-4000-8000-000000000000/attributes",
            headers=headers_a,
            json={"category": "nope", "colors": []},
        ),
    ]
    for response in probes:
        assert "detail" not in response.json(), response.text
        assert set(response.json()) == {"error"}, response.text
