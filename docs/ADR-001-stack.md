# ADR-001 — Stack

- **Status:** accepted
- **Date:** 2026-10-28
- **Deciders:** lead (architecture), with the whole team as reviewers
- **Context:** every row of the "Fixed technical decisions" table in `BRIEF.md`. These are
  recorded here as decisions with a rationale and a rejected alternative — not as a debate.
  Re-opening one requires a superseding ADR, which must include a migration cost.

Format: decision → **why** → *rejected alternative and what it would have cost us*.

---

### 1. Monorepo, pnpm workspace for JS + `uv` for Python

**Why:** one repo, one lockfile per ecosystem, so the contract (pydantic + zod) can be changed
and tested in a single commit; `uv` is fast enough that a clean install stays under a minute,
which keeps CI honest.

*Rejected:* separate repos per app — would have forced versioned npm packages for a contract
that changes weekly during v1. *Rejected:* a single-language repo (Node API) — the vision stack
we need (SAM 2, BiRefNet, CatVTON, diffusers) is Python-only.

### 2. Python 3.11, FastAPI, Pydantic v2, SQLModel

**Why:** Pydantic v2's Rust core makes schema validation of VLM output cheap enough to run on
every request; 3.11 is the newest version all of our CV dependencies ship wheels for.
`SQLModel` keeps one definition per table without a second mapping layer.

*Rejected:* Django — ORM + admin we do not need, and async story is worse for a job API.
*Rejected:* Python 3.12/3.13 — some CUDA wheels still lag; not worth the novelty.

### 3. Supabase (Postgres + Storage + Auth: email, Apple, Google)

**Why:** one vendor for the three boring things (DB, file storage with signed URLs, OAuth for
Apple/Google). RLS lets authz live in the database, so a missing `where user_id = …` in a new
endpoint cannot leak another seller's items.

*Rejected:* self-managed Postgres + S3 + Auth0 — three moving parts, three bills, and we would
have to write the RLS-equivalent in application code. *Rejected:* Firebase — weaker SQL story
for the quota ledger.

### 4. Job execution: `jobs` table + Modal for GPU; **no Redis** in v1

**Why:** Postgres already gives us durable queue semantics via
`SELECT … FOR UPDATE SKIP LOCKED`. One fewer service to run, back up and pay for. Modal gives
per-second GPU billing without a cluster.

*Rejected:* Celery + Redis — an extra datastore, an extra failure mode, and a worker fleet to
keep warm for a product that does a few thousand jobs a day at v1 volume. Revisit at ~50k
items/month.

### 5. Segmentation: SAM 2.1 hosted, U²-Net / BiRefNet fallback for cheap matting

**Why:** SAM 2.1 gives the mask quality the "catalog-quality" promise needs; the fallbacks make
the cost ceiling ($0.03/item) reachable for the long tail and keep the product working on a
machine with no GPU.

*Rejected:* per-pixel manual matting in v1 — not a product. *Rejected:* rembg alone — fails on
the hard cases in our golden set (hanger visible, two garments in frame).

### 6. Attributes: one VLM behind an adapter interface, JSON-schema enforced

**Why:** the model *will* change inside a quarter; the interface is the durable part. Forcing
output through `ItemAttributes` makes an unparseable answer an ordinary validation error instead
of a downstream mystery.

*Rejected:* a classifier per attribute — 5 models to train, 5 accuracy curves, and no way to
answer "what era is this". *Rejected:* free-form LLM output parsed with regex — the exact source
of silent data corruption.

### 7. Prettify: segment → matte → rectify (homography) → neutral backdrop → masked SDXL inpaint

**Why:** the deterministic stages do the heavy lifting so the generative model only touches
wrinkles. That is what keeps logos and text intact and lets us prove SSIM ≥ 0.85 before any
inpainting.

*Rejected:* one-shot image-to-image "make it look professional" — uncontrollable, hallucinates
logos, unmeasurable. *Rejected:* inpaint the whole garment — destroys print detail.

### 8. Virtual try-on: FASHN VTON v1.5 API primary, CatVTON on Modal fallback

**Why:** a commercial licence on the primary keeps the legal position simple; a self-hosted
fallback caps cost per render and removes the single-vendor outage risk for a paid feature.

*Rejected:* CatVTON only — quality below our gate on structured garments. *Rejected:* FASHN
only — one API key away from a dead Pro tier.

### 9. Mobile: Expo SDK (React Native, TypeScript), EAS build

**Why:** one codebase, over-the-air fixes, and camera/file/async-storage APIs that exist. The
team is not large enough to maintain two native codebases for v1.

*Rejected:* native iOS+Android — 2× the work for a capture-and-upload app. *Rejected:* a PWA —
camera quality and reliable background upload are still worse, and the seller shoots photos in
poor light on purpose.

### 10. Payments: Stripe Checkout + webhooks. Starter $19/100 items, Pro $49/400 + on-model

**Why:** Checkout means we never touch card data (PCI scope drops to near zero) and price
changes are configuration, not a release.

*Rejected:* StoreKit IAP for v1 — 30% cut and a review cycle for every price test. The App Store
risk of that choice is recorded in `docs/APPSTORE-RISK.md` (E9-T4), not hand-waved here.

### 11. Infra: GitHub Actions CI; Fly.io or Railway for the API; no k8s

**Why:** the API is one container and one job table. Anything with a control plane is overhead
we would pay for in on-call time.

*Rejected:* Kubernetes — for one service, it is a second product to operate.

### 12. Cost ceilings, treated as design constraints

≤ $0.03 all-in per item at Starter volume (seg + attrs + prettify) · ≤ $0.10 per on-model
render · free tier 3 items/month without on-model.

**Why:** these numbers decide the model choices above, not the other way round. They are
measured per stage in `evals/reports/cost.md`, not estimated in a spreadsheet.

*Rejected:* launching on the best model regardless of cost — a tool at $0.09/item cannot be
sold at $19 for 100 items.

---

## Consequences

- The heavy node_modules (Expo) and the heavy python venv live side by side; `make install`
  installs both, so a fresh clone is one command.
- Two lockfiles (`uv.lock`, `pnpm-lock.yaml`) are committed. Both are reviewable diffs.
- Sharing the contract across languages costs us one parity test
  (`apps/api/tests/test_contract_parity.py`). That test is cheaper than the alternative: two
  teams discovering drift in production.
