"""Identity resolution: the two modes, and what each one actually promises.

The tests are written so the *claims* in `apps/api/auth.py` are checkable:

* local dev: a self-declared uuid, refused in production
* JWT: a real HS256 signature check — a tampered token, an expired token and `alg: none` are all
  rejected, which is the part that matters
* unconfigured in production: 503, not an open door

The tokens here are generated with `hmac` exactly like a Supabase project would sign them, so this
proves the verification code, not a mock of it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.repository import SqliteRepository
from apps.api.settings import Settings

SECRET = "test-jwt-secret-do-not-use-anywhere"
USER = UUID("11111111-1111-4111-8111-111111111111")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_token(
    secret: str = SECRET,
    *,
    subject: str = str(USER),
    expires_in: int = 3600,
    algorithm: str = "HS256",
    tamper: bool = False,
) -> str:
    header = _b64url(json.dumps({"alg": algorithm, "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "sub": subject,
                "role": "authenticated",
                "iat": int(time.time()) - 10,
                "exp": int(time.time()) + expires_in,
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if tamper:
        signature = bytes(byte ^ 0xFF for byte in signature)
    return f"{header}.{payload}.{_b64url(signature)}"


@pytest.fixture
def jwt_app():
    settings = Settings(env="staging", supabase_jwt_secret=SECRET)
    repository = SqliteRepository(":memory:")
    try:
        yield create_app(repository=repository, settings=settings)
    finally:
        repository.close()


@pytest.fixture
def jwt_client(jwt_app) -> TestClient:
    return TestClient(jwt_app)


# ---------------------------------------------------------------------------------------
# local dev mode
# ---------------------------------------------------------------------------------------
def test_local_dev_without_the_header_is_401(client: TestClient) -> None:
    response = client.get("/v1/usage")
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["error"]["code"] == "unauthorized"
    assert "X-SnapList-User-Id" in body["error"]["message"]


def test_local_dev_with_a_malformed_header_is_401(client: TestClient) -> None:
    response = client.get("/v1/usage", headers={"x-snaplist-user-id": "me"})
    assert response.status_code == 401
    assert "uuid" in response.json()["error"]["message"]


def test_local_dev_accepts_a_uuid_and_labels_the_response(
    client: TestClient, headers_a: dict[str, str]
) -> None:
    response = client.get("/v1/usage", headers=headers_a)
    assert response.status_code == 200
    assert response.headers["x-snaplist-auth-mode"] == "local_dev"


def test_readyz_reports_the_unverified_identity(client: TestClient) -> None:
    body = client.get("/readyz").json()
    assert body["auth_mode"] == "local_dev"
    assert body["unverified_identity"] is True
    # names only, never values
    assert "supabase_service_key" in body["missing_credentials"]


# ---------------------------------------------------------------------------------------
# JWT mode
# ---------------------------------------------------------------------------------------
def test_a_valid_hs256_token_is_accepted(jwt_client: TestClient) -> None:
    response = jwt_client.get("/v1/usage", headers={"authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200, response.text
    assert response.headers["x-snaplist-auth-mode"] == "supabase_jwt"
    assert response.json()["plan"] == "free"


def test_a_tampered_signature_is_401(jwt_client: TestClient) -> None:
    token = make_token(tamper=True)
    response = jwt_client.get("/v1/usage", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_a_token_signed_with_the_wrong_secret_is_401(jwt_client: TestClient) -> None:
    token = make_token(secret="some-other-secret")
    response = jwt_client.get("/v1/usage", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_an_expired_token_is_401(jwt_client: TestClient) -> None:
    token = make_token(expires_in=-3600)
    response = jwt_client.get("/v1/usage", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"].lower()


def test_alg_none_is_refused_with_501_not_accepted(jwt_client: TestClient) -> None:
    token = make_token(algorithm="none")
    response = jwt_client.get("/v1/usage", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 501, "an unverifiable algorithm must never fall through"
    assert response.json()["error"]["code"] == "not_implemented"


def test_the_local_dev_header_is_ignored_when_a_jwt_secret_exists(jwt_client: TestClient) -> None:
    response = jwt_client.get("/v1/usage", headers={"x-snaplist-user-id": str(USER)})
    assert response.status_code == 401, "a self-declared uuid must not work in JWT mode"


def test_a_non_bearer_authorization_header_is_401(jwt_client: TestClient) -> None:
    response = jwt_client.get("/v1/usage", headers={"authorization": "Basic abc"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------------------
# unconfigured
# ---------------------------------------------------------------------------------------
def test_production_without_a_secret_is_503() -> None:
    settings = Settings(env="production", supabase_jwt_secret="")
    repository = SqliteRepository(":memory:")
    try:
        app = create_app(repository=repository, settings=settings)
        client = TestClient(app)
        response = client.get("/v1/usage", headers={"x-snaplist-user-id": str(USER)})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "internal"
        # The refusal names the missing configuration, and nothing else.
        assert "SUPABASE_JWT_SECRET" in response.json()["error"]["message"]
    finally:
        repository.close()
