# BLOCKED — E8 Stripe webhook acceptance

<!-- e8-stripe-webhook-blocked -->

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
