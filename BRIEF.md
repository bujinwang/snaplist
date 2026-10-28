# SnapList — Company Brief (read before any work)

## Mission
Ship a paid tool for **resale sellers** (eBay / Poshmark / Depop / Vinted) that turns a phone photo of a
garment into a listing-ready asset pack:

1. **Prettify** — clean, catalog-quality flat-lay image (background removed, straightened, evenly lit)
2. **Attributes** — structured JSON (category, color, pattern, material, fit, era, defects, measurements guess)
3. **On-model shot** — the garment rendered on a consistent model image, seller's choice of size/skin tone
4. **Listing** — copy + title + item specifics mapped to the target marketplace, exportable as their CSV

## Who pays and why
Full/part-time resellers listing 20–200 items/month. They currently hand-shoot, hand-clean and hand-type every
listing. Target: **cut listing time from ~8 min to <2 min per item.** They already pay for tools; this is a
revenue line, not a novelty.

## Non-goals (do not build these)
- No consumer "stylist" app, no social feed, no wardrobe graph, no virtual closet.
- No multi-tenant enterprise admin, no marketplace of its own.
- No self-hosted model training in v1. Use hosted APIs + one self-hosted fallback.
- No mobile-native code in v1 beyond what Expo gives us.

## Fixed technical decisions (do not relitigate)
| Concern | Decision |
|---|---|
| Repo | monorepo, pnpm workspace for JS + `uv` for python |
| API | Python 3.11, FastAPI, Pydantic v2, SQLModel |
| DB / storage / auth | Supabase (Postgres + Storage + Auth: email, Apple, Google) |
| Job execution | `jobs` table + Modal (Python) for GPU work; no Redis in v1 |
| Segmentation | SAM 2.1 (hosted) with U²-Net / BiRefNet fallback for cheap matting |
| Attributes | one VLM behind an adapter interface (`packages/vision/attrs.py`), JSON-schema enforced |
| Prettify | segment → matte → rectify (homography to canonical flat-lay) → neutral backdrop → de-wrinkle inpaint (SDXL inpaint, masked) |
| Virtual try-on | FASHN VTON v1.5 API (primary, commercial licence); CatVTON on Modal (fallback/cheap tier) |
| Mobile | Expo SDK (React Native, TypeScript), EAS build |
| Payments | Stripe Checkout + webhooks. Starter $19/mo = 100 items, Pro $49/mo = 400 items + on-model |
| Infra | GitHub Actions CI, Fly.io or Railway for api, no k8s |

## Repo layout (create exactly this)
```
snaplist/
  apps/api/            FastAPI service
  apps/mobile/         Expo app
  apps/web/            marketing + Stripe portal redirects
  packages/vision/     python: segment, matte, rectify, prettify, attrs, vton, dedupe
  packages/shared/     zod/pydantic schemas + marketplace CSV mappers
  evals/               golden fixtures + metric harness
  infra/               modal_app.py, migrations, CI
  docs/                PLAN.md, CONTRACTS.md, ADR-*.md, RUNBOOK.md
```

## Contracts
`docs/CONTRACTS.md` is the single source of truth for:
- `ItemAttributes` (pydantic + zod mirror)
- `AssetPack` response shape (`{item_id, prettified_url, cutout_url, on_model_urls[], attributes{}}`)
- `Job` states: `queued → running → done | failed(reason_code)`
- error `reason_code` enum: `bad_photo`, `no_garment`, `low_confidence`, `vton_rejected`, `internal`
- marketplace field mappings (eBay item specifics, Poshmark fields, Vinted fields)

Nobody changes a contract without an ADR in `docs/` and a version bump.

## Definition of done (applies to every task)
1. Files exist at the promised paths.
2. `make test` (pytest + `pnpm -w test`) exits 0 — paste the real output.
3. The eval command for your area runs and prints real numbers, not "should work".
4. No mocked model calls in the acceptance path. Fixtures on disk are fine; stubbed network responses are not.
5. `docs/HANDOFF-<role>.md` written: what shipped, what is broken, exact next command.

## Honesty rules (hard)
- **Never** report success you did not observe. If a run fails, write the error verbatim into `BLOCKED.md` and stop.
- Do not invent metric values. If you cannot measure it, write `UNMEASURED`.
- Do not edit another role's directory. If you need a change there, write it in `HANDOFF-<role>.md`.
- Do not add dependencies without noting them in `docs/DEPS.md` (name, version, licence, why).

## Cost ceilings (v1)
- ≤ **$0.03** all-in per item at Starter volume (seg + attrs + prettify).
- ≤ **$0.10** per on-model render.
- Free tier: 3 items/month, no on-model.
