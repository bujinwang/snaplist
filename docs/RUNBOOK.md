# RUNBOOK — credentials, environment, and what each one unblocks

Read this before you run anything that talks to a network. Read the bottom half before you
report anything as working.

## 0. The short version

```bash
cd /Users/bujin/Documents/Projects/SnapList
make install                 # no credentials needed
make test                    # no credentials needed
make lint                    # no credentials needed
make evals                   # no credentials needed (prints UNMEASURED)
make dev                     # /health works without credentials; /v1 does not exist yet
```

**Only these four commands are credential-free.** Everything from E5 onward needs at least
Supabase. If a task's acceptance command needs a credential you do not have, the correct move is
`BLOCKED.md` with the verbatim error — not a stub, not a mocked response.

### Activate the venv before you type `python`

`make` targets call `.venv/bin/python` by absolute path, so they are safe. A bare `python` is
**not**: on a machine with several interpreters it resolves outside the venv and fails on
`import pydantic`. Activate first, then the commands in PLAN.md work verbatim:

```bash
source .venv/bin/activate      # python -> .venv/bin/python, 3.11.15
python -c "import apps.api.schemas as s; print(sorted(s.ItemAttributes.model_fields))"
```

`make contract` runs that same check without needing an activated shell.


## 1. Environment file

Copy the template, never commit the result:

```bash
cp .env.example .env       # .env is gitignored
```

`.env` is read by `apps/api/settings.py` (`pydantic-settings`, `env_file=".env"`). Names are
case-insensitive on purpose (`SUPABASE_URL` == `supabase_url`). No secret has a default value in
code — a missing secret yields `""`, and `/readyz` lists the names that are empty (never the
values).

Check what is missing at any time:

```bash
make dev &
curl -s localhost:8000/readyz | jq
# {"ready": false, "missing_credentials": ["supabase_url", "supabase_service_key", ...]}
```

## 2. Supabase — `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (+ `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`)

**Where to get it**

1. Create a project at <https://supabase.com/dashboard> (region: closest to the sellers, `us-east-1`
   or `eu-west-2`; moving later means a data migration).
2. Project settings → **API**:
   - `SUPABASE_URL` = Project URL (`https://<ref>.supabase.co`)
   - `SUPABASE_ANON_KEY` = `anon` / publishable key — **this one ships in the mobile bundle**
   - `SUPABASE_SERVICE_KEY` = `service_role` key — **server only, bypasses RLS, treat as a root password**
   - `SUPABASE_JWT_SECRET` = Project settings → API → JWT Settings → JWT Secret (used to verify
     bearer tokens locally without a round trip)
3. Project settings → **Auth → Providers**: enable Email (magic link) and Apple + Google. Apple
   needs an Apple Developer account (Services ID + key) — that is a multi-day lead time, start it
   the week E6-T1 starts, not the day before demo.
4. Storage → create a private bucket named `items`. Signed URLs only; no public bucket.

**Local alternative** (preferred for tests, so CI never touches a real project):

```bash
brew install supabase/tap/supabase
supabase start                 # prints the local URL + keys; put them in .env
supabase db reset              # applies infra/migrations/*.sql
```

**Unblocks:** E5-T1 … E5-T5 (schema, endpoints, storage, auth), E6-T1 (mobile sign-in),
E8-T3 (quota ledger), and every `curl` acceptance in PLAN.md. `/health` and `/readyz` work
without it; nothing that touches data does.

## 3. Stripe — `STRIPE_SECRET`, `STRIPE_WEBHOOK_SECRET`

**Where to get it**

1. <https://dashboard.stripe.com/test/apikeys> → copy the **test mode** secret key (`sk_test_…`).
   Never paste a live key into a dev `.env`.
2. Install the Stripe CLI and forward webhooks locally:
   ```bash
   brew install stripe/stripe-cli/stripe
   stripe login
   stripe listen --forward-to localhost:8000/v1/billing/webhook
   # prints: whsec_…   -> that is STRIPE_WEBHOOK_SECRET (a new one per `stripe listen` session)
   ```
3. Create the two prices in test mode and record their ids in `apps/api/routers/billing.py`
   config (E8-T1): Starter $19/100 items, Pro $49/400 items + on-model.
4. Test the four events by hand with `stripe trigger`:
   `checkout.session.completed`, `invoice.paid`, `customer.subscription.updated`,
   `customer.subscription.deleted`.

**Unblocks:** E8-T1 (checkout session), E8-T2 (webhook + idempotency), E7-T5 (the checkout link
on the landing page). E8-T3 (quota metering) can be built and tested **without** Stripe by
writing `subscriptions` rows directly — do that first so a missing key does not stall the epic.

## 4. FASHN — `FASHN_API_KEY`

**Where to get it**

1. <https://fashn.ai> → create an account → API keys.
2. Confirm in the dashboard, in writing (screenshot into `docs/DEPS.md` as a note), that the plan
   is the **commercial** VTON licence — the whole reason for choosing FASHN over a research
   checkpoint is the licence, so an unverified licence is a legal blocker, not a detail.
3. Rate limits and price per render must be recorded from the dashboard, then measured for real
   in E4-T1. `$/render` in `evals/reports/cost.md` must come from an observed call, never from
   the pricing page alone.

**Unblocks:** E4-T1 (primary adapter), E4-T3 (quality gate over real renders). The on-model
feature cannot be demoed at all without it; everything else in v1 can.

## 5. Modal — `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`

**Where to get it**

```bash
pip install modal
modal token new                # browser flow; or:
modal token set --token-id $MODAL_TOKEN_ID --token-secret $MODAL_TOKEN_SECRET
modal token current            # verify
```

**Unblocks:** E5-T3 (deploying the job worker), E4-T2 (CatVTON fallback). Without Modal you can
still develop and test the job runner locally (run the same function in-process against local
Postgres) — do that first so the epic is not serialised on a token.

## 6. Attributes VLM provider — `VLM_API_KEY` (name pending)

**Status: undecided.** BRIEF.md fixes "one VLM behind an adapter interface" but not which
provider. The CV role picks one in E3-T1 and records it, with its price and licence, in a new ADR
in `docs/` (next free number — 002–005 are taken) and in `docs/DEPS.md`. Until then, E3 cannot be
measured and the attribute accuracy numbers stay `UNMEASURED`.

**Do not** invent a provider name, a price or an accuracy figure here.

## 7. Blocking matrix

| credential | blocks | can be worked around locally? |
|---|---|---|
| `SUPABASE_URL` | E5-T1…T5, E6-T1, E8-T3, all curl acceptance | yes — `supabase start` |
| `SUPABASE_SERVICE_KEY` | E5-T2/T4/T5, E8-T2 | yes — local Supabase prints one |
| `SUPABASE_JWT_SECRET` | E5-T5 auth tests | yes — local Supabase |
| `SUPABASE_ANON_KEY` | E6-T1 mobile sign-in | yes — local Supabase |
| `STRIPE_SECRET` | E8-T1, E7-T5 | partly — E8-T3 does not need it |
| `STRIPE_WEBHOOK_SECRET` | E8-T2 | yes — `stripe listen` |
| `FASHN_API_KEY` | E4-T1, E4-T3, any on-model demo | **no** |
| `MODAL_TOKEN_ID/SECRET` | E5-T3 deploy, E4-T2 | partly — run the worker in-process |
| `VLM_API_KEY` | E3-T2, E3-T3 | **no** |
| Apple Developer account | E6-T1 Apple sign-in, `expo export` to a device | partly — email magic link works without it |

## 8. Reporting rules that go with this file

- A credential you were given but did not verify is `UNVERIFIED`. Say so.
- A number produced from a mocked provider is not a measurement. Log it as `UNMEASURED` and say
  which credential would have made it real.
- Do not print a secret into a handoff, a log, or a test failure message. `/readyz` reports names
  only; keep it that way.
