# HANDOFF — engineer (Kou)

Run date: 2026-10-28. Repo: `/Users/bujin/Documents/Projects/SnapList`.
Everything below was executed on this machine, in this working tree. Outputs are pasted verbatim.

**Headline:** the vertical slice runs with **zero credentials**. `make test` went from
32 pytest + 11 node to **163 pytest + 72 node**, `make lint` is clean, and the endpoints were
exercised against a real uvicorn process with real `curl`. The vision pipeline is still stubs, and
three routes are still 501 on purpose — see §2.

> Concurrency note: 54 of the node tests are mine; the other 2 come from the growth role adding an
> `export_delivered` analytics event to `packages/shared/src/analytics.ts` + its test while I was
> working. The numbers above were re-run after that landed, and the whole suite is green together.

### What changed in the second batch (after QA + lead review)

Baseline before this batch: `142 pytest + 58 node`, lint clean. After: **163 pytest + 72 node**,
lint clean, run twice — once warm and once after deleting `__pycache__` and `packages/shared/dist`.

1. **`POST /v1/exports` now returns a JSON envelope by default**:
   `{csv, requires_manual_columns, exported:[{item_id,row_index}], refused:[{item_id,reason,fields}], warnings}`.
   `Accept: text/csv` still returns the raw file with `content-disposition` **and**
   `x-snaplist-requires-manual-columns` (alignment aid only — the body is authoritative). Both
   representations send `Vary: Accept, Accept-Encoding`; without it a cache can serve the JSON to
   a client asking for the file, and that failure is invisible locally.
   `reason` is deliberately **not** `reason_code`: that name belongs to the frozen five-value
   `Job` enum, and an export refusal is a different domain
   (`test_export_refusal_reasons_are_not_job_reason_codes` fails if anyone folds them together).
   The three shapes are defined **once per language**: `apps/api/responses.py` on the Python side,
   `ExportedRowModel` / `RefusedRowModel` / `ExportBatchResponse` in `packages/shared/src/schemas.ts`
   on the TS side, with `test_contract_parity.py` mapping TS name → python model. Note the
   asymmetry, which is another role's deliberate call: on the TS side they are *inside* the contract
   module (and `CONTRACT_VERSION` went 1.0.0 → **1.1.0** for it), while on the Python side they sit
   next to `schemas.py` rather than in it. The parity test is what keeps the two honest; the file
   layout does not.
2. **`requires_manual_columns` is derived by a rule, not maintained as a list.**
   `ColumnSource` gained `manual_when_empty: string | null`; provenance is already mandatory per
   header, so adding a column forces the answer to "is an empty cell here the seller's job?".
   The batch rule uses the quantifier **any** (`rows.some(empty)`), not all: three items with two
   prices still need the third one. Governing line, in the code: *a column earns a literal when an
   empty cell makes the row invalid on upload* — `ebay_category_id`, `ebay_start_price`,
   `poshmark_price` (conditional, same input as eBay), `vinted_package_size` (always). Attribute
   columns are `null`: an item with no era is a data state, not an instruction.
3. **`prices: {item_id: cents}`** accepted on the export request, per item, never per batch. It
   rides on `ListingSource.price_cents`. `0` renders `"0.00"` **and does not appear in
   `requires_manual_columns`** — a giveaway is a real price; asking the seller to fill a field they
   already filled is its own failure mode. `null`/absent renders an empty cell. Unknown id, id
   outside `item_ids`, negative, non-integer, or above 10,000,000 cents ⇒ 422, never a silent drop.
   **This is a request-body field, not a contract change**: `ExportRequest` is frozen and
   `extra="forbid"`, so the router lifts `prices` out before the frozen model validates the rest.
   When it graduates into CONTRACTS.md, delete that shim.
   Price is *not* in BRIEF's listing definition (copy + title + item specifics). v1 sources it from
   the request; built-in pricing needs its own ADR.
4. **Quota de-duplication.** `PlanLimits` has no defaults any more (`PlanLimits()` is a
   `TypeError`), `Settings` is the code-side source, and the `plan_limits` table is the runtime
   override. One resolution order, in `usage.plan_ceiling`: DB row → Settings → nothing.
   Tests pin the seed against Settings and pin the precedence behaviourally (DB says 5, Settings
   says 3 ⇒ the 5th create is allowed and the 6th is the 402).
5. **Row-width guard in `writeCsv`.** A row whose cell count differs from the header throws
   `CsvShapeError` naming `row_index`, `len(row)`, `len(header)` and the first three cells. Padding
   would shift every following column (Size lands in Color), upload cleanly, and be discovered by a
   buyer.
6. **503 vs 501 share one shape for "not configured"**: `details.missing_credential`. A missing
   *dependency* (no `node`, no built dist) uses `details.missing_dependency` instead — prompting
   for a key that does not exist would send whoever fixes it to the wrong place. The discriminator
   is still "has `missing_credential`", not the status code.
7. **`noEmitOnError: true`** in `packages/shared/tsconfig.json`, with the CI caveat written next to
   it: when the build fails, `node --test` prints **no test names at all**, and that absence is the
   failure signal — not "there were no tests". `apps/mobile` needs nothing: it extends
   `expo/tsconfig.base` and sets `noEmit: true`, so the flag would be a no-op there.
8. **`infra/migrations/README.md`**: `merged_into` now says `references profiles(user_id)` with the
   reason (profiles has no `id`; `user_id` is the PK so every RLS predicate stays
   `auth.uid() = user_id`), plus the `CHECK merged_into <> user_id` that README did not have.

**Also fixed by this batch, found by QA**: the 503 body used to be indistinguishable from a server
error (`code: "internal"`, no details). It now carries `details.missing_credential`, reusing the
501 shape rather than adding an `ErrorCode` value (which would be a wire-contract change for a
wording problem).

### One incident worth knowing about (concurrent edits, 11:07–11:10)

While I was taking the final snapshot, `apps/api/schemas.py`, `apps/api/settings.py`,
`apps/api/usage.py` and `packages/shared/src/{schemas,analytics}.ts` were being rewritten by another
role (mtimes moved 3–4 times in three minutes). Two runs went red mid-edit with **exactly the drift
the contract gate is for**:

```
E  AssertionError: {'ItemAttributes': {'python_only': ['size_label'], 'zod_only': ['sizeLabel']}}
E  AssertionError: {'Measurements': {'python_only': ['inseamCm'], 'zod_only': ['inseam_cm']}}
E  AssertionError: assert {'colors'} == {'category', 'colors'}      # a default crept onto `category`
```

Those are not my code failing and not a false positive: they are `test_contract_parity.py` and
`test_contracts.py` doing their job while somebody edited the frozen models. After the files settled
and stale `__pycache__`/`dist` were cleared, **three consecutive `make test && make lint` runs were
green** — that is the state I am handing over. If the tree goes red again on a camelCase field or a
newly-optional `category`, look at whoever is editing `apps/api/schemas.py` /
`packages/shared/src/schemas.ts`, not at the API code. `CONTRACT_VERSION` is **`1.1.0`** on both
sides as of this handoff (bumped during this batch by another role, for the export envelope models —
`ItemAttributes` is still exactly 16 `snake_case` fields, unchanged from v1.0.0).

(The 11:07–11:10 edits turned out to be QA's mutation-testing window, not a real drift — the lead
confirmed it afterwards. Leaving the note here because the *diagnosis technique* is the useful part:
two different failures at two different times, both inside the frozen models, and the parity test
was right both times.)

### Verified twice, warm and cold (this batch)

```
$ date; make test && make lint                 # warm: dist and __pycache__ present
11:36:20
packages/shared test: # tests 72
packages/shared test: # pass 72
packages/shared test: # fail 0
packages/shared test: # skipped 0
163 passed, 1 warning in 0.99s
All checks passed!

$ find . -name __pycache__ -prune -exec rm -rf {} + ; rm -rf .pytest_cache packages/shared/dist
$ date; make test && make lint                 # cold: rebuild from source
11:36:25
packages/shared test: # fail 0
163 passed, 1 warning in 1.01s
All checks passed!

$ ls -l packages/shared/dist/src/schemas.d.ts    # proves the cold run really rebuilt
-rw-r--r-- 1 bujin staff  ...  Oct 28 11:36 packages/shared/dist/src/schemas.d.ts
```

`git status` cannot show that deletion: `packages/shared/dist/` is `.gitignore`d, so its output is
empty both before and after — identical to "I did nothing". The timestamps above are the proof.

Test counts: **163 pytest** (128 API + 27 vision + 8 evals — QA's three new files are inside the
127 and are QA's property, not mine to edit) and **72 node** (was 58).

---

## 1. What is real, what is stub, what is credential-blocked

| # | Ticket (docs/PLAN.md) | State | Evidence |
|---|---|---|---|
| P0-1 | E5-T1 `infra/migrations/0001_init.sql` | **done, executed on local Postgres 14.18** (not on 15/16) | §4 — applied **twice** (idempotent), 9 tables, RLS on all 9, 19 policies, 7 triggers, `RLS PROOF: PASS (4/4)`, `CONSTRAINT PROOF: PASS (14/14)` |
| P0-2 | E5-T2 items/jobs endpoints | **done (with a repository seam)** | §3 A–J |
| P0-2 | E8-T3 quota ledger + 402 | **done** | §3 K–M |
| P0-2 | E8-T5 `GET /v1/usage` | **done** | §3 K |
| P0-2 | E5-T5 auth | **partly done — HS256 real, JWKS not** | §3 R–U, §2 |
| P0-2 | E5-T4 storage / signed upload | **501, blocked** (needs Supabase) | §3 P |
| P0-3 | E7-T1 eBay mapper | **done, headers partly UNVERIFIED** | §5, and §2 "eBay category" |
| P0-3 | E7-T2 Poshmark + Vinted mappers | **done, all columns UNVERIFIED** | §5 |
| P0-4 | E7-T3 copy generator | **done, hallucination test proven red-able** | §5 |
| P1 | E2-T1 rectify **pure math** | **done** (homography, target rect, clamping) | `packages/vision/tests/test_rectify_geometry.py` |
| P1 | E2-T1 rectify **pixel pipeline** | **still stub** (`StageNotImplemented`) | — |
| P2 | E7-T4 comps pricing (offline) | **done**, `insufficient_data` under 5 comps | §5 |
| P1 | E1-T1 runtime probe (`packages/vision/runtime.py`) | **done — Pip, 2026-10-28, second session** | §12 — acceptance command output pasted, 10 unit tests, `make test`/`make lint` re-run green |
| — | E1/E3/E4 vision + VTON stages | **untouched stubs**, as instructed | — |
| — | E5-T3 job worker | **not built**; state machine implemented and tested, no process claims jobs | §2 "jobs stay queued" |
| — | E8-T1/T2 Stripe | **501**, names the missing secret | §3 P |

---

## 2. The honest caveats (read these before quoting anything above)

1. **SQL: executed on PostgreSQL 14.18, not on 15/16.** `0001_init.sql` was applied to a throwaway
   local database with `infra/migrations/verify/local_supabase_shim.sql` (which recreates
   `auth.users`, `auth.uid()` and the `authenticated`/`anon` roles), **twice in a row**, then both
   proof scripts were run against it — see §4 for the verbatim transcript. It has **never** been
   run against a real Supabase project (15/16); `supabase db push` needs a project ref we do not
   have. RLS/CHECK semantics are stable across 14→16, but treat "works on Supabase" as `UNVERIFIED`
   until someone runs it there.
2. **Auth: HS256 is real, asymmetric is not.** Signature checking, `exp`/`nbf` and `sub`-is-a-uuid
   are implemented with stdlib `hmac` and proven with real signed tokens (§3 R–U). **ES256/RS256
   (Supabase's current JWKS flow) returns 501**, not "accept". That is a gap, not a pass.
3. **`local_dev` identity is not authentication.** With no `SUPABASE_JWT_SECRET` and `ENV` in
   {local,test,development}, the caller declares its own uuid in `X-SnapList-User-Id`. Every `/v1`
   response carries `x-snaplist-auth-mode: local_dev` and `/readyz` reports
   `unverified_identity: true`. The header is refused the moment a JWT secret exists or `ENV` is
   anything else (§3 S). This exists so the isolation tests can run without credentials; it is a
   development double, never a security claim.
4. **`POST /v1/items` returns 202 with a `queued` job that nothing will ever run.** There is no
   worker (E5-T3). A fresh job therefore stays `queued` forever and every asset URL stays `null`.
   I judged that more honest than either (a) inventing a `done` job, or (b) 501-ing the create,
   which would have made the item/quota/export paths untestable. `GET /v1/jobs/{id}` says
   `queued`, and the AssetPack says `null` — nothing claims a render happened.
5. **No item can get a photo without a worker.** `POST /v1/exports` therefore correctly refuses
   every row with `missing_fields=prettified_url` on a fresh item. For the curl session I attached
   the asset through `python -m apps.api.devtools attach-asset`, which performs **the same
   `repository.set_asset` call the worker will make**. That tool refuses to run when `ENV` is not
   local/test/development. The export endpoint itself contains no test hooks (§3 G, H).
6. **eBay `*Category` is empty by design.** Resolving `top` → an eBay numeric category id needs the
   Taxonomy API and credentials. Emitting a guessed id is the exact failure CONTRACTS.md §5
   forbids, so the column is left blank and the batch returns
   `warnings: ["ebay_category_id_unverified", ...]`. Consequence: **the CSV is not upload-ready**;
   a seller must map the category column first. See §6 for why I did not 422 the whole batch
   instead, and disagree with me if you prefer the refusal.
7. **Poshmark/Vinted column names are ours.** Neither marketplace publishes a stable bulk-CSV
   template this project could confirm. Every column is marked `unverified` with a written reason
   in `POSHMARK_SOURCES` / `VINTED_SOURCES`, and each export carries a warning string.
8. **numpy is not in this venv**, and adding it means editing `pyproject.toml` + `docs/DEPS.md`,
   neither of which is in my file scope. The homography is therefore a pure-Python 8×8 Gaussian
   elimination, unit-tested to 1e-9 against analytically known matrices. `_solve_linear` is the
   single seam to swap in `numpy.linalg.solve` later.

---

## 3. Real curl session (verbatim)

Server: `.venv/bin/python -m uvicorn apps.api.main:app --port 8000`, `SNAPLIST_DB=/tmp/snaplist-dev.db`.
Dates/server/content-length headers stripped with `grep -viE` to keep it readable; nothing else edited.

### A–B. create + idempotent replay

```
## A. create (first call, Idempotency-Key: upload-42)
$ curl -s -i -X POST localhost:8000/v1/items -H "content-type: application/json" -H "x-snaplist-user-id: 11111111-1111-4111-8111-111111111111" -H "Idempotency-Key: upload-42" -d '{"filename":"tee.jpg"}'
HTTP/1.1 202 Accepted
content-type: application/json
x-snaplist-auth-mode: local_dev

{"item_id":"23b3dd8b-e335-470c-bd3f-e2f1022b96d9","job_id":"65def2a9-1c49-46cf-ba3c-466d1a3f65b4","upload_url":null}

## B. replay of the same Idempotency-Key (must be the same item_id + job_id, one unit spent)
{"item_id":"23b3dd8b-e335-470c-bd3f-e2f1022b96d9","job_id":"65def2a9-1c49-46cf-ba3c-466d1a3f65b4","upload_url":null}
```

### C–F. AssetPack, job, PATCH, invalid PATCH

```
## C. GET /v1/items/23b3dd8b-e335-470c-bd3f-e2f1022b96d9  (AssetPack: five keys, nulls while the pipeline has not run)
{"item_id":"23b3dd8b-e335-470c-bd3f-e2f1022b96d9","prettified_url":null,"cutout_url":null,"on_model_urls":[],"attributes":null}

## D. GET /v1/jobs/65def2a9-1c49-46cf-ba3c-466d1a3f65b4  (queued: no worker exists to claim it)
{"id":"65def2a9-1c49-46cf-ba3c-466d1a3f65b4","item_id":"23b3dd8b-e335-470c-bd3f-e2f1022b96d9","kind":"full","state":"queued","reason_code":null,"attempts":0,"progress":0.0,"error_detail":null,"created_at":"2026-10-28T16:56:48.324231Z","updated_at":"2026-10-28T16:56:48.324231Z","started_at":null,"finished_at":null}

## E. PATCH attributes (validated by ItemAttributes)
HTTP/1.1 200 OK
content-type: application/json
x-snaplist-auth-mode: local_dev

{"category":"top","subcategory":"crewneck tee","colors":["black"],"pattern":"unknown","material":["cotton"],"fit":"unknown","era":null,"brand":null,"size_label":"M","condition":"good","defects":[],"measurements":{"chest_cm":52.0,"length_cm":68.5,"waist_cm":null,"rise_cm":null,"inseam_cm":null,"sleeve_cm":null,"source":"seller_provided"},"keywords":[],"confidence":{},"needs_review":[],"model_version":"unset"}

## F. PATCH with an illegal enum value (422, unified envelope)
HTTP/1.1 422 Unprocessable Entity
content-type: application/json
x-snaplist-auth-mode: local_dev

{"error":{"code":"invalid_request","message":"Request did not validate against the contract. See details for the fields.","retry_after_seconds":null,"details":{"body.category":"Input should be 'top', 'bottom', 'dress', 'outerwear', 'shoe', 'accessory' or 'other'"}}}
```

### G–H. worker stand-in + AssetPack after an asset exists

```
## G. worker stand-in: attach the prettified asset (no worker exists yet — see apps/api/devtools.py)
$ SNAPLIST_DB=/tmp/snaplist-dev.db .venv/bin/python -m apps.api.devtools attach-asset --item 23b3dd8b-e335-470c-bd3f-e2f1022b96d9 --user 11111111-1111-4111-8111-111111111111 --kind prettified --path https://example.test/items/1/prettified.png
attached prettified -> https://example.test/items/1/prettified.png
item 23b3dd8b-e335-470c-bd3f-e2f1022b96d9 assets: {'prettified': 'https://example.test/items/1/prettified.png'}

## H. GET the AssetPack again (prettified_url now populated, attributes present)
{"item_id":"23b3dd8b-e335-470c-bd3f-e2f1022b96d9","prettified_url":"https://example.test/items/1/prettified.png","cutout_url":null,"on_model_urls":[],"attributes":{"category":"top","subcategory":"crewneck tee","colors":["black"],"pattern":"unknown","material":["cotton"],"fit":"unknown","era":null,"brand":null,"size_label":"M","condition":"good","defects":[],"measurements":{"chest_cm":52.0,"length_cm":68.5,"waist_cm":null,"rise_cm":null,"inseam_cm":null,"sleeve_cm":null,"source":"seller_provided"},"keywords":[],"confidence":{},"needs_review":[],"model_version":"unset"}}
```

### I. eBay export — real CSV, with a quoted multi-line description

```
## I. POST /v1/exports  {"marketplace":"ebay"}  → real CSV (headers + body)
$ curl -s -i -X POST localhost:8000/v1/exports -H "content-type: application/json" -H "x-snaplist-user-id: 11111111-1111-4111-8111-111111111111" -d "{\"marketplace\":\"ebay\",\"item_ids\":[\"23b3dd8b-e335-470c-bd3f-e2f1022b96d9\"]}"
HTTP/1.1 200 OK
content-disposition: attachment; filename="snaplist-ebay.csv"
x-snaplist-export-rows: 1
x-snaplist-export-refused: 0
content-type: text/csv; charset=utf-8
x-snaplist-auth-mode: local_dev

*Action(SiteID=US|Country=US|Currency=USD|Version=745|CC=UTF-8),*Category,*Title,*Description,*ConditionID,*Quantity,*StartPrice,*Duration,PicURL,C:Brand,C:Type,C:Size,C:Color,C:Material,C:Pattern
Add,,Black cotton crewneck tee M,"Black crewneck tee.

In good pre-owned condition, worn with normal signs of use.

Details
- Size: M
- Material: cotton
- Colour: black

Measurements
Measured by the seller, flat.
- Chest: 52 cm
- Length: 68.5 cm",3000,1,,GTC,https://example.test/items/1/prettified.png,,crewneck tee,M,black,cotton,
```

(`*Category` and `*StartPrice` are empty — see §2 caveats 6 and the mapper docstring.)

### J. Poshmark refusal — the contract's "refusal beats guessing"

```
## J. POST /v1/exports {"marketplace":"poshmark"} → refused: brand is required and this item has none
HTTP/1.1 422 Unprocessable Entity
content-type: application/json
x-snaplist-auth-mode: local_dev

{"error":{"code":"invalid_request","message":"No row could be exported for this marketplace; the mapper refused every item.","retry_after_seconds":null,"details":{"reason":"insufficient_data","item_id":"23b3dd8b-e335-470c-bd3f-e2f1022b96d9","missing_fields":"brand","refused_rows":"1"}}}
```

### K–M. usage, ceiling, 402

```
## K. GET /v1/usage (1 of 3 free items used)
{"plan":"free","period_start":"2026-10-01T00:00:00Z","period_end":"2026-11-01T00:00:00Z","items_included":3,"items_used":1,"items_remaining":2,"on_model_included":0,"on_model_used":0}

## L. two more items → the free ceiling of 3
create#2 -> 202
create#3 -> 202

## M. the 4th item: 402 Payment Required, body must carry code="quota_exceeded"
create#4 -> HTTP 402
HTTP/1.1 402 Payment Required
content-type: application/json
x-snaplist-auth-mode: local_dev

{"error":{"code":"quota_exceeded","message":"Free plan allows 3 items per month.","retry_after_seconds":null,"details":{"plan":"free","limit":"3","used":"3"}}}
```

### N–Q. isolation, 501s, unauthenticated

```
## N. quota is per user: user B is unaffected
{"plan":"free","period_start":"2026-10-01T00:00:00Z","period_end":"2026-11-01T00:00:00Z","items_included":3,"items_used":0,"items_remaining":3,"on_model_included":0,"on_model_used":0}

## O. cross-user isolation over HTTP: B cannot read A item (404, not 403 — no existence leak)
HTTP/1.1 404 Not Found
content-type: application/json
x-snaplist-auth-mode: local_dev

{"error":{"code":"not_found","message":"No item 23b3dd8b-e335-470c-bd3f-e2f1022b96d9 for this account.","retry_after_seconds":null,"details":null}}

## P. no credentials blocked path: rerender (501) and checkout (501)
rerender -> HTTP 501
checkout -> HTTP 501
{"error":{"code":"not_implemented","message":"POST /v1/items/{item_id}/rerender needs the job worker (E5-T3) and packages.vision.prettify (E2-T2), neither of which is implemented.","retry_after_seconds":null,"details":null}}

## Q. unauthenticated call (401, envelope)
no-auth GET /v1/usage -> HTTP 401
```

### R–U. JWT mode (`ENV=staging SUPABASE_JWT_SECRET=curl-demo-secret`, port 8001, real signed HS256 token)

```
## R. real JWT mode (ENV=staging, SUPABASE_JWT_SECRET=curl-demo-secret): a signed HS256 token is required
$ curl -s -i localhost:8001/v1/usage -H "authorization: Bearer <hs256 token>"
HTTP/1.1 200 OK
content-type: application/json
x-snaplist-auth-mode: supabase_jwt

{"plan":"free","period_start":"2026-10-01T00:00:00Z","period_end":"2026-11-01T00:00:00Z","items_included":3,"items_used":0,"items_remaining":3,"on_model_included":0,"on_model_used":0}

## S. the local-dev header is ignored in JWT mode (401)
x-snaplist-user-id only -> HTTP 401

## T. a tampered signature is rejected (401)
tampered token -> HTTP 401

## U. no authorization header at all (401)
{"error":{"code":"unauthorized","message":"Missing `Authorization: Bearer <supabase access token>` header.","retry_after_seconds":null,"details":null}}
```

---

## 4. SQL verification — actually executed on local Postgres 14.18

Postgres **is** available on this machine (`pg_isready` → `/tmp:5432 - accepting connections`,
`psql (PostgreSQL) 14.18 (Homebrew)`), so the migration was not left as an assertion — it was run,
twice, on a throwaway database, and the two proof scripts were run against it.

Version scope, stated precisely: **verified on PostgreSQL 14.18. Supabase production is 15/16 and
this file has NOT been re-verified on 15/16.** RLS and CHECK semantics used here are stable across
14→16, but "works on Supabase" remains `UNVERIFIED` until someone runs `supabase db push`.

```
$ createdb snaplist_migcheck_4828
$ export PGDATABASE=snaplist_migcheck_4828
$ psql -v ON_ERROR_STOP=1 -f infra/migrations/verify/local_supabase_shim.sql
BEGIN
CREATE SCHEMA
CREATE TABLE
CREATE FUNCTION
COMMENT
DO
COMMIT
EXIT=0

$ psql -v ON_ERROR_STOP=1 -f infra/migrations/0001_init.sql      # first apply
BEGIN
CREATE TABLE
COMMENT
INSERT 0 3
CREATE TABLE
COMMENT
COMMENT
COMMENT
CREATE TABLE
COMMENT
CREATE TABLE
COMMENT
CREATE TABLE
COMMENT
COMMENT
COMMENT
COMMENT
COMMENT
CREATE TABLE
COMMENT
COMMENT
COMMENT
CREATE INDEX
CREATE INDEX
CREATE TABLE
CREATE INDEX
CREATE INDEX
COMMENT
CREATE TABLE
COMMENT
CREATE TABLE
COMMENT
CREATE FUNCTION
COMMENT
NOTICE:  trigger "set_updated_at" for relation "public.profiles" does not exist, skipping
DROP TRIGGER
CREATE TRIGGER
... (the same NOTICE/DROP TRIGGER/CREATE TRIGGER pair repeats for items, assets, jobs,
     subscriptions, model_refs, api_keys — 7 triggers total; elided here only, EXIT=0)
CREATE INDEX  ×6
NOTICE:  policy "plan_limits_read_all" for relation "public.plan_limits" does not exist, skipping
CREATE POLICY
... (the same NOTICE/CREATE POLICY pair repeats for all 19 policies; elided here only)
COMMIT
EXIT=0

$ psql -v ON_ERROR_STOP=1 -f infra/migrations/0001_init.sql      # second apply (README: must run clean twice)
BEGIN
NOTICE:  relation "plan_limits" already exists, skipping
CREATE TABLE
COMMENT
INSERT 0 0
NOTICE:  relation "profiles" already exists, skipping
...
NOTICE:  policy "api_keys_delete_own" for relation "public.api_keys" does not exist, skipping
CREATE POLICY
COMMIT
EXIT=0

$ psql -v ON_ERROR_STOP=1 -f infra/migrations/verify/rls_isolation_check.sql
 seller_a_sees_own_item
------------------------
 ok
(1 row)

 seller_b_cannot_see_a_item
----------------------------
 ok
(1 row)

        result
-----------------------
 RLS PROOF: PASS (4/4)
(1 row)

EXIT=0

$ psql -v ON_ERROR_STOP=1 -f infra/migrations/verify/constraints_check.sql
             result
--------------------------------
 CONSTRAINT PROOF: PASS (14/14)
(1 row)

EXIT=0
```

Every step exited 0. The second apply is clean, which is the `infra/migrations/README.md` hygiene
rule ("every migration runs clean twice in a row") — the `if not exists` guards and the
`drop trigger/policy if exists` pairs are what make that true, not luck.

The 14 constraint proofs each assert that the database **refuses** a row the contract forbids:
failed job without a reason code · a sixth reason code · a reason code on a non-failed job ·
`attempts = 3` in a non-terminal state · `finished_at < started_at` · overage enabled with no price ·
an unknown plan · a plaintext API key · a real likeness without consent · attributes counters on the
wrong event · a double refund for one job · negative `cost_micros` · a blank `provider` · a profile
merged into itself.

### Are the 14 proofs independent, or does one failure mask the rest?

`14/14` alone does not answer that. The check: drop exactly one CHECK, re-run, and count failures.

```
$ psql -d snaplist_patch2_39607 -c 'alter table public.jobs drop constraint jobs_reason_code_check;'
ALTER TABLE
EXIT=0

$ psql -v ON_ERROR_STOP=0 -d snaplist_patch2_39607 -f /tmp/constraints_no_stop.sql
EXIT=0
--- failures (expect exactly 1) ---
1
psql:/tmp/constraints_no_stop.sql:38: ERROR:  CONSTRAINT MISS: a reason code outside the frozen
                                              five was accepted
--- did the script reach the end? (proves the other 13 did not raise) ---
 CONSTRAINT PROOF: PASS (14/14)
```

Line 38 is proof #2 — the one that owns `jobs_reason_code_check`. Exactly one proof failed, and the
script ran to the end, so the other thirteen still pass. **The proofs are independent.**

Two honest notes about that experiment:

* `/tmp/constraints_no_stop.sql` is the repo file with its first line rewritten
  (`\set ON_ERROR_STOP on` → `off`); the file's own directive otherwise overrides the `-v` flag and
  the run stops at the first failure, which would have hidden whether the remaining proofs still
  pass.
* **the `PASS (14/14)` banner is not a trustworthy signal in that mode** — it prints whenever the
  script reaches the end, regardless of how many proofs raised during the run. In `ON_ERROR_STOP`
  mode the script dies at the first failure instead, so the banner never lies there. The real signal
  is the count of `CONSTRAINT MISS` lines. (P2 improvement: make the banner conditional on a
  zero-failure count, so the two modes agree.)

Schema inventory as read back from the live database (`tablename|policies|rowsecurity`):

```
api_keys|4|t
assets|1|t
items|4|t
jobs|1|t
model_refs|4|t
plan_limits|1|t
profiles|2|t
subscriptions|1|t
usage_events|1|t
```

9 tables, RLS on all 9, 19 policies, **7 `set_updated_at` triggers**.
`usage_events` and `jobs` have a SELECT policy only — a seller cannot append to the quota ledger or
move a job, so the refund fraud path is closed at the database, not just in the router.

Columns an auditor will look for, read back from `information_schema.columns`:

```
jobs.cost_micros bigint null=YES            <- NULL means UNMEASURED, never 0
jobs.model_version text null=YES
jobs.provider text null=YES                 <- stubs self-report 'stub'
profiles.is_anonymous boolean null=NO       <- ADR-003
profiles.merged_into uuid null=YES          <- ADR-003
profiles.paywall_variant text null=NO
subscriptions.overage_enabled boolean null=NO
subscriptions.overage_unit_price_cents integer null=YES
```

The API writes `provider='stub'` on every job it creates (`apps/api/repository.py::create_job`), so
six weeks from now a real pipeline run is distinguishable from this build's placeholder. The three
audit columns are deliberately **not** on the wire — `Job` is frozen at 11 fields and
`cost_micros` is an internal accounting fact, not client state.

`jobs` CHECK constraints, straight from `pg_constraint`:

```
jobs_attempts_exhausted_is_terminal|CHECK (((attempts < 3) OR (state = ANY (ARRAY['done'::text, 'failed'::text]))))
jobs_attempts_range|CHECK (((attempts >= 0) AND (attempts <= 3)))
jobs_cost_micros_non_negative|CHECK ((cost_micros IS NULL) OR (cost_micros >= 0))
jobs_kind_check|CHECK ((kind = ANY (ARRAY['full'::text, 'prettify'::text, 'attributes'::text, 'vton'::text, 'export'::text])))
jobs_progress_range|CHECK (((progress >= (0)::numeric) AND (progress <= (1)::numeric)))
jobs_provider_not_blank|CHECK ((provider IS NULL) OR ((length(btrim(provider)) >= 2) AND (length(btrim(provider)) <= 64)))
jobs_reason_code_check|CHECK (((reason_code IS NULL) OR (reason_code = ANY (ARRAY['bad_photo'::text, 'no_garment'::text, 'low_confidence'::text, 'vton_rejected'::text, 'internal'::text]))))
jobs_reason_code_iff_failed|CHECK (((state = 'failed'::text) = (reason_code IS NOT NULL)))
jobs_state_check|CHECK ((state = ANY (ARRAY['queued'::text, 'running'::text, 'done'::text, 'failed'::text])))
jobs_timestamps_ordered|CHECK ((((started_at IS NULL) OR (started_at >= created_at)) AND ((finished_at IS NULL) OR ((started_at IS NOT NULL) AND (finished_at >= started_at)))))
```

**Cleanup.** The throwaway database was dropped after the run
(`dropdb snaplist_migcheck_4828`), and `psql -lqt | grep -i snaplist` now returns nothing. No
existing database on this machine was connected to, written to, or modified — the other databases
listed by `psql -l` (`JobSeekerDb`, `alberta_medical_billing_new`, `medical_codes`, `autobebe`,
`badminton_group`, `aibeautylens_dev_db`) were never touched.

**How to verify on real Supabase** (the step I could not do):

```bash
supabase link --project-ref <ref>
supabase db push                       # applies 0001_init.sql
psql "$SUPABASE_DB_URL" -f infra/migrations/verify/constraints_check.sql   # expect 14/14
# RLS needs two real JWTs (PLAN E5-T1): one per seller, then select across.
```

---

## 5. Acceptance commands (verbatim)

```
$ make test && make lint
...
packages/shared test: # tests 72
packages/shared test: # pass 72
packages/shared test: # fail 0
packages/shared test: # skipped 0
packages/shared test: Done
...
163 passed, 1 warning in 1.00s
All checks passed!
packages/shared lint: Done
apps/mobile lint: Done
```

Warm and cold runs of both commands are pasted at the top of this file (the cold one deletes
`__pycache__` and `packages/shared/dist` first, and proves the rebuild with `ls -l` timestamps).

Zero skips, proven rather than assumed — the same suite run verbosely, counting `SKIPPED` lines:

```
$ .venv/bin/python -m pytest -v 2>&1 | grep -c "SKIPPED"
0
```

```
$ .venv/bin/python -m ruff check .
All checks passed!
```

Lint history, so nobody has to guess who fixed what: the 15 errors reported mid-run were
10 × `B008` (FastAPI `Depends()` in signature defaults — a false positive, silenced **in config
only** by the architect's `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls` block in
`pyproject.toml`; no endpoint structure was changed for it) plus 5 in my files: 2 × `UP038`
(`isinstance(x, int | float)`), 2 × `E501`, 1 × `I001`. All 5 are fixed in my files, and the config
change is not mine — I did not edit `pyproject.toml`.

```
$ pnpm -w test packages/shared
...
packages/shared test: # tests 56
packages/shared test: # pass 56
packages/shared test: # fail 0
```

Test count movement: **pytest 32 → 163** (+131), **node 11 → 72** (+61). Attribution, because the
numbers are not all mine: of the +130 python tests, ~90 are this role's, 4 came from
`evals/tests/test_harness.py` (another role), and ~37 are QA's (`test_contract_requiredness.py`,
`test_billing_blocked.py`, `test_devtools_guard.py` and additions to existing files) — QA's files
are QA's property and were not edited here. Of the +61 node tests, 43 are this role's, 2 are the
concurrent `export_delivered` event, and the rest arrived with the same files over time. The
authoritative number is whatever `make test` prints on your machine, not this paragraph. Run three
times back-to-back after clearing caches: `125 passed` × 3, lint green × 3. Nothing was skipped:
`test_exports.py` has a `skipif` on `node`/`dist` but `make test` builds the shared package first,
so all 7 of those ran, which the `SKIPPED` count of 0 confirms.

**`apps/api/tests/test_scaffold.py` — the two stale 501 assertions were replaced, not deleted.**
The scaffold asserted `POST /v1/items` → 501 because every `/v1` route was a stub. That stopped being
true when the route was implemented, so the two tests now assert the *real* behaviour:
`test_unauthenticated_v1_calls_use_the_error_envelope` (401 + envelope shape, no bare 500),
`test_credential_blocked_routes_answer_501_with_the_same_envelope` (the routes that genuinely still
501 — checkout names `stripe_secret`), plus `test_an_unknown_path_uses_the_error_envelope`,
`test_the_stripe_webhook_is_501_and_takes_no_auth` and
`test_no_route_returns_fastapi_default_detail_bodies`. Nothing was skipped or loosened to "not a 500":
the 202/asset-pack shape is asserted field-by-field in `test_items.py`
(`set(body) == {"item_id", "job_id", "upload_url"}`, `upload_url is None`,
`sorted(body) == [five AssetPack keys]`). The 501-instead-of-202 behaviour is still pinned where it
belongs — the four credential/provider-blocked routes.

What the new tests actually assert (not just "calls returned 200"):

* `test_isolation.py` — B cannot GET/PATCH/DELETE/export A's item; quota is per user; an idempotency
  key reused on a second account does not hand over the first account's item.
* `test_jobs.py` — every illegal edge raises (`done→running`, `queued→done`, `queued→failed`),
  retry budget is exactly 3 claims then terminal, a failure refunds exactly one unit, a refund
  without a spend mints nothing.
* `test_usage.py` — plan limits mirror `Settings` (3/100/400), calendar-month period including the
  December rollover, half-open interval, negative usage clamped to 0, hitting the free ceiling
  records `free_tier_item_limit`.
* `test_auth.py` — real HS256 verify/tamper/expiry, `alg=none` → 501, local-dev header refused in
  JWT mode, production-without-secret → 503.
* `test_exports.py` — eBay CSV over HTTP, Poshmark refusal naming `brand`, 2-item refused batch.
* `packages/shared/test/copy.test.ts` — **the hallucination test can go red**: `unsupportedClaims`
  finds nothing on the honest item, then finds `["Burberry"]` / `["cashmere"]` in a deliberately
  forged copy. Plus `hasShoutedToken("NIKE AIR FORCE") === true` while the generated title is false.
* `packages/shared/test/marketplaces.test.ts` — header arrays pinned as string literals, provenance
  row per column, `documented` columns must carry a URL, CSV escaping for comma/quote/newline/bare
  quote, Vinted fibre filtering, eBay condition table.

---

## 6. Deviations and things I think are wrong with the design

Saying these out loud rather than papering over them:

1. **CONTRACTS.md §5 "unmapped ⇒ refuse that row" vs a usable eBay export.** I chose to leave
   `*Category` empty and warn, instead of refusing every eBay row. Refusing would mean the export
   endpoint can never succeed in v1 (all three marketplaces lack a verified category mapping), which
   makes the feature unshippable and gives the seller no information about the other 14 columns.
   An empty cell is a visible, fixable gap; a wrong category id is an invisible, expensive one. **If
   the lead disagrees, the change is 5 lines in `ebay.ts` (`refusal(source.item_id, ["category_id"])`)
   plus rewriting 2 tests — say the word.**
2. **The repo has no price anywhere.** `ExportRequest` and `ItemAttributes` are both frozen and
   neither carries a price, and `items` has no price column (nor should it — price is per-listing,
   not per-garment). So `*StartPrice` / `Price` / `Original Price` are always empty and every export
   warns. A real v1 needs a seller-set price on the export request; that is an ADR + minor contract
   bump, not something I can do inside a frozen contract.
3. **`GET /v1/usage` returns 200 even over the ceiling.** The task text said "usage → 402 when over",
   but a seller at their limit still needs to read the number, and mobile reads
   `items_remaining` from that call to draw the paywall. The 402 lives at the enqueue points
   (`POST /v1/items`, `POST /v1/exports`, on-model), which is what E8-T3 asks for ("enforced at the
   enqueue point so a double-tap cannot spend two units"). I kept 402 out of GET on purpose.
4. **A `queued` job that nothing will ever claim is a UX lie of omission.** Today's API is honest
   per-response but a client polling forever will show a spinner forever. The fix is E5-T3, not a
   message tweak. Until then, treat `state=queued` as "accepted, not started".
5. **`devtools.py` exists because of a hole**: no code path can attach an asset without a worker, so
   the export path was unreachable by hand. It is gated to local/test envs and imports nothing into
   the API. A reviewer may reasonably want it deleted once E5-T3 lands.
6. **`attributes_edited.fields_touched` can only be client-reported.** The server sees writes, not
   reads, so it records `edited_field_count` and accepts `X-SnapList-Fields-Touched` for the honest
   workload number. If that header is missing, `fields_touched` is understated — documented in the
   route and in the migration column comment, but someone should decide whether the client is
   trusted for this metric.
7. **~~`plan_limits` and `Settings` both encode 3/100/400.~~ Resolved in the second batch**: one
   resolution order in `usage.plan_ceiling` (DB row → Settings), no defaults on `PlanLimits`, and
   two tests pinning the seed and the precedence. The duplication that remains is the *seed* itself,
   which SQL cannot read out of Python — the test is the glue.
8. **`requires_manual_columns` covers *empty* columns, not *invalid* ones — this is the part I am
   least sure about.** All three mappers emit our own category vocabulary (`top`, `crewneck tee`)
   into a column the marketplace expects to be *its* tree. The cell is not empty, so the rule
   correctly stays silent, and the client shows no warning — yet Poshmark and Vinted would very
   likely reject that value. The `warnings` array says `..._category_id_unverified`, but warnings
   are advisory strings, which is exactly the "dies in JSON" failure this feature exists to fix.
   Fixing it properly means a second concept — *columns that need mapping, not filling* — or
   treating an unmapped category as a refusal once a taxonomy source exists. Neither is in this
   batch: one is a new field the lead has not ruled on, the other needs credentials we do not have.
   Until then, the honest reading of a green export is "nothing is missing", not "everything is
   valid".

---

## 7. Global consistency review

| check | result |
|---|---|
| 1. pydantic vs zod, 16 fields field-for-field | **YES** — `test_contract_parity.py` green; `apps/api/schemas.py` was **not modified** (0 lines changed) |
| 2. every non-2xx uses `{error:{code,message,…}}` | **YES** — one handler set in `errors.py`; `test_no_route_returns_fastapi_default_detail_bodies` probes 404 / 422 / 400 / validation and asserts no `detail` key |
| 3. `reason_code` has exactly five values | **YES** — `ReasonCode` untouched; pinned in pytest, in node tests, and by a Postgres CHECK |
| 4. no 501 was silently turned into "looks successful" | **YES** — 501 remains for checkout, webhook, rerender, on-model, binary upload, non-HS256 JWT. The 202 create does **not** claim work: job `queued`, assets `null`, `upload_url: null` |
| 5. snake_case on both sides, no alias drift | **YES** — `ListingSource.{item_id, prettified_url, on_model_urls}`, `Comp.price_cents`; the one camelCase option (`startPriceCents`) was renamed to `start_price_cents` during this review; no pydantic aliases anywhere |
| 6. the new DB columns did not disturb the wire contract | **YES** — `jobs.provider / model_version / cost_micros` and `profiles.is_anonymous / merged_into` are **database-only audit columns**. `Job` still has its 11 frozen fields and `ItemAttributes` its 16; the parity test is green and `CONTRACT_VERSION` is still `1.0.0` on both sides |

**IS_PASS: YES** — with the caveats in §2 standing as known, documented gaps (SQL not re-run on
PostgreSQL 15/16, HS256-only auth, empty eBay category, no job worker, `rectify()` pixel pipeline
missing).

### `packages/vision/rectify.py` — precise status

**Pure geometry is implemented and live; the pixel pipeline is not.** Concretely:

* live and tested (`packages/vision/tests/test_rectify_geometry.py`, 20 tests):
  `solve_homography` (4-point DLT, 8×8 Gaussian elimination with partial pivoting),
  `apply_homography`, `invert_homography`, `canonical_size`, `target_rect`,
  `homography_for_quad`, `warp_quad`, `clamp_to_frame`, `quad_area`, `determinant`.
  Round-trip error is asserted `< 1e-6` on five hostile quads and a known perspective matrix is
  recovered to `< 1e-6`.
* still raising `StageNotImplemented` (line: `def rectify(...)`): JPEG decode, alpha-PNG read,
  bilinear resample under the homography, `#F2F1EE` composite, contact shadow. Those need a codec
  and real fixtures, and E2-T3's SSIM check is what would prove them. No half-working wrapper was
  left behind: the entry point refuses and says which half is missing.

So: **E2-T1 is ~40% done — the maths, not the images.** It is not "P1 not started", and it is not
"done" either.

---

## 8. Files touched

New:
`infra/migrations/0001_init.sql`, `infra/migrations/verify/{local_supabase_shim,rls_isolation_check,constraints_check}.sql`,
`apps/api/{errors,auth,repository,usage,exporters,requests,web,devtools}.py`,
`apps/api/jobs/{__init__,state_machine}.py`,
`apps/api/routers/{jobs,exports,usage,billing}.py`,
`apps/api/tests/{conftest,test_items,test_isolation,test_jobs,test_usage,test_auth,test_exports}.py`,
`packages/shared/src/csv.ts`, `packages/shared/src/marketplaces/{types,ebay,poshmark,vinted,index}.ts`,
`packages/shared/src/listing/{copy,pricing}.ts`,
`packages/shared/test/{marketplaces,copy,pricing}.test.ts`,
`packages/vision/tests/test_rectify_geometry.py`, `docs/HANDOFF-engineer.md`.

Rewritten in place: `apps/api/main.py`, `apps/api/routers/items.py`,
`apps/api/tests/test_scaffold.py`, `packages/shared/src/index.ts`, `packages/vision/rectify.py`.

**Not touched** (deliberately): `docs/CONTRACTS.md`, `docs/PLAN.md`, `docs/PRD.md`,
`docs/METRICS.md`, `BRIEF.md`, `.gitignore`, `apps/api/schemas.py`,
`packages/shared/src/schemas.ts`, `packages/shared/src/analytics.ts`, `pyproject.toml`,
`docs/DEPS.md`. No `git commit` was run.

---

## 9. Next commands

```bash
make test && make lint                       # 163 pytest + 72 node, both green
make dev                                     # :8000, local_dev auth mode
curl -s localhost:8000/readyz | jq           # auth_mode + missing credentials

# SQL re-verification (local, shimmed):
dropdb --if-exists snaplist_migcheck && createdb snaplist_migcheck
psql -v ON_ERROR_STOP=1 -q -d snaplist_migcheck -f infra/migrations/verify/local_supabase_shim.sql
psql -v ON_ERROR_STOP=1 -q -d snaplist_migcheck -f infra/migrations/0001_init.sql
psql -d snaplist_migcheck -f infra/migrations/verify/rls_isolation_check.sql      # 4/4
psql -d snaplist_migcheck -f infra/migrations/verify/constraints_check.sql        # 14/14

# The three things that unblock the most:
#   1. E5-T3 worker            → jobs stop being permanently queued
#   2. E2-T1 pixel pipeline    → assets exist, exports stop refusing on prettified_url
#   3. eBay Taxonomy creds     → *Category resolves, warnings drop to zero
```

---

## 12. Addendum — E1-T1 runtime probe (Pip, 2026-10-28, second working session)

Scope claimed this session: **E1-T1 only**. Everything in §1–§9 above is Kou's and is untouched;
nothing in `apps/api/`, `infra/` or `evals/` was modified. No new dependencies (stdlib only), so
`docs/DEPS.md` needed no row.

### What shipped

* `packages/vision/runtime.py` — `probe()` returns `RuntimeInfo(device, backend, torch_available,
  hosted_available, reason, notes)` and `RuntimeInfo.stage_meta()` carries the decision into the
  `StageMeta` every stage must populate. Policy, first rule that fires wins:
  1. explicit override `SNAPLIST_SEGMENT_BACKEND=birefnet|u2net|sam2_hosted` — an unknown value
     **raises** rather than silently swapping ("no silent model swap" cuts both ways);
  2. `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` both set → `sam2_hosted` (contract primary, runs on
     Modal GPUs, so device does not matter);
  3. torch sees `cuda`/`mps` → local `birefnet`;
  4. CPU only → cheap `u2net`, **with a note stating the quality downgrade**.
  torch itself is import-guarded: absent torch ⇒ device reported as `cpu` with a note saying
detection was impossible — a missing accelerator is a logged fallback, never an exception
(E1-T1 DoD). `probe()` performs zero network calls (proven by a socket-blocking test).
* `packages/vision/tests/test_runtime.py` — 10 tests; every policy branch is forced by faking
  `_detect_torch`/`_hosted_available`, so no branch is reachable only in production.
* `packages/vision/tests/test_imports.py` — `runtime` added to the MODULES import guard.

### Acceptance output (verbatim, this machine)

```
$ .venv/bin/python -c "from packages.vision.runtime import probe; print(probe())"
RuntimeInfo(device='cpu', backend=<SegmentBackend.U2NET: 'u2net'>, torch_available=False,
hosted_available=False, reason='cpu-only machine without hosted credentials: cheap backend by policy',
notes=("torch is not installed: device detection is unavailable, reporting 'cpu'; no local
accelerated backend can run in this process", 'no CUDA/MPS and no hosted credentials: selecting
the cheap backend (u2net); segmentation quality will be lower — stated here, not silent'))
```

### Suite after this session

`make test` → **72 node + 173 pytest** (was 72 + 163), 0 failed, 0 skipped. `make lint` → all
green (one E501 in the MODULES line during the round, fixed before landing).

### What E1-T1 deliberately does NOT cover

* No backend is *implemented* — `segment()` still raises `StageNotImplemented` (E1-T2).
* The `mps`/`cuda` branches were exercised by faking `_detect_torch`, not by running torch:
  this machine's venv has no torch, so "birefnet runs on MPS" remains **UNMEASURED**.
* No `SNAPLIST_SEGMENT_BACKEND` row was added to `.env.example` — the variable is documented in
  the module docstring only. Adding it touches a lead-owned file; flagged here instead per the
  do-not-edit-another-role's-directory rule.

Next command (unchanged from §9): the three unblockers stand; E1-T3 (`matte`, local, no network)
is the next vision ticket that does not need credentials.
