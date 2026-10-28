"""Caller identity.

Two modes, and the difference matters to anyone reading a security claim in a doc:

`supabase_jwt`
    Real verification. An `Authorization: Bearer <jwt>` is parsed, its HS256 signature is
    checked against `SUPABASE_JWT_SECRET` with `hmac.compare_digest`, `exp`/`nbf` are
    enforced, and `sub` must be a uuid. This is implemented with the standard library
    (`hmac`/`hashlib`/`base64`/`json`) because `pyjwt` is not a dependency of this repo and a
    JWT decoder is 40 lines of code that we can read. **Asymmetric Supabase keys (ES256 /
    RS256 via JWKS) are not verified here** — that path returns 501 instead of silently
    accepting an unverified token.

`local_dev`
    Only when `SUPABASE_JWT_SECRET` is empty *and* `ENV` is local/test/development. The
    caller identifies itself with `X-SnapList-User-Id: <uuid>`. This is **not
    authentication**: it is a development stand-in so the vertical slice and its
    cross-user-isolation tests can run with zero credentials. Every `/v1` response carries
    `x-snaplist-auth-mode: local_dev` so a client cannot mistake one for the other, and the
    header is refused outright the moment a JWT secret exists or ENV is production.

There is no third mode. A production deployment with no secret gets 503 on every `/v1`
route — a misconfiguration must be loud, not an open door.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from uuid import UUID

from fastapi import Request

from apps.api import errors
from apps.api.settings import Settings, get_settings

LOCAL_DEV_HEADER = "x-snaplist-user-id"
AUTH_MODE_HEADER = "x-snaplist-auth-mode"

# Local environments where the unverified header is allowed. Everything else must have a
# real secret. `staging` and `production` are deliberately absent.
LOCAL_ENVS = frozenset({"local", "test", "development"})

# 30 seconds of clock skew tolerance, the usual allowance for a laptop that just woke up.
CLOCK_SKEW_SECONDS = 30


class AuthMode(StrEnum):
    SUPABASE_JWT = "supabase_jwt"
    LOCAL_DEV = "local_dev"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is asking. `verified` is False only in local dev."""

    user_id: UUID
    mode: AuthMode
    verified: bool


def resolve_auth_mode(settings: Settings) -> AuthMode:
    if settings.supabase_jwt_secret:
        return AuthMode.SUPABASE_JWT
    if settings.env.lower() in LOCAL_ENVS:
        return AuthMode.LOCAL_DEV
    return AuthMode.UNCONFIGURED


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _split_jwt(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise errors.unauthorized("Authorization header is not a JWT (expected 3 segments).")
    return parts[0], parts[1], parts[2]


def verify_hs256_jwt(token: str, secret: str, *, now: float | None = None) -> dict[str, object]:
    """Verify a Supabase HS256 access token and return its claims.

    Raises `ApiError(401)` for anything that is not a live, correctly signed token. Kept
    deliberately strict: an unverified claim is worse than a rejected request.
    """
    header_b64, payload_b64, signature_b64 = _split_jwt(token)
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise errors.unauthorized("Authorization header is not decodable as a JWT.") from exc

    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise errors.unauthorized("JWT header and payload must be JSON objects.")

    algorithm = header.get("alg")
    if algorithm != "HS256":
        # Asymmetric verification needs a JWKS fetch and an RSA/ECDSA primitive. Refusing is
        # honest; `alg: none` and `alg: RS256` must never fall through to "accept".
        raise errors.not_implemented(
            f"Unsupported JWT algorithm {algorithm!r}. This build verifies HS256 only; "
            "configure a Supabase legacy JWT secret or implement JWKS verification (E5-T5)."
        )

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{header_b64}.{payload_b64}".encode("ascii"),
        sha256,
    ).digest()
    if not hmac.compare_digest(expected, signature):
        raise errors.unauthorized("JWT signature is not valid.")

    current = time.time() if now is None else now
    expiry = payload.get("exp")
    if not isinstance(expiry, int | float):
        raise errors.unauthorized("JWT has no `exp` claim; refusing an immortal token.")
    if current > float(expiry) + CLOCK_SKEW_SECONDS:
        raise errors.unauthorized("JWT has expired.")
    not_before = payload.get("nbf")
    if isinstance(not_before, int | float) and current + CLOCK_SKEW_SECONDS < float(not_before):
        raise errors.unauthorized("JWT is not valid yet (`nbf`).")

    return payload


def _caller_from_jwt(authorization: str | None, secret: str) -> Caller:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise errors.unauthorized(
            "Missing `Authorization: Bearer <supabase access token>` header."
        )
    claims = verify_hs256_jwt(authorization.split(" ", 1)[1].strip(), secret)
    subject = claims.get("sub")
    try:
        user_id = UUID(str(subject))
    except (TypeError, ValueError) as exc:
        raise errors.unauthorized("JWT `sub` claim is not a uuid.") from exc
    return Caller(user_id=user_id, mode=AuthMode.SUPABASE_JWT, verified=True)


def _caller_from_local_header(local_user: str | None) -> Caller:
    if not local_user:
        raise errors.unauthorized(
            "Local dev auth: send `X-SnapList-User-Id: <uuid>`. No JWT secret is configured, "
            "so this header stands in for a verified Supabase token and is refused in "
            "production."
        )
    try:
        user_id = UUID(local_user.strip())
    except ValueError as exc:
        raise errors.unauthorized("`X-SnapList-User-Id` must be a uuid.") from exc
    return Caller(user_id=user_id, mode=AuthMode.LOCAL_DEV, verified=False)


def resolve_caller(headers: dict[str, str], settings: Settings) -> Caller:
    """Pure function: headers + settings -> Caller. Easy to unit-test, no FastAPI magic."""
    mode = resolve_auth_mode(settings)
    if mode is AuthMode.SUPABASE_JWT:
        return _caller_from_jwt(headers.get("authorization"), settings.supabase_jwt_secret)
    if mode is AuthMode.LOCAL_DEV:
        return _caller_from_local_header(headers.get(LOCAL_DEV_HEADER))
    raise errors.unavailable(
        "Authentication is not configured: set SUPABASE_JWT_SECRET (see docs/RUNBOOK.md §7).",
        details={errors.DETAIL_MISSING_CREDENTIAL: "supabase_jwt_secret"},
    )


def current_caller(request: Request) -> Caller:
    """FastAPI dependency. Reads settings and storage off `app.state` so tests can swap them."""
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return resolve_caller(
        {key.lower(): value for key, value in request.headers.items()},
        settings,
    )
