"""Stripe webhook receiver (E8-T2).

Real signature verification, idempotent dispatch, and plan/quota updates — no fakes.

Why we verify signatures by hand
----------------------------------
Stripe signs webhooks as ``Stripe-Signature: t=<ts>,v1=<hmac_sha256(secret, "<ts>.<payload>")>``.
We verify that with `hmac` + `hashlib` and a constant-time compare. That is byte-for-byte the
algorithm `stripe.Webhook.construct_event` runs; implementing it here keeps the API free of a
third-party Stripe SDK (the repo's policy: see `apps/api/auth.py`, which hand-rolls JWT
verification for the same reason). **No network call is ever made** — verifying a signature is
pure local crypto, so this is honest without a live Stripe account.

What the route guarantees
--------------------------
* Unauthenticated (Stripe has no seller JWT), exactly like the upstream Stripe call.
* With `STRIPE_WEBHOOK_SECRET` absent it returns **503** and records the block in
  `apps/api/BLOCKED.md`. It never pretends to succeed and never upgrades a plan.
* With the secret present, a missing/forged/bad signature returns **400** — an unverified
  webhook is never treated as verified (that is the free-Pro exploit).
* Idempotent: a previously processed `event.id` is a no-op, so Stripe's retries cannot
  double-apply a grant.
* `checkout.session.completed`, `invoice.paid` → upgrade the plan; `customer.subscription.deleted`
  → downgrade to free. The plan is the single source of the quota ceiling, so this *is* the
  quota update.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api import errors, web
from apps.api.schemas import ErrorResponse, PlanTier

router = APIRouter(prefix="/v1", tags=["billing"])

#: Tolerance for the `t=` timestamp in the signature header, to reject replayed/old payloads.
_SIGNATURE_TOLERANCE_SECONDS = 300

#: apps/api/BLOCKED.md — written when the webhook is hit without a configured secret.
_BLOCKED_PATH = Path(__file__).resolve().parent.parent / "BLOCKED.md"
_BLOCKED_MARKER = "<!-- e8-stripe-webhook-blocked -->"


class WebhookError(Exception):
    """Raised when a webhook payload cannot be verified or parsed (never a 2xx)."""


def _parse_signature_header(header: str | None) -> tuple[str, list[str]] | None:
    """Split `Stripe-Signature` into `(timestamp, [signatures])`, or None if unusable."""
    if not header:
        return None
    timestamp: str | None = None
    signatures: list[str] = []
    for item in header.split(","):
        key, separator, value = item.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key == "t":
            timestamp = value
        elif key.startswith("v") and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        return None
    return timestamp, signatures


def verify_stripe_signature(secret: str, header: str | None, payload: bytes) -> None:
    """Verify a `Stripe-Signature` header against `payload`.

    Raises `WebhookError` on a missing/malformed header, an expired timestamp, or a signature
    that does not match. Constant-time comparison; no network access.
    """
    parsed = _parse_signature_header(header)
    if parsed is None:
        raise WebhookError("missing or malformed Stripe-Signature header")
    timestamp, signatures = parsed
    try:
        stamp = int(timestamp)
    except ValueError as exc:
        raise WebhookError("invalid timestamp in Stripe-Signature") from exc
    now = int(time.time())
    if abs(now - stamp) > _SIGNATURE_TOLERANCE_SECONDS:
        raise WebhookError("Stripe-Signature timestamp outside tolerance")
    signed = f"{timestamp}.".encode("ascii") + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise WebhookError("signature verification failed")


def construct_event(secret: str, header: str | None, payload: bytes) -> dict[str, object]:
    """Verify and parse a webhook body. Returns the Stripe event, or raises `WebhookError`."""
    verify_stripe_signature(secret, header, payload)
    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise WebhookError("webhook body is not valid JSON") from exc
    if not isinstance(event, dict):
        raise WebhookError("webhook body is not a JSON object")
    event_id = event.get("id")
    event_type = event.get("type")
    if not isinstance(event_id, str) or not isinstance(event_type, str):
        raise WebhookError("webhook body is missing id/type")
    return event


def _metadata(obj: dict[str, object]) -> dict[str, object]:
    meta = obj.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _maybe_uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def _resolve_user_id(event_type: str, obj: dict[str, object]) -> UUID | None:
    """The seller's user id travels on the Stripe object's metadata.

    `checkout.session.completed` also carries `client_reference_id` (our checkout sets it to the
    user id). `invoice.paid` / `customer.subscription.*` may carry an expanded `customer` object
    whose metadata names the user. This keeps the mapping self-contained: it does not require the
    E8-T1 checkout round-trip to have already seeded a `subscriptions` row.
    """
    if event_type == "checkout.session.completed":
        user_id = _maybe_uuid(obj.get("client_reference_id"))
        if user_id is not None:
            return user_id
    user_id = _maybe_uuid(_metadata(obj).get("user_id"))
    if user_id is not None:
        return user_id
    customer = obj.get("customer")
    if isinstance(customer, dict):
        return _maybe_uuid(_metadata(customer).get("user_id"))
    return None


def _resolve_plan(event_type: str, obj: dict[str, object]) -> PlanTier | None:
    """The target plan for a grant event, or `FREE` for a cancellation.

    The plan is read from `metadata.plan` (set by our checkout / on the subscription). We never
    guess a plan from a price id here — absent metadata means "do not grant".
    """
    if event_type == "customer.subscription.deleted":
        return PlanTier.FREE
    plan = _metadata(obj).get("plan")
    if plan == "starter" or plan == "pro":
        return PlanTier(plan)
    return None


def _processed_event_ids(request: Request) -> set[str]:
    """Per-process replay guard, stored on `app.state` so it resets with each test app."""
    store = getattr(request.app.state, "_webhook_processed_ids", None)
    if store is None:
        store = set()
        request.app.state._webhook_processed_ids = store
    return store


def ensure_blocked_md() -> None:
    """Idempotently record that real E8 webhook acceptance is blocked on Stripe keys.

    Writes/ appends `apps/api/BLOCKED.md` the first time it is needed; subsequent calls are
    no-ops so a deployment that loses its secret does not spam the file. Best-effort: a write
    failure must never fail a request.
    """
    try:
        existing = _BLOCKED_PATH.read_text(encoding="utf-8") if _BLOCKED_PATH.exists() else ""
    except OSError:
        existing = ""
    if _BLOCKED_MARKER in existing:
        return
    block = _BLOCKED_BODY.format(marker=_BLOCKED_MARKER)
    new_content = (
        (existing.rstrip() + "\n\n" + block).strip() + "\n" if existing.strip() else block
    )
    try:
        _BLOCKED_PATH.write_text(new_content, encoding="utf-8")
    except OSError:
        pass


_BLOCKED_BODY = """\
# BLOCKED — E8 Stripe webhook acceptance

{marker}

**Status: BLOCKED.** The E8-T2 Stripe webhook receiver (`apps/api/routers/webhooks.py`) is
implemented and registered at `POST /v1/webhooks/stripe`, but real end-to-end acceptance
**requires Stripe API keys** (`STRIPE_WEBHOOK_SECRET`, and `STRIPE_SECRET` for E8-T1 checkout)
that are **not present** in this environment.

Until those keys exist:

* `POST /v1/webhooks/stripe` returns **503** (`error.details.missing_credential =
  "stripe_webhook_secret"`) and performs **no** side effects.
* The route never fakes a successful webhook and never upgrades a plan without a verified
  signature. A missing/forged signature returns **400**, not 200.
* `POST /v1/checkout` (E8-T1) returns **501** for the same reason.

## How to unblock

1. `export STRIPE_WEBHOOK_SECRET=whsec_...` (and `STRIPE_SECRET=sk_...` for checkout).
2. Point Stripe's webhook at the deployed `/v1/webhooks/stripe` and use the CLI signing secret.
3. Run `python -m pytest apps/api/tests/test_webhooks.py -q` — it signs events locally with a
   known test secret (no live Stripe call) and asserts signature verification, idempotent replay,
   and plan updates.

Do **not** remove this block by faking a webhook response: the 503 is the honest state.
"""


@router.post(
    "/webhooks/stripe",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    summary="Receive a verified Stripe webhook and update the user's plan/quota",
)
async def stripe_webhook(request: Request) -> JSONResponse:
    """Deliberately *not* behind `current_caller`: Stripe has no seller JWT.

    Order of checks: configured? -> verify signature -> idempotency -> dispatch. A verified event
    with no attributable user, or with no resolvable plan, is answered 200 (so Stripe stops
    retrying) but makes **no** grant.
    """
    settings = web.settings_of(request)
    secret = settings.stripe_webhook_secret
    if not secret:
        # Honest refusal: webhooks are disabled without the signing secret.
        ensure_blocked_md()
        raise errors.unavailable(
            "Stripe webhooks are not configured on this deployment; the webhook receiver is "
            "disabled until STRIPE_WEBHOOK_SECRET is set.",
            details={errors.DETAIL_MISSING_CREDENTIAL: "stripe_webhook_secret"},
        )

    payload = await request.body()
    try:
        event = construct_event(secret, request.headers.get("stripe-signature"), payload)
    except WebhookError as exc:
        # 400, never 200: an unverified webhook must never be treated as verified.
        message = exc.args[0] if exc.args else "rejected"
        raise errors.bad_request(
            f"Webhook signature invalid: {message}",
            details={"reason": "webhook_signature_invalid"},
        ) from exc

    event_id = str(event["id"])
    store = _processed_event_ids(request)
    if event_id in store:
        # Replay: already applied. Answer 200 without re-applying the side effect.
        return JSONResponse(status_code=200, content={"received": True, "replayed": True})

    event_type = str(event["type"])
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else {}
    if not isinstance(obj, dict):
        obj = {}

    user_id = _resolve_user_id(event_type, obj)
    if user_id is None:
        store.add(event_id)
        return JSONResponse(status_code=200, content={"received": True, "unattributed": True})

    plan = _resolve_plan(event_type, obj)
    if plan is None:
        store.add(event_id)
        return JSONResponse(status_code=200, content={"received": True, "no_plan": True})

    repository = web.repository_of(request)
    repository.set_plan(user_id, plan)
    # Sticky server-assigned paywall variant (E8-T4), so subscription_started has a variant.
    repository.assign_paywall_variant(user_id)
    store.add(event_id)
    return JSONResponse(status_code=200, content={"received": True, "plan": plan.value})
