"""Stripe checkout surface (E8-T1) — blocked, and 501 on purpose.

A checkout session needs `STRIPE_SECRET`; the webhook receiver (E8-T2) lives in
`apps/api/routers/webhooks.py` and needs `STRIPE_WEBHOOK_SECRET` **and** a signature
verification we can actually test. There is no acceptable middle ground here:

* returning a fake `checkout_url` would send a seller to a 404 and lose the conversion;
* a webhook without verifying the signature would let anyone grant themselves Pro with a single
  unauthenticated POST — a free-subscription bug, which is the worst kind.

So the checkout route exists (the shape is fixed, the mobile/web clients can code against it) and
answers 501 with the missing credential named in `details`. `require_stripe` is the only place
that decides, so the moment the key exists the route can be wired without touching the rest of the
API.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from apps.api import errors, web
from apps.api.auth import Caller, current_caller
from apps.api.schemas import CheckoutRequest, ErrorResponse

router = APIRouter(prefix="/v1", tags=["billing"])


def require_stripe(request: Request, *, needs_webhook: bool = False) -> NoReturn:
    """Always raises. Names the missing secret so the fix is a config change, not a bug hunt."""
    settings = web.settings_of(request)
    if needs_webhook and not settings.stripe_webhook_secret:
        missing = "stripe_webhook_secret"
    elif not settings.stripe_secret:
        missing = "stripe_secret"
    else:
        missing = None

    if missing is not None:
        raise errors.ApiError(
            errors.STATUS_NOT_IMPLEMENTED,
            errors.ErrorCode.NOT_IMPLEMENTED,
            "Stripe is not configured on this deployment; billing endpoints are disabled.",
            details={"missing_credential": missing, "ticket": "E8-T1"},
        )
    raise errors.not_implemented(
        "Stripe credentials are present but the checkout flow (E8-T1) is not implemented yet."
    )


@router.post(
    "/checkout",
    responses={501: {"model": ErrorResponse}},
    summary="Create a Stripe Checkout session (blocked: Stripe credentials)",
)
def create_checkout(
    body: CheckoutRequest,
    request: Request,
    caller: Caller = Depends(current_caller),
) -> JSONResponse:
    """E8-T1. `body`/`caller` are taken so the signature is final and the client can code
    against it; the handler then refuses rather than pretending."""
    require_stripe(request)
