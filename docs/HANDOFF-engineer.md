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
| P1 | E2-T1 rectify **pixel pipeline** | **done — Pip, 2026-10-28, second session (§15)**; contact shadow + real quad estimate still owed | §15 |
| P2 | E7-T4 comps pricing (offline) | **done**, `insufficient_data` under 5 comps | §5 |
| P1 | E1-T1 runtime probe (`packages/vision/runtime.py`) | **done — Pip, 2026-10-28, second session** | §12 — acceptance command output pasted, 10 unit tests, `make test`/`make lint` re-run green |
| P1 | E1-T3 matte (`packages/vision/matte.py`) | **done — Pip, 2026-10-28, second session (§14)**; golden-fixture histogram acceptance still owed to E0-T1 | §14 |
| — | E1/E3/E4 vision + VTON stages | **untouched stubs**, as instructed | — |
| — | E5-T3 job worker | **built — Pip, 2026-10-28, second session (§13)**: claim/run/retry/recover all real; every built-in handler still terminates `failed(INTERNAL)` until the vision stages land | §13 |
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

---

## 13. Addendum — E5-T3 job worker (Pip, 2026-10-28, same session as §12)

Scope claimed: **E5-T3 (local seam)**. The state machine and the ledger were already real;
what was missing is the process that moves a job, so every job sat in `queued` forever.

### What shipped

* `apps/api/jobs/worker.py` — `JobWorker` with:
  * `claim_next()` — peek the oldest queued job **inside one `begin immediate` transaction**
    and move it `queued -> running` through the state machine. Two workers cannot claim the
    same row (test proves it). The production claim stays `for update skip locked`, already
    documented in `infra/migrations/0001_init.sql`; nothing here pretends to be a distributed lock.
  * `run_once()` / `run_pending(limit)` — exception mapping is the whole policy:
    `PipelineUnavailable` / `StageNotImplemented` → **terminal `failed(INTERNAL)` immediately**
    with error_detail naming the PLAN ticket (retrying a deterministic stub is theatre);
    anything else → retryable, the 3-claim budget decides, then the refund fires.
  * `recover_interrupted()` — startup sweep: jobs found in `running` are re-queued with
    `attempts += 1` (CONTRACTS §3 treats a lost heartbeat as retryable) or failed terminally
    when the budget is spent. A recovered job can never surface `done` without its assets,
    because `done` is only written by the transition that observed the handler return.
  * CLI: `python -m apps.api.jobs.worker [--database PATH] [--limit N]`, always
    recover-then-drain.
* `apps/api/repository.py` — two read-only protocol methods: `peek_next_queued_job()` and
  `list_jobs_by_state()`. The claim transition itself stays in the state machine, so no
  backend can invent a sixth state. Postgres implementations are still owed by E5-T2's
  `PostgresRepository`.
* `apps/api/tests/test_worker.py` — 13 tests: single-claim, stub-terminal-no-budget-burn,
  transient→retry→done, permanent-transient→3 claims→failed→refunded-once, per-user refund
  isolation, success-with-assets, both crash-recovery paths, limit.

### Real output (CLI, this machine)

```
$ python -m apps.api.jobs.worker --database /tmp/snaplist-worker-demo.db
recovered 0 interrupted job(s); processed 1 job(s)
# job row afterwards:
{'state': 'failed', 'reason_code': 'internal', 'attempts': 0,
 'error_detail': 'kind=full cannot run: segment (first stage of the full pipeline) is not
 implemented (docs/PLAN.md ticket E1-T2); photo bytes are not persisted yet either
 (signed upload is still 501, E5-T4)', 'provider': 'stub'}
# quota ledger: -1 spent + 1 refunded = 0 units
```

### Suite after this addendum

`make test` → **72 node + 186 pytest** (was 72 + 173), 0 failed, 0 skipped. `make lint` → all green.

### Honest caveats

* Every job still **ends in `failed(INTERNAL)`** — that is the truth about this build (no vision
  stages, no persisted photo bytes). The worker makes the machinery real; it does not make the
  pipeline exist. Handlers are the seam the E1/E2 stages plug into.
* Heartbeat *detection* (`heartbeat_at` + Modal sweeper) is production mechanics and is not
  faked locally; `recover_interrupted()` at process start is the local equivalent.
* The kill-mid-job acceptance (paste two log tails) was exercised as the recover test
  (claim → "die" → restart → complete), not by killing a real OS process.

---

## 14. Addendum — E1-T3 matte (Pip, 2026-10-28, same session as §12–13)

Scope claimed: **E1-T3**. `matte()` went from stub to real, deterministic, offline code.

### What shipped

* **Dependencies** (recorded in `docs/DEPS.md` per the honesty rule): `pillow 11.3.0`
  (MIT-CMU) and `numpy 2.4.6` (BSD-3-Clause), added via `uv add` — both landed in the same
  commit as this note.
* `packages/vision/matte.py` — classical feather, v1:
  1. mask normalized to 8-bit single-channel (RGBA/LA masks contribute their alpha channel);
  2. 1.0σ Gaussian feather — about one pixel of soft edge;
  3. interior stays **exactly** 255 (`max(soft, hard)` — a bare blur would make a 1px strap
     see-through, and `test_thin_strap_survives` pins a 3px strap's centre column);
  4. band guard = **exact Chebyshev dilation** of the mask by 2px (PIL `MaxFilter(5)`).
* The interesting bug, caught by the test written before it was fixed: PIL's GaussianBlur is a
  box approximation, and its tail leaves `alpha=1` residue **3px** outside the mask — which is
  precisely the halo the DoD forbids. The first implementation used a blurred band guard and
  the pixel-level test failed on the residue. The fix replaces the numeric guard with a
  geometric one (dilation cannot leak), and the test now asserts the DoD directly: any pixel
  with alpha > 0 must lie within 2px of a mask pixel, measured by numpy dilation, independent
  of the blur implementation.
* `image` bytes are decoded (garbage fails loudly with `ValueError` naming the culprit) but
  their pixels are **not read** yet — v1 refinement is geometry-only. Colour decontamination
  is a future pass in this same file, and `meta.notes["color_decontamination"] = False` says so.
* `packages/vision/tests/test_matte.py` — 14 tests: halo bound, interior opacity, soft band,
  3px strap survival, RGBA masks, garbage-bytes validation for both inputs, output format,
  honest meta (seconds is a measured float, never a 0.0 placeholder), determinism, and a
  socket-trap fixture asserting **zero network calls on every single test**.

### Real output (40×40 fixture: garment slab + 3px strap)

```
alpha histogram bins [0, 1, 64, 128, 192, 255, 256): [997, 108, 74, 1, 0, 420]
# 997 fully transparent, 183 soft-edge pixels inside the 2px band, 420 opaque (= mask_px)
meta: local_feather_v1 seconds=0.00077 notes: {'feather_sigma_px': 1.0, 'halo_band_px': 2,
      'color_decontamination': False, 'network_calls': 0, 'mask_px': 420}
```

### Suite after this addendum

`make test` → **72 node + 200 pytest** (was 72 + 186), 0 failed, lint all green.

### Honest caveats

* The PLAN acceptance (`python -m packages.vision.cli` on a knit and a strap garment from the
  golden set, alpha histogram pasted) **remains open** — it needs E0-T1's real fixtures, which
  forbid synthetic entries. The histogram above is from a synthetic unit-test fixture and is
  NOT golden-set evidence.
* `matte()` is not yet wired into any job handler: the worker's `full` handler still fails
  terminal until E1-T2 (`segment`) lands ahead of it in the pipeline. Wiring order when it
  does: segment → matte, sharing one decode of the image bytes.

---

## 15. Addendum — E2-T1 rectify pixel pipeline (Pip, 2026-10-28, same session as §12–14)

Scope claimed: **E2-T1** (the pixel half; the homography maths were already done and tested).
`rectify()` went from `StageNotImplemented` to a working, deterministic, offline warp.

### What shipped

* `packages/vision/rectify.py` — `rectify(image, alpha_png, category)`:
  1. decode photo (RGB, JPEG/PNG both exercised) + alpha (L, or the alpha channel of RGBA/LA);
     garbage bytes and size mismatches raise `ValueError` naming the culprit;
  2. garment quad from the alpha bounding box — **v1 shortcut, declared in
     `meta.notes["quad_source"] = "alpha_bbox"`**; a real corner detector replaces it in this
     same file and must keep the TL,TR,BR,BL order;
  3. homography quad → canonical canvas (`homography_for_quad`, the frozen geometry);
  4. **inverse mapping** of every canvas pixel into the photo (inverse is what avoids holes),
     bilinear resample of garment and alpha, source-coordinate clamping via the existing
     `clamp_to_frame` semantics;
  5. composite onto `#F2F1EE`, sRGB PNG, long edge per `canonical_size` (≤1600px).
* The full-bleed design is pinned, not discovered: `CANONICAL_ASPECT`'s "fill the frame without
  letterboxing" means the garment quad maps onto the whole canvas and the backdrop shows through
  soft alpha. My first three pixel tests assumed a centred garment with margins and failed — the
  tests were wrong, the frozen geometry was right, and the tests now pin the actual design
  (`test_corners_sample_the_quad_and_centre_is_garment`).
* `test_rectify_pixels.py` — 13 tests: canvas size per category, ≤1600px everywhere, corner
  sampling, alpha-blend betweenness on a half-covered column, unknown-category fallback
  (`other`, no raise), empty alpha → `no_garment` ValueError, size mismatch, garbage bytes,
  honest meta, determinism, and forward-homography corner mapping at 1e-6. Socket trap on every
  test. The geometry file's stub-marker test flipped to the new contract: `rectify()` now
  validates inputs with ValueError instead of raising `StageNotImplemented`.

### Real output (400×300 gradient garment, JPEG q92)

```
canvas (960, 1200), wall 0.161s, meta.seconds=0.1614
garment_px 39600, output texture std 38.3 (the gradient survives the warp — no flat fill)
```

### Suite after this addendum

`make test` → **72 node + 213 pytest** (was 72 + 200), 0 failed. `make lint` → all green.

### Honest caveats

* **`contact_shadow` is not done** — `meta.notes["contact_shadow"] = False`. PLAN lists it in
  E2-T1; prompts/02-vision.md's soft contact shadow is a later pass in this file.
* The bbox quad means v1 "rectify" is a scale-to-canvas, not a true perspective correction. The
  SSIM eval (E2-T3) will score it; if it fails the 0.85 gate the corner detector is the fix.
* The CLI acceptance (`python -m packages.vision.cli --in evals/fixtures/bottom-03.jpg`) remains
  open on E0-T1's real fixtures — synthetic unit fixtures are not golden evidence.
* `rectify()` is not wired into a worker handler yet; the pipeline order when it is: segment →
  matte → rectify, sharing decodes.

---

## 16. Addendum — E0-T1 golden-set tooling (Pip, 2026-10-28, same session)

Scope claimed: **E0-T1 tooling only**. The golden set itself cannot be produced here — 40 real
garment photos and 20 hand masks are Bert's to provide, and PLAN E0-T1 forbids synthetic
stand-ins. What shipped is the contract around them, so the moment photos exist the set becomes
mechanical to complete and impossible to half-fake.

### What shipped

* `evals/golden/README.md` — layout (`manifest.json` + `photos/` + `masks/`), the exact entry
  shape, the shooting rules, and the **composition interpretation**: PLAN's "10 tops / 10
  bottoms / 5 dresses / 5 shoes / 5 accessories / 5 hard cases" only sums to 40 if the 5 hard
  cases are an *extra* bucket over 35 category entries. That interpretation is stated once, in
  the README, and encoded once, in the validator's constants.
* `evals/tools/validate_golden.py` — `make golden` / `python -m evals.tools.validate_golden`.
  Checks, each PASS/FAIL with counts: manifest present and a bare JSON array (so `jq 'length'`
  prints 40 and `evals/run.py` counts entries); 40 entries; required fields; unique slug ids;
  no placeholder/synthetic tokens; categories and patterns from the contract enums; `colors`
  1–3 lowercase; `material` non-empty lowercase; exactly 3 hard attributes each; exactly 5
  `hard_case: true`; the 35-entry category quotas; every photo exists **and decodes**; exactly
  20 masks, single-channel and sized like their photo.
  Exit codes: 0 complete, 1 incomplete, 2 manifest unusable (absent/bad JSON/not an array) —
  "missing" and "broken" are different fixes, same reasoning as the eval gates.
* `make golden` target. It exits 2 today, on purpose: the manifest does not exist, and an
  empty golden set must not look like a passing one.
* `evals/tests/test_golden_validation.py` — 19 tests: the complete-set happy path, all three
  fatal cases, count/quota/mask-count failures, missing and undecodable photos, RGB mask, mask
  size mismatch, duplicate ids, four vocabulary/shape violations, and the placeholder-token
  guard.

Real output today:

```
$ make golden
golden set check — /Users/bujin/Documents/Projects/SnapList/evals/golden
  [FAIL] manifest present  .../manifest.json does not exist — the golden set is empty by design
summary: INCOMPLETE — 1 blocking problem(s).
make: *** [golden] Error 2
```

### Suite after this addendum

`make test` → **72 node + 232 pytest** (was 72 + 213), 0 failed. `make lint` → all green.

### Honest caveats

* The tool cannot prove a photo is real; it rejects the obvious placeholder tokens and
  everything structural. Realness is Bert's call when he shoots them.
* `pytest`'s `tmp_path` factory trips this machine's filesystem shim (EEXIST on its own cache
  dir), so these tests own their temp directories via `tempfile.mkdtemp`. Documented in the
  fixture docstring so the next person does not rediscover it.
* Nothing writes into `evals/golden/` except Bert's future photos and manifest; the generated
  fixtures live only in temp dirs.

---

## 17. Addendum — E1-T2 `segment()` on the U²-Net path (Pip, 2026-10-28, third session)

Scope claimed: **E1-T2, U²-Net path only**. SAM 2.1 and BiRefNet still raise
`SegmentBackendUnavailable` — they are not quietly faked. `apps/api/`, `infra/`, `evals/` and
`docs/CONTRACTS.md` are untouched.

### First, a correction to §9/§12/§16

Those sections say E1-T2 needs hosted SAM 2.1 credentials *or* local weights, and that "this
machine's venv has no torch, so the local path does not work". The second half is wrong:
**torch only gates BiRefNet.** U²-Net runs through **onnxruntime**, which has nothing to do
with torch — and `probe()` already selects `u2net` on a CPU-only machine without hosted
credentials, which is exactly this machine. So the ticket that "waited on credentials" was
waiting on a 176 MB file:

| backend | what it actually needs | status |
|---|---|---|
| `u2net` | `u2net.onnx` (a file) + onnxruntime | **implemented this session** |
| `birefnet` | torch + MPS/CUDA | unavailable, says so |
| `sam2_hosted` | Modal token pair + deployed endpoint | unavailable, says so |

The distinction is worth keeping: a *weight file* is a one-time download that can be pinned
and verified; a *credential* is an account. Conflating them cost this project two sessions.

### What shipped

* `packages/vision/segment.py` — the stage, the backend dispatch, and a stage CLI
  (`python -m packages.vision.segment --in photo.jpg --out /tmp/sn1`). Deliberately *not* the
  pipeline CLI, which promises `prettified.png`/`cutout.png`/`attributes.json` and cannot
  finish until E2/E3 exist.
  * **Backend dispatch**: `u2net` runs; `birefnet`, `sam2_hosted` and `UNMEASURED` raise
    `SegmentBackendUnavailable(StageNotImplemented)`. Subclassing `StageNotImplemented` is the
    point — the worker already maps that to a terminal failure without burning the retry
    budget, because "torch is not installed" will not resolve itself between attempt 1 and 2.
  * **Policy is not repeated here.** `segment()` runs the label it is given; it never calls
    `probe()` itself. One resolver, one place to change a model.
  * **Weights are never downloaded by the stage.** Path is `SNAPLIST_MODEL_DIR` or
    `~/.cache/snaplist/models`; absent ⇒ `SegmentBackendUnavailable` with the fix in the
    message (`make fetch-models`). A stage that fetches 176 MB on first call is a network call
    inside a zero-network contract, and a four-minute stall inside a job.
  * **sha256 pin** (`8d10d2f3…b491`, verified at session load, cached): a truncated download
    fails at load instead of producing quietly worse masks for a week.
  * **Preprocessing**: 320×320, ImageNet mean/std, CHW float32 — the U²-Net contract.
  * **Postprocessing**, min-max normalised then resized to the source size, plus one thing that
    is easy to get wrong: bilinear upscaling **erodes the peak**, so the resized field is
    divided by its own maximum before quantising. Without it the mask tops out at 241 on a
    sharp field — and `matte()` (E1-T3) finds its opaque interior by looking for `== 255`, so
    the garment body would come out at alpha 254, i.e. slightly see-through. `peak_restored`
    in `notes` records whether the correction fired.
  * `notes` carries: `model_file`, `model_sha256`, `input_size`, `providers`,
    `inference_seconds`, `mask_px`, `mask_coverage`, `raw_peak`, `peak_restored`,
    `degenerate`, `network_calls: 0`, and a `quality_note` stating the downgrade in words.
* `Makefile` — `make fetch-models`. Not part of `install`: 176 MB and a network call must be an
  explicit act, not a side effect of a clone. Idempotent, sha256-verified, and a mismatch
  discards the `.part` file rather than leaving a half-model on disk.
* `docs/DEPS.md` — `onnxruntime 1.31.1` (MIT; transitives `flatbuffers` 25.12.19 Apache-2.0,
  `protobuf` 7.36.2 BSD-3-Clause) and a new **Model weights** section for `u2net.onnx`
  (Apache-2.0, U-2-Net by Xuebin Qin, redistributed by rembg/MIT), with
  `$/item = 0.00 provider spend` stated as such — local CPU, 0.73 s measured — and
  machine-attributed cost explicitly **not claimed**.
* `packages/vision/tests/test_segment.py` — 26 tests. None of them open the weights: a
  `FakeSession` returns a known `d0`, so a red test always means a bug in this repo, never a
  slow download. `packages/vision/tests/test_imports.py` updated: `segment` is no longer in the
  stub list, and the comment says why.

### Real output (this machine, real weights)

```
$ python -m packages.vision.segment --in /tmp/snapsmoke/tee.jpg --out /tmp/snapsmoke/out
probe: backend=u2net device=cpu (cpu-only machine without hosted credentials: cheap backend by policy)
wrote /tmp/snapsmoke/out/mask.png (16839 bytes)
{ "backend": "u2net", "device": "cpu", "stage": "segment",
  "seconds": 0.868, "cost_usd": null,
  "notes": { "inference_seconds": 0.7297, "input_size": 320,
             "mask_px": 233209, "mask_coverage": 0.1794,
             "model_file": "/Users/bujin/.cache/snaplist/models/u2net.onnx",
             "model_sha256": "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491",
             "network_calls": 0, "peak_restored": false, "raw_peak": 1.0,
             "providers": ["CPUExecutionProvider"],
             "quality_note": "u2net is the cheap backend selected by policy on a CPU-only machine: …" } }
```

The photo is **synthetic** — a drawn tee on a noisy plaster background, because the repo has no
real garment photo (E0-T1). That makes the following a smoke test, not evidence:

* IoU against the drawn silhouette: **0.9956** at `mask >= 128` (0.9844 at 64, 0.9871 at 192).
  The ground truth is a polygon I wrote, so this number proves the plumbing follows a shape; it
  says nothing about a real knit, a real strap or a real shadow. **The DoD's mean IoU ≥ 0.92
  remains UNMEASURED.**
* 200,301 pixels at exactly 255 — the mask has a genuine opaque interior for `matte()`, which
  the `peak_restored: false` line explains: a real U²-Net field saturates, so the resize did
  not erode anything on this input.
* `segment → matte` wired by hand (§"not wired" below), alpha histogram over the 1000×1300
  frame: `[1065946, 12304, 3474, 3594, 14381, 200301]` for bins
  `[0,1,64,128,192,255,256)` — 200,301 fully opaque, 33,753 soft, 0 halo outside the 2px band.

### Mutation check (green is not evidence)

Five deliberate breakages, each run against one named test:

| mutation | result |
|---|---|
| remove the peak-restore division | **survived at first** → fixed the fixture, now killed (max 241 ≠ 255) |
| soft mask → hard threshold at 0.5 | killed, 3 failure lines |
| remove the degenerate-field guard | killed, 2 |
| remove the weights-existence check | killed, 2 |
| disable the sha256 comparison | killed, 2 |

The survivor was worth the detour: with a *smooth* synthetic field the erosion is ~0.0001, which
rounding to uint8 hides completely, so the test could not see the line it was supposed to guard.
The fixture is now a sharp spike with a comment explaining that it exaggerates the effect on
purpose — and noting why the mechanism still matters for real photos below 320 px, where the
field is downscaled and the erosion is not a rounding detail.

### Suite after this addendum

`make test` → **72 node + 258 pytest** (was 72 + 232), 0 failed, 0 skipped. `make lint` → all
green (ruff + `tsc --noEmit`). `make golden` → still exit 2, unchanged and correct.
`make evals` → every metric still `UNMEASURED`, `GATE NOT EVALUATED`.

### Honest caveats

* **Nothing is wired into the worker.** `segment → matte → rectify` is still a hand-run chain,
  and wiring it would not make a job complete anyway: photo bytes are still not persisted (E5-T4
  signed upload is 501). Last session's summary said segment would let a job "run to the end" —
  that was wrong, and this is the correction. What segment unlocks is E1-T4 (the IoU report) and
  E1-T3's acceptance command, both of which need the golden set first.
* **The DoD metric is UNMEASURED.** 20 hand masks are Bert's; `evals/run.py` cannot report a
  segmentation IoU until they exist, and the 0.9956 above is against a polygon, not a hand mask.
* **No `no_garment` decision.** CONTRACTS §4 has the reason code; the threshold that would fire
  it is unmeasured, so `mask_coverage` is reported and the decision waits. A guessed threshold
  would reject real photos and nobody would know why.
* **CPU only.** `CoreMLExecutionProvider` is available on this machine and unused on purpose:
  switching to it without a measured IoU/latency comparison is a silent model swap in hardware
  clothing. `notes["providers"]` always says what actually ran.
* **`evals/run.py` now has one stale sentence**: "the golden set and the pipeline stages do not
  exist yet". Segment exists. It is `evals/`'s file, so this is flagged rather than edited; the
  right time to fix the wording is E1-T4, when a number replaces the `UNMEASURED`.
* `SNAPLIST_MODEL_DIR` and `SNAPLIST_SEGMENT_BACKEND` are still not in `.env.example`
  (lead-owned file). Both are documented in the module docstrings and the Makefile.
* §16 warns that `pytest`'s `tmp_path` trips this machine's filesystem shim. These 26 tests use
  `tmp_path` and passed 4 consecutive full runs; if it ever flakes, the fix is §16's
  `tempfile.mkdtemp` pattern, not a change to the stage.

### Next commands

```bash
make fetch-models                     # once per machine; verified against the pinned sha256
python -m packages.vision.segment --in <a real garment photo> --out /tmp/sn1
```

With 20 hand masks in `evals/golden/masks/`, E1-T4 is a report away: the mask format is already
`mask_px`-enumerable and `meta` already carries everything the report needs.

---

## 18. Addendum — the web-sourced provisional set, and the first segmentation IoU (Pip, same day)

### What was asked, and what the web actually had

Bert's instruction was "go find the photos". I did, and the finding is a negative one that
needs to be written down before the artefacts:

* **One source was reachable.** Openverse's API times out from here, Unsplash's search
  endpoint answers `307 Authorization required`, Pexels returns 403, the Met's collection API
  is 410. That leaves Wikimedia Commons, which is a fine source for encyclopaedic material
  and a poor one for seller photos.
* **672 candidates** survived licence filtering (CC0 / public domain / CC BY; share-alike and
  non-commercial excluded so the repo's own licence is not complicated), mime, and a 700 px
  size floor. They came from 40+ categories, targeted full-text searches, and depth-first
  paging into subcategories (`Trousers/Breeches`, `Skirts/Bases (fashion)`, …).
* **89 survived a "can a colour mask isolate this" screen** (uniform border, plausible
  foreground fraction, one dominant blob).
* **25 were picked by eye; 17 survived mask verification.** The rest were dropped for masks
  that missed a hem, a cuff or a sole, or because the photo was a fencing manual, a letter, a
  textile fragment or a person wearing the garment.
* **The resulting composition is 2 top / 6 bottom / 4 shoe / 4 accessory / 1 outerwear /
  0 dress.** `docs/PLAN.md` E0-T1 wants 10/10/5/5/5. Ten bottoms did not exist in a clean
  licence; dresses did not survive verification at all.

So the golden set stays empty, `make golden` keeps exiting 2, and every number below carries
the word *provisional*. Substituting a convenient set for the contract set, silently, is how a
project ends up quoting an IoU that only holds on museum mannequins.

### What shipped

* `evals/golden-provisional/` — 17 entries, every one with a mask:
  * `photos/` 1280 px Commons thumbnails (not the multi-megabyte originals; the pipeline caps
    at 1600 px anyway, and 4.5 MB is committable where 60 MB is not);
  * `masks/` 8-bit single channel, same size as their photo;
  * `manifest.json` — the E0-T1 shape **plus** two extra keys per entry that the validator
    ignores and the report prints: `mask_source` and `source{provider,page,file,licence,artist}`;
  * `CREDITS.md` — per-photo licence and author, because a CC BY photo without attribution is
    not licensed;
  * `README.md` — what the set is, what it is not, and the two biases stated plainly.
* `evals/tools/provisional_masks.py` — the mask generator: background-colour distance → Otsu
  → corner flood fill → keep the body at the centre of mass → 3×3 close → 1 px feather.
  numpy + PIL only, no new dependency, ~1 s per photo.
* `evals/tools/seg_iou.py` — E1-T4, first half: segment every entry, binarise both masks at
  128, report per-image IoU, per-category means, and the provenance header (git sha, backend,
  device, model sha256, n, mask source).
* `evals/tools/validate_golden.py` — `--profile {golden,provisional}`. `golden` is still the
  default and still strict; `provisional` checks structure, prints the composition as
  *reported, not gated*, and renders a banner saying in as many words that a set validated
  under it is not the golden set.
* `Makefile` — `golden-provisional`, `masks-provisional`, `seg-iou`.
* `evals/tests/test_provisional_set.py` — 16 tests, no weights, no network.

### The first real number (provisional, n=17)

```
backend u2net · cpu · model 8d10d2f3bb75ae3b… · mean per-image segment 0.217 s
segmentation mean IoU  0.9416      median 0.9697      worst 0.7090 (shoe-04)  best 0.9925
by category            bottom 0.9794 · outerwear 0.9925 · top 0.9653 · accessory 0.9186 · shoe 0.8833
```

It clears the DoD's 0.92. **That is the flattering direction and it is not evidence.** The
photos were selected for colour separability, the masks are algorithmic, there are no dresses
and no hard cases. On real seller photos — a black knit on a black chair, phone camera, one
lamp — this will be lower, and the gap between the two numbers is itself the useful finding.

The two worst rows are informative rather than alarming: `shoe-04` (worn leather tennis shoes,
predicted coverage 0.495 against a reference 0.396) is the model pulling the shadow in;
`shoe-03` is a pale shoe on a pale background.

### Mutation check

| mutation | result |
|---|---|
| drop `.copy()` before `ImageDraw.floodfill` | killed — this is the Pillow 11 trap: `fromarray` is read-only and floodfill **silently** no-ops, which produced coverage 1.0 for every photo until the corner assertion caught it |
| IoU denominator → `ref.sum()` instead of the union | killed |
| provisional profile stops requiring a mask | killed |

### Suite after this addendum

`make test` → **72 node + 274 pytest** (was 72 + 258), 0 failed, 0 skipped. `make lint` clean.
`make golden` → still exit 2. `make golden-provisional` → exit 0.

### What is still owed

* **E1-T4, second half**: a `segmentation mean IoU` row in `evals/run.py` and a gate. It is
  not there because a gate over algorithmic masks on 17 colour-separable photos would be a
  gate that passes for the wrong reason. It lands with the hand masks.
* **The 20 masks.** Nothing in this session changed that: an algorithmic mask is a stand-in
  for the *plumbing*, never for the measurement.
* A dress and ten bottoms still do not exist from a clean-licence web source.

**ADR-010 status note (2026-11-08) — three-tier mask provenance, not an unblock.** The old
"20 hand masks" wording above is the 2026-10-28 record and stays. ADR-010 adds two more tiers
besides hand-drawn: `ai-assisted:<model>+point-reviewed` (AI proposal + evidenced human point
review) and machine-made. The current 20 local masks are all machine-made (18
`algorithmic:matting`, 2 `ai:google-ai-mode-nano-banana`); they are **not** grandfathered and
must be re-made under the new rules with the review recorded in
`evals/golden-review/point_review_register.json`. That register is `0/20` reviewed with the
edit-distance threshold `UNSET`, so U1 is UNMET, E1-T4's hand-mask gate stays open and E2 must
not start. `evals/mask_provenance.py` is now the single source of truth for all four consumers;
`python -m evals.tools.validate_point_reviews` exit 0 means only "register structurally valid".
The IoU semantics after the re-make belong to a follow-up ticket.

---

## 19. Addendum — the harness measures segmentation, and the repo is on GitHub (same day, `7122c28`)

E1-T4's second half landed: `python -m evals.run --suite vision` prints
`0.9416 (n=17, provisional)` in the segmentation row instead of `UNMEASURED`, with a
provenance block naming backend, device, mask source and worst case, and the "this set is
optimistic by construction" caveat inside the same report.

* Set selection is ordered — `evals/golden` wins as soon as it has one mask, so Bert's photos
  replace the stand-in with no code change (a test pins the ordering).
* Prerequisites are checked, never assumed: no masked set / no weights / a raising measurement
  each give `UNMEASURED` **plus the reason**, because "no number" and "no number because you
  have not run `make fetch-models`" are different fixes.
* The gate is still E0-T5's. A measured row does **not** become an exit code; the banner reads
  `n=17 measured, but the gate itself is E0-T5. This is NOT a pass.` and `--gate` still exits 2
  with the same refusal — a gate over 17 colour-separable photos would pass for the wrong reason.
* Two stale things fixed while in there: the report's "the pipeline stages do not exist yet"
  sentence (untrue since `da3cdfe`), and the missing git SHA in the header (E0-T2 asked for it).

**Remote**: `https://github.com/bujinwang/snaplist` — **private**, `master` tracking
`origin/master`. Private on purpose: the repo carries BRIEF/PRD/METRICS/INTERVIEWS. Making it
public is a decision about publishing the business plan, not a `gh` flag.

**E0-T3 is now half-unblocked and still not done.** A push is a real CI trigger, so the missing
piece is the workflow file itself — and it needs one design call first: `make golden` exits 2
while the golden set is empty, so a naive `make ci` would make every PR red for a reason that is
correct but useless. The workflow has to run `make test lint` and the *report-only* eval path,
and leave the golden check to the release gate.

Suite after this: 72 node + 277 pytest, lint clean, `make golden` still exit 2.

---

## 20. Addendum — the search-engine experiment (same day, after Bert pushed back)

Bert's objection was fair: I said "the web cannot supply this set" after testing only
*image APIs*. Bing/Google Images were never tried, and "no API key" is not "no images". So I
tried them. Bing works — `bing.com/images/search` returns ~25–35 results per query and embeds
the media URL in the result markup.

### What the harvest actually produced

| step | count |
|---|---|
| queries issued (12 + 12, "preloved / second hand / used / thrifted / vinted / depop") | 24 |
| image URLs after filtering stock-photo domains (freepik, vecteezy, dreamstime, shutterstock, …) | **636** |
| passed an automatic "can a colour mask isolate this" screen | **75** |
| downloaded at up to 1600 px and inspected by eye in batches of twelve | 40 |
| **actually one garment, with a correct mask** | **9** |

**The 9/636 pass rate is the finding, not an accident.** Two separate things went wrong, and
the second one is the interesting one:

1. My index picking was sloppy. Labels sit at the bottom edge of each contact-sheet tile and I
   read a few rows off, so a third of the picks were the wrong image entirely. My error, and it
   cost a whole verification round.
2. **The "maskable" screen is a bias machine.** It rewards a uniform background, and the single
   most uniform-background class in a garment-photo search is *wholesale inventory*: racks of
   jeans on a flat floor, bales of clothes in a warehouse, two hundred shoes laid out in rows.
   Those pass the screen and are worthless as fixtures. Meanwhile a real Vinted photo — one
   dress on a bed, phone camera, cluttered room — fails it, because there is nothing uniform
   to key on.

So the funnel is self-defeating in exactly the way §18 predicted for Commons, now confirmed on
a 636-image pool instead of a 40-image sample: **"looks like a seller photo" and "a colour mask
can isolate it" are anti-correlated.** Any automatic screen picks the wrong half.

### The licensing point, stated properly this time

I previously said the web "does not have" the photos. It has plenty. What it does not have is
photos anyone may redistribute: the top results for `t-shirt flat lay` are freepik/vecteezy/
dreamstime listings — paid stock, some watermarked — and the marketplace photos belong to the
shops that shot them. Copying either into this repo (or any repo) is infringement regardless of
whether the repo is private.

That is a licensing constraint, not an availability one, and it is why the local cache is the
only honest home for internet photos.

### What was kept

* `SNAPLIST_GOLDEN_DIR` in `evals/run.py` — a set can now live outside the tree. It exists for
  exactly this case: fixtures that may be measured but not shipped. Provenance, `n` and the
  "outside the repo" fact all appear in the report.
* `~/.cache/snaplist/golden-web/` — 9 verified photos + masks + `sources.json`, with a
  `README.txt` saying what they are, what they are not, and that they must never be committed.
  Not a fixture set; not referenced by any make target.
* The harvester itself is **not** committed. A scraper for all-rights-reserved images is not
  something this repo should carry.

### The conclusion I should have reached three hours earlier

Everything points the same way: 40 photos take Bert fifteen minutes with a phone, and they are
the only version of this set that is licensed, representative, and cheap. The web path costs
more effort than shooting, produces a worse distribution, and cannot be committed. If the
photos never arrive, the provisional Commons set stays and every number stays labelled.

Suite unchanged: 72 node + 277 pytest, lint clean.





---

# E5-T4 — Storage: signed upload URL + asset write-back

Run date: 2026-11-04. Every command below was executed in this working tree, on this machine.
Suite: **419 pytest + 72 node**, `make lint` clean. No commit (the lead收口).

**Second round (after QA's CONDITIONAL PASS).** Five defects the reviewer found and could
reproduce are fixed, and each fix has a test that fails without it:

| # | defect | fix | test that now kills it |
|---|---|---|---|
| A | `?sig=%C3%A9` → **503** on an unauthenticated route (`compare_digest` raises `TypeError` on non-ASCII), and `?sig=<valid>%20` → **201** because `_sig_of` `.strip()`ed it back into a valid value | shape check (`^[0-9a-f]{64}$`) before the comparison; `_sig_of` no longer trims | `test_a_non_ascii_signature_is_403_not_503_on_{upload,read}`, `test_a_signature_with_a_trailing_space_is_403`, `test_verify_signature_returns_false_for_any_non_hex_value` |
| B | `write_back` "all or nothing" covered digests but not keys: asset #1 landed on disk before asset #2's traversal `kind` was refused → orphan object, zero rows | keys planned first, then `preflight` validates **every key** and rejects `/` in `kind` | `test_a_kind_that_escapes_the_root_writes_nothing_at_all` (scans the disk), `test_a_kind_with_a_path_separator_is_refused` |
| C1 | unsigned PUT at a non-existent item must be 403, not 404 (existence oracle) — true, but unguarded | documented the ordering in `_item_for_key` | `test_an_unsigned_put_at_an_unknown_item_key_is_403_not_404` (+ the signed case still 404) |
| C2 | `exp == now` boundary was unstated and unguarded | **chosen: inclusive**, documented in `verify_signature`'s docstring | `test_the_expiry_boundary_is_inclusive` |
| C3 | — | same as B; the disk scan is the independent assertion | — |
| D | a replayed PUT after `job.done` silently replaced the source while the renders still described the old photo | `Repository.has_finished_job` + **409** before any byte is overwritten | `test_a_finished_item_refuses_a_new_source_photo_with_409`, `test_an_unprocessed_item_still_accepts_a_replayed_upload` |
| E | the mutation table's "md5 after restore" column was wrong (it listed a pre-delivery digest), so the numbers could not be trusted | §4 rewritten: every mutant has its exact patch, its measured red count, and the **delivered** md5 | — |
| F | three known gaps + `devtools --path` writing a URL | §7.1–7.3 below; `devtools.attach_asset` now calls `validate_key` before opening the database | `test_devtools_refuses_a_storage_path_that_is_not_an_object_key`, `test_devtools_accepts_a_real_object_key` |

**Third round (small).** Two survivor mutants got tests, and four sentences of documentation
were corrected. QA's `M13` — deleting `validate_key(key)` from `preflight` — turned **zero**
tests red, because the B tests attacked through `kind="../../escape"`, which the separator check
catches first. The one input that reaches the key-length rule unimpeded is an over-long `kind`
(190 characters → a 274-character key), so
`test_a_kind_that_overflows_the_key_limit_writes_nothing_at_all` closes that door and M13 is now
red on its own (§4). The second survivor was the row-after-bytes ordering in `write_back`:
`test_rows_are_written_only_after_the_bytes_exist` hooks `set_asset` and asks whether the object
is already readable, so swapping the loops is a red test (`M6b`) rather than an undocumented
intention. §7 gained the sentence that matters most — **the 409 only exists on the `LocalStorage`
transport** — plus the admission that `is_relative_to(root)` has no test of its own, and
`apps/api/requests.py:20-26` joined the stale-prose list. A `jobs (item_id)` index was added to
the SQLite mirror so `has_finished_job` stops scanning the `state` slice.


## 1. What changed

| file | change |
|---|---|
| `apps/api/storage.py` | **new.** `Storage` protocol + `LocalStorage` (filesystem + HMAC) + `SupabaseStorage` (REST via `httpx`) + `UnavailableStorage` + `build_storage()`. Key validation, signature **shape** check, TTLs, content-type allow-list. |
| `apps/api/routers/uploads.py` | **new.** `PUT /v1/uploads/{key}` and `GET /v1/assets/{key}`, capability-URL only (no bearer); 409 when the item is already processed. |
| `apps/api/jobs/writeback.py` | **new.** `AssetWrite` / `write_back()`: plan keys → preflight (digests **and** keys) → bytes → rows. |
| `apps/api/devtools.py` | `--path` is validated as an object key; the docstring example is now a key, not a URL. |
| `apps/api/settings.py` | seven storage knobs. TTLs/ceilings have defaults; the **signing secret has none**. Each accepts `SNAPLIST_STORAGE_X` or `STORAGE_X`. |
| `apps/api/main.py` | `create_app(storage=...)`, `app.state.storage`, uploads router, `/readyz` split into `repository` + `storage` + `storage_configured`. |
| `apps/api/routers/items.py` | `upload_url` is a signed URL (also on an idempotent replay); `get_item` signs the three AssetPack URLs at read time; the multipart 501 points at the two-step flow. |
| `apps/api/repository.py` | `assets.byte_size` + `assets.meta` (+ in-place `alter table` for an old `SNAPLIST_DB`), `set_asset(byte_size=, meta=)`, `to_asset_pack(url_for=...)`, claim predicate on `kind='source'`, `has_finished_job`, and the `jobs (item_id)` index that lookup needs (`EXPLAIN QUERY PLAN` now says `SEARCH jobs USING INDEX jobs_item`). |
| `infra/migrations/0001_init.sql` | **comment only**: the documented Postgres claim query carries the `exists (… kind='source')` predicate, and the upload-time `state='done'` lookup is written down beside it. No schema change. |
| `apps/api/tests/test_storage.py` | **new**, 87 tests. |
| `apps/api/tests/test_items.py`, `test_worker.py`, `test_scaffold.py` | assertions updated for the new wiring (see §6.1). |

## 2. Acceptance — real uvicorn, real curl, real bytes

Server: `SNAPLIST_DB=/tmp/fin/db.sqlite SNAPLIST_STORAGE_DIR=/tmp/fin/objects
SNAPLIST_STORAGE_SIGNING_SECRET=fin-secret .venv/bin/python -m uvicorn apps.api.main:app --port 8020`
(`x.jpg` = 37 bytes, sha256 `8f7fee3276c2ad1e…233798aac6`)

```
$ curl -s -X POST localhost:8020/v1/items -H "x-snaplist-user-id: $U" -H 'content-type: application/json' \
       -d '{"filename":"x.jpg","content_type":"image/jpeg"}'
{
    "item_id": "0bb13feb-3ad9-42f9-8a7a-8120f152f9c4",
    "job_id": "9a6c30af-af8f-4c01-84cd-1936eb2fdac2",
    "upload_url": "http://localhost:8020/v1/uploads/items/11111111-1111-4111-8111-111111111111/0bb13feb-3ad9-42f9-8a7a-8120f152f9c4/source.jpg?exp=1793825648&sig=fbbeefe1891e30423714f726b8167a52f458bc79b3b69993403826097d8f8254"
}

$ curl -s -o /dev/null -w "%{http_code}\n" -X PUT --data-binary @x.jpg -H "content-type: image/jpeg" "$UV"
201
$ curl -s -o /dev/null -w "%{http_code}\n" -X PUT --data-binary @x.jpg -H "content-type: image/jpeg" "${UV%%\?*}"
403
$ curl -s -o /dev/null -w "%{http_code}\n" -X PUT --data-binary @x.jpg -H "content-type: image/jpeg" "$UV"   # replay, job queued
200
```

Round-2 signatures on the same server (`KEY = items/<user>/<item>/source.jpg`):

```
$ curl ... ".../v1/uploads/$KEY?exp=9999999999&sig=%C3%A9"      # A
403
{"error":{"code":"forbidden","message":"Signature is missing, expired or not valid for this object.","retry_after_seconds":null,"details":{"purpose":"upload"}}}
$ curl ... ".../v1/assets/$KEY?exp=9999999999&sig=%C3%A9"
403
$ curl -X PUT ... "${UV}%20"                                    # A, trailing space
403
```

After the job reached `done` (D) — a freshly signed URL for the same key, same 900 s window:

```
$ curl -s -X PUT --data-binary @x.jpg -H "content-type: image/jpeg" "$UV_FRESH"
409
{
    "error": {
        "code": "invalid_request",
        "message": "This item has already been processed, so its original photo can no longer be replaced: the assets derived from it would no longer match. Create a new item (POST /v1/items) to upload a different photo, or POST /v1/items/{item_id}/rerender to redo the rendering from the photo already stored.",
        "retry_after_seconds": null,
        "details": {"item_id": "0bb13feb-3ad9-42f9-8a7a-8120f152f9c4"}
    }
}
```

AssetPack after a write-back of prettified/cutout/on_model (`write_back()`, one row per slot):

```
$ curl -s localhost:8020/v1/items/$ID -H "x-snaplist-user-id: $U"
{
    "item_id": "0bb13feb-3ad9-42f9-8a7a-8120f152f9c4",
    "prettified_url": "http://localhost:8020/v1/assets/…/prettified.png?exp=1793828406&sig=83601c3e7ea656c11aacf4612756d20c590af63d590fbbb2bff9e95128245f62",
    "cutout_url":     "http://localhost:8020/v1/assets/…/cutout.png?exp=1793828406&sig=34d1ceec32d310d0f0ed0406a34aaca3d6a8f9f6783c3ca1346ddf84627e8124",
    "on_model_urls": ["http://localhost:8020/v1/assets/…/on_model-1.png?exp=1793828406&sig=d11258a35c5c31826d813adb3bf3b26f5319769854bb96a90a1174206eeae8c0"],
    "attributes": null
}

$ curl -s -o fetched.png -w "%{http_code} %{content_type}\n" "$prettified_url"
200 image/png
$ shasum -a 256 fetched.png x.jpg
8f7fee3276c2ad1eaf77e52204448da968fbef62fef0861dc2a631233798aac6  fetched.png
8f7fee3276c2ad1eaf77e52204448da968fbef62fef0861dc2a631233798aac6  x.jpg
$ curl -s -o /dev/null -w "%{http_code}\n" "${prettified_url%%\?*}"     # signature stripped
403
$ curl -sD - -o /dev/null "$prettified_url" | grep -i cache-control
cache-control: private, max-age=3600
```

**Expiry, on a real server, not "it looks like it would expire".** Second process,
`SNAPLIST_STORAGE_UPLOAD_TTL_SECONDS=1`, port 8022 (8021 was already taken on this machine):

```
upload_url = http://localhost:8022/v1/uploads/items/…/87b2f3b2-9c81-4e06-99ce-da4d19806e0e/source.jpg?exp=1793824793&sig=21be6ab575113b83d91dfa04f70d03fa7d598f4805bc503ea8b541db47129252
PUT immediately:     201
same URL, 2 s later: 403
body: {"error":{"code":"forbidden","message":"Signature is missing, expired or not valid for this object.","retry_after_seconds":null,"details":{"purpose":"upload"}}}
```

The suite proves the same thing with an injected clock instead of `sleep`, including the
inclusive boundary: `exp == now` → valid, `exp + 1` → 403.

`/readyz` on the same process:

```
{"ready": false, "missing_credentials": [...], "auth_mode": "local_dev", "unverified_identity": true,
 "repository": "SqliteRepository", "storage": "LocalStorage", "storage_configured": true,
 "migrations_applied": false}
```

## 3. Tests

`apps/api/tests/test_storage.py` — **87 tests**, all with `connect`/`create_connection`/
`getaddrinfo` trapped and time injected through `FakeClock`.

* **signature (12)**: valid · unsigned · other key · tampered `exp` · purpose confusion (both
  directions) · expired (upload and download) · non-ASCII (PUT and GET routes) · trailing
  space · non-hex unit sweep · inclusive `exp` boundary
* **keys (22 params)**: `items/../../etc/passwd`, `../escape.jpg`, `/etc/passwd`, `items//x`,
  `items/./x`, `items\x`, `\x00`, newline, empty, 200+ chars — refused by `validate_key`
  **and** shown to leave no file outside the root; plus an end-to-end traversal PUT (`%2e%2e`)
  that asserts 400 and then looks at the disk
* **body (6)**: 413 over the ceiling, 415 for `text/html`, all four accepted image types, 400
  for an empty body, 404 for a deleted item (before any byte is written)
* **bytes (10)**: write→read identical · upload stores verbatim and records the measured digest
  · `write_back` records the digest of what it stored · `expected_sha256` mismatch → no row, no
  file · mismatch on asset #2 → asset #1 not written either · `write` refuses a mismatch on its
  own · replay keeps one row · empty asset refused · **a traversal `kind` with a valid first
  asset leaves the disk empty** · **`kind="a/b"` refused** · **an over-long `kind` leaves the disk empty too** (the one input only `preflight`'s `validate_key` guards) · **rows are written only after their objects exist**
* **idempotency (1)**: 201 then 200, one row, latest bytes
* **read (3)**: 200 with the same bytes + `x-snaplist-sha256` · unsigned 403 even when the
  object exists · valid signature + missing object 404
* **AssetPack (3)**: all three URL fields fetchable, `storage_path` holds the key; a fresh item
  keeps nulls/`[]`; the no-resolver default still returns raw keys
* **claim (2)**: no `source` asset → not claimable; a non-source asset does not unblock it
* **processing (2)**: finished item → 409 and bytes/metadata unchanged; queued/running/failed
  → replay still 200
* **authorisation order (2)**: unsigned PUT at an unknown item key is 403 (and does not echo
  the id); signed PUT at an unknown item key is 404
* **Supabase (5)**: `MockTransport` asserts the signing POST (URL, `authorization: Bearer …`,
  `apikey`, body `{"expiresIn": 3600}`), upload-signing POST, `PUT` with `x-upsert: true` and
  the exact bytes, 404 → `MissingObject`, and that Supabase never claims to verify our signatures
* **backend selection (6)**: auto → filesystem · forced supabase with no key →
  `UnavailableStorage` · `from_settings` names `supabase_service_key` · `POST /v1/items` still
  202 with `upload_url: null`, `/readyz` says unconfigured, `PUT` → 503 · protocol conformance
* **plumbing (5)**: the two TTLs are 900/3600 exactly · ephemeral secret per process unless
  configured · an old SQLite file gains `byte_size`/`meta` in place · remaining-life
  `cache-control`
* **devtools (2)**: a URL is refused before the DB is opened; a real key is accepted

`test_scaffold.py::test_readyz_reports_repository_and_storage_as_separate_facts` pins the
`/readyz` split (decision 10).

## 4. Mutation testing — every number below is reproducible **with its patch**

Run in `/tmp/mut2` and `/tmp/mut3`, `rsync` copies of this tree (never the working tree). Each
mutant is applied as an exact string replacement, the storage tests are run, the file is
restored, and the restore is checked against the **working tree's** md5 (not against a backup).
Delivered digests: `storage.py 52152d47523ee1e2e2a563b1eed24a34`,
`uploads.py 02255f1ae7e915eb7d81340e5a344159`,
`writeback.py a32001e9163c42aaeda3964ddba13738`,
`repository.py beee6d1c529a607568b8fd284e459445` — all verified after every mutant.

| # | mutant (exact change) | red | which tests |
|---|---|---|---|
| M1 | `signature_message` drops `key` | 1 | `test_a_signature_for_another_key_is_403` |
| M2 | the `if int(clock()) > exp` check deleted | 3 | the two expiry tests + `test_the_expiry_boundary_is_inclusive` |
| M3a | `signature_message` drops `purpose` (sign **and** verify agree) | 2 | the two purpose-confusion tests |
| M3b | `verify_signature` signs its expectation with a fixed `"asset"` | 15 | every test that uses a *valid* upload URL — i.e. 15 tests depend on a working signature |
| M4 | `write_back` records `expected_sha256 or digest` | 2 | both `expected_sha256` mismatch tests |
| M5 | `validate_key` allows `.`/`..` segments | 5 | 4 key-validation params + the traversal PUT |
| M6 | claim predicate removed from `peek_next_queued_job` | 2 | both claim tests |
| M6b | `write_back`'s two loops swapped (rows before bytes) | 1 | `test_rows_are_written_only_after_the_bytes_exist` |
| M7 | the `is_signature_shaped` guard deleted (pre-fix A) | 3 | non-ASCII PUT, non-ASCII GET, non-hex sweep — the third fails with `TypeError` at `storage.py:450`, which is the 503 |
| M8 | `_sig_of` `.strip()` restored (pre-fix A) | 1 | `test_a_signature_with_a_trailing_space_is_403` |
| M9 | pre-fix `write_back`: no `kind` check, no key validation | 2 | both B tests (disk scan sees the orphan) |
| M10 | `_item_for_key` called before the signature check (pre-fix C1) | 1 | `test_an_unsigned_put_at_an_unknown_item_key_is_403_not_404` |
| M11 | `>` → `>=` in the expiry comparison (C2) | 1 | `test_the_expiry_boundary_is_inclusive` |
| M12 | `_require_unprocessed` call removed (pre-fix D) | 1 | `test_a_finished_item_refuses_a_new_source_photo_with_409` |
| M13 | `validate_key(key)` deleted from `preflight` (only the key-length guard goes) | 1 | `test_a_kind_that_overflows_the_key_limit_writes_nothing_at_all` — **0 red before this test existed**, which is why it exists |

**On the numbers the reviewer measured (11 for purpose, 1 for claim).** A red count is a
property of *a patch*, not of a sentence: M3a (both sides stop using purpose) gives 2, M3b
(verification stops agreeing) gives 15. QA's "11" therefore came from a different edit than my
M3a, and both are honest numbers for different mutants — which is exactly why the exact patch
is now written next to each count. For claim, this tree gives 2 (`…source_asset_exists` *and*
`…non_source_asset_does_not_unblock_the_claim`); a run that only counts the first would report
1. No mutant survived: there is no row with `red = 0`.

**M13 was the last survivor and it is now dead.** Deleting only `validate_key(key)` from
`preflight` left every test green, because the B tests attack through `kind="../../escape"`,
which the separator check catches first. The one input that reaches the key-length rule
unimpeded is a 190-character `kind`: its key is 274 characters, past the 200-character ceiling,
so asset #1 would land on disk before asset #2 was refused — the orphan bug through a second
door. `test_a_kind_that_overflows_the_key_limit_writes_nothing_at_all` closes it and turns
M13 red on its own.


## 5. Gates

```
$ make test
# pass 72        (node)
# fail 0
421 passed, 1 warning in 22.14s

$ make lint
All checks passed!                      (ruff)
packages/shared lint: Done              (tsc --noEmit)
apps/mobile lint: Done                  (tsc --noEmit)
```

Baseline was 333 pytest before E5-T4: +88 (87 storage + 1 scaffold).

## 6. Deliberate choices worth arguing about

1. **`test_worker.py::make_job` writes a `source` asset.** Decision 8 said to change the test
   if it went red, and to say so: this is that. The old fixture created a job with no bytes
   behind it and asserted the worker claimed it — it pinned the *bug* as the invariant. One
   fixture edit fixed 15 tests; no assertion was relaxed.
2. **Route `PUT`/`GET` take no bearer token.** The signature is the capability, bound to
   `purpose` + `key` + `exp`. Adding `Authorization` would make the URL insufficient on its own
   and force a token refresh mid-upload — the failure mode a signed URL exists to avoid.
3. **`/v1/uploads` and `/v1/assets` are the `LocalStorage` transport.** On Supabase they 403
   (`SupabaseStorage.verify_signature` returns `False`), because Supabase signs with its own
   key and this process must not pretend to verify it. Clients never call them in that mode.
4. **Key convention** `items/<user_id>/<item_id>/<name>`; the upload route parses it to write
   the `source` row, so the format is load-bearing. `parse_item_key` returns `None` for
   anything else rather than raising.
5. **`set_asset` is newest-wins** for `storage_path`/`byte_size`/`meta`. `apps/api/devtools.py`
   attaches an asset without measurements, so a re-attach clears `byte_size` to NULL — honest
   (unmeasured ≠ 0), but a behaviour change for that CLI.
6. **`source.<ext>` for the upload; `prettified.png`/`cutout.png`/`on_model-<n>.png` for
   outputs.** One extension rule per origin, so a bucket listing is readable.
7. **Ephemeral signing secret.** No `SNAPLIST_STORAGE_SIGNING_SECRET` → 32 random bytes per
   process, so local URLs die on `--reload`. Chosen over a hard-coded default, which would sign
   everything for everybody.
8. **A finished item refuses a new source photo (409) — the refusal names the alternative.**
   The message says "create a new item, or rerender", because "409" alone sends a client author
   looking for a retry that cannot work. Deliberately **not** "re-enqueue and wipe downstream
   assets": that is a change to the job model (E5-T2/T3), not to storage.

### Deviations from the original ten decisions: four, all small, all stated

* **Decision 6 says "控制字符"; `.` segments are refused too.** `items/./a.png` and
  `items/a.png` are the same object under two keys, which breaks the "same key twice = one
  object" property the idempotency test relies on.
* **Decision 5 says "成功 200/201"; it is 201 on first write and 200 on an overwrite** — the
  split is what makes "idempotent" observable.
* **Decision 9's 503 is also raised by the two signed routes** when no backend is configured;
  `POST /v1/items` still returns 202 + `upload_url: null` exactly as specified.
* **`exp` is inclusive** (`now <= exp` valid). Either boundary is defensible; leaving it
  unstated was the defect (C2).

## 7. What I do not stand behind

### 7.1 `CreateItemRequest.source_sha256` is accepted and then dropped

A client can declare `source_sha256 = "aaaa…"` while sending different bytes; the upload still
returns 201, and the declared value appears in no table and in no `AssetPack` field. So the
equivalence E3-T4's exact layer assumes — "the digest the client declared is the digest of the
bytes we hold" — **is still an unverified convention**. The digest that *is* trustworthy is
`assets.meta.sha256`, measured from the bytes that were written. Reconciling them (compare at
upload time, or persist `source_sha256` on the asset) is a separate ticket; doing it here would
change a request body this ticket froze.

### 7.2 The read path has no integrity check

A `GET` of a signed URL returns whatever bytes are on disk. If an object is modified out of
band, the response is still 200 and `x-snaplist-sha256` is the digest of *those* bytes — it
does not match anything in `assets.meta.sha256` and is not compared against it. Storage makes
no integrity guarantee; the header is a convenience for a client that already has a digest, not
a check. (For Supabase this is moot: the bytes never transit this process.)

### 7.3 `devtools attach-asset --path` — fixed, with a test

`--path https://…` (its own docstring's example) used to write a URL into `storage_path`, after
which `GET /v1/items/{id}` failed *while signing* (`validate_key` refuses a key with `//`), i.e.
a 503 from a dev tool that looked like it had worked. It now validates the key before opening
the database and prints what `--path` takes instead. `test_devtools_refuses_a_storage_path_that_is_not_an_object_key`
and `test_devtools_accepts_a_real_object_key` cover it.

### 7.4 The 409 only exists on the LocalStorage transport

**`_require_unprocessed` runs inside `PUT /v1/uploads/{key}`, and that route is the
`LocalStorage` transport.** In Supabase mode `POST /v1/items` hands the client a *Supabase*
signed URL and the bytes go straight from the phone to the bucket — this process never sees
that write, so the 409 is never reached and cannot stop anything. On that backend the guarantee
has to come from a **bucket policy** (make the object immutable once the item is processed, or
deny overwrite for an existing key) or from routing uploads back through the API instead of
signing them. Until one of those exists, "a processed item refuses a new photo" is a statement
about the local backend only; it is not a global invariant, and the D-tests in
`test_storage.py` exercise the local path.

### 7.5 The rest, unchanged

1. **`SupabaseStorage` is unexercised against the real service.** No credentials here; the only
   evidence is `MockTransport` asserting the request shape I *believe* Supabase wants. If the
   endpoint moved, this fails at the first call and nothing else does.
2. **No connection pooling when no client is injected** — `_send()` opens and closes an
   `httpx.Client` per call. Correct, slow, wrong for production; inject one client at
   `create_app` time.
3. **The upload route reads the whole body into memory.** The ceiling is 20 MB and
   `content-length` is checked first, so it is bounded — but a bound, not streaming.
4. **Orphan objects remain possible in one narrow case**: the item is checked before the write,
   so a delete *during* the write leaves an object with no row. Nothing cleans it up; the
   bucket is private and the object is addressable only by a signature that expires.
5. **`assets.meta` is text-shaped JSON in SQLite and `jsonb` in Postgres.** No test compares
   the two backends.
6. **The `sha256` on a `source` row is the digest of the *latest* upload** while the item is
   unprocessed. After `done` the row no longer changes (D), so the window is closed — but a
   re-upload before the job is claimed still means the row describes bytes the job may not
   read. E3-T4 owns the decision about where the digest belongs.
7. **`LocalStorage._path`'s `is_relative_to(root)` check is defence in depth, not an
   independently testable constraint.** Removing it alone turns **zero** tests red (the key
   rules already make it unreachable); only a mutation that removes the key rules *and* this
   check shows anything (7 red). The comment in the code still calls it "a second lock" —
   accurate, but it must not be read as "there is a test for this line". The test suite covers
   the first lock; the second is a belt that has never been asked to hold anything up.
8. **Stale prose I was not allowed to edit** — `apps/api/jobs/worker.py` still ends its
   `PipelineUnavailable` message with "signed upload is still 501 (E5-T4)"; `apps/api/web.py`
   still says a multipart upload "is E5-T4"; and `apps/api/requests.py:20-26` still claims
   `source_sha256` "is the dedupe key E3-T4 will use; accepting it now means **the column** and
   the client contract do not change" — there is no column, and §7.1 above says so. The worker
   string reaches `jobs.error_detail` on the wire; the other two mislead the next reader.
9. **`/readyz` still reports `migrations_applied: false`** unconditionally.


# E5-T6 — `events` table + `POST /v1/events`

Run date: 2026-11-05. Every command below was executed in this working tree, on this machine.
Suite: **540 pytest (480 → 540, i.e. 60 new) + 72 node**, `make lint` clean.
No commit (the lead收口). **One ADR was needed after all**: implementing the route exposed that the
frozen envelope had no `event_id`, so a client validating with `analytics.ts` could not build a
body the route accepts. ADR-007 adds the field, `EVENTS_VERSION` goes 1.2.0 → **1.3.0**,
`docs/CONTRACTS.md` §7 changes four lines (version sentence + history row), and
`CONTRACT_VERSION` does not move.

## 1. What changed

| file | change |
|---|---|
| `infra/migrations/0002_events.sql` | **new.** `public.events`: client-uuid PK, seven-name CHECK, one narrow nullable column per payload field with its own CHECK, forward CHECKs (required per event) **and reverse CHECKs (this column belongs to that event only)**, `payload_sha256`, two indexes, RLS on with a single `service_role` policy. Plus `events_field_names_are_name_list(text[])`, a 3-line immutable SQL function, because a CHECK may not contain the `unnest` the per-element regex needs. |
| `infra/migrations/README.md` | new section describing 0002: why it is not an extension of `usage_events`, what each pin is for, and that "applies on Postgres" is UNVERIFIED here. |
| `apps/api/routers/events.py` | **new.** 7 pydantic models in a discriminated union (`extra="forbid"`), `TypeAdapter`-based manual body parse (so 413 precedes 422), `subject`↔caller binding (403), canonical payload digest, idempotency (202 duplicate / 409 reuse). No `GET`. |
| `apps/api/repository.py` | `EventRecord` (frozen dataclass), protocol + `SqliteRepository.append_analytics_event()` / `get_analytics_event()`, and a SQLite mirror of `events` carrying the same forward and reverse CHECKs. |
| `apps/api/main.py` | router registered; module docstring's route list mentions the new endpoint. |
| `apps/api/tests/test_events.py` | **new**, 60 tests, several of which read *other* files as text: `infra/migrations/0002_events.sql` and `packages/shared/src/analytics.ts`. |

Deliberately **not** touched: `docs/PLAN.md`, `docs/CONTRACTS.md`, `apps/api/schemas.py`,
`packages/**`, `evals/**`. In particular `apps/api/schemas.py` did not gain a model — see §6.1.

## 2. Acceptance — real uvicorn, real curl

Server: `SNAPLIST_DB=$(mktemp -d)/dev.db .venv/bin/python -m uvicorn apps.api.main:app --port 8011 --log-level warning`
(`U = 11111111-1111-4111-8111-111111111111`, `EID = f889bed7-4422-4a3c-a2a8-b482ffd751c3`)

```
$ curl -s -X POST localhost:8011/v1/events -H "x-snaplist-user-id: $U" -H 'content-type: application/json' -d "$BODY" -w ' [%{http_code}]\n'
{"accepted":true,"duplicate":false} [202]

$ curl -s ... -d "$BODY"        # byte-identical replay, same event_id
{"accepted":true,"duplicate":true} [202]

$ curl -s ... -d "${BODY/\"row_count\":3/\"row_count\":99}"    # same id, different content
{"error":{"code":"invalid_request","message":"This `event_id` was already ingested with a different payload. A reused id is a client bug: event ids must be generated once per event and reused only for retries of that same event.","retry_after_seconds":null,"details":{"event_id":"f889bed7-4422-4a3c-a2a8-b482ffd751c3"}}} [409]

$ curl -s ... -d '{"event":"item_created"}'
{"error":{"code":"invalid_request","message":"Request body is not one of the seven frozen analytics events. See details for the fields.","retry_after_seconds":null,"details":{"body":"Input tag 'item_created' found using 'event' does not match any of the expected tags: 'upload_started', 'prettify_approved', 'attributes_edited', 'export_clicked', 'subscription_started', 'paywall_imp"}}} [422]
```

Three more, on the same server, because the brief asked for the ones the four above do not cover:

```
$ curl -s -o /dev/null -w '%{http_code}\n' localhost:8011/v1/events -H "x-snaplist-user-id: $U"      # GET
405

$ curl ... --data-binary @big.json     # 9 KB body
{"error":{"code":"invalid_request","message":"Analytics event body must be at most 8192 bytes; 9039 was too large.","retry_after_seconds":null,"details":{"limit_bytes":"8192","observed_bytes":"9039"}}} [413]

$ curl ... -d '{... "email":"a@b.c"}'  # otherwise-valid upload_started + one extra key
{"error":{"code":"invalid_request","message":"Request body is not one of the seven frozen analytics events. See details for the fields.","retry_after_seconds":null,"details":{"upload_started.email":"Extra inputs are not permitted"}}} [422]
```

The row on disk (same server, `sqlite3` CLI against the file backend — no repository code involved,
which is why the replay assertion in §3 uses a second connection too):

```
$ sqlite3 $DB 'select count(*) from events;
               select event, subject, marketplace, row_count, quote(plan), payload_sha256 from events;'
1
export_clicked|usr_11111111-1111-4111-8111-111111111111|ebay|3|NULL|818c725aec5bf20e61ccda9a115756121f0f3f7c5867742f348ea33bf60e2060
```

One row after four POSTs: the 202 duplicate did not add a second, and neither the 409 nor the
422 (`item_created`) was written anywhere. `plan` is `NULL` on an `export_clicked` — that is the
reverse constraint doing its job.

## 3. Tests

`apps/api/tests/test_events.py`, 60 cases (9 + 27 + 5 + 4 + 7 + 5 + 3). Grouped by what they
defend, not by endpoint:

- **Happy paths (9).** 7 — one per frozen event, each asserting `{accepted: true, duplicate:
  false}`; the union's membership (7 members, names equal to the builder keys); and one that
  reads back a stored `export_clicked` row to prove only its own payload columns were written
  (`plan`, `currency`, `bytes`, `field_names`, `decision` all `None`).
- **Vocabulary and shape (27).** Unknown event name → 422; unknown envelope key (`email`) → 422;
  unknown payload key (`photo_url`) → 422; `field_names: ["color:red"]` → 422; `["Size Label"]`
  → 422; 21 names → 422; `field_names` omitted → 202 with `()`; **7** cases of a required
  payload field missing → 422; **5** cases of a payload field on the *wrong* event → 422;
  **5** negative/zero counters → 422; unknown enum literal → 422; `client_ts` with no offset →
  422; `currency: "usd"` → **202** (see §6.2).
- **Identity (5).** `anon_` and `usr_` + own uuid → 202 (2 cases); another account's uuid → 403
  with `details.reason = subject_not_bound_to_caller`; `subject: "seller@example.com"` → 422; no
  identity → 401.
- **Idempotency (4).** Replay → 202 duplicate **and exactly one row, read by a second `sqlite3`
  connection**; same id + changed body → 409 and the first payload still stands; replay respelled
  as `+02:00` → duplicate (the digest canonicalises `client_ts`); a fresh id → a second row
  (per-id, not per-content).
- **Transport (7).** >8 KB → 413; **exactly** 8 KB is not refused for size (the boundary is `>`,
  pinned); `b"not json"`/`b"[1,2,3]"`/`b""` → 422 (3 cases); `GET` → 405; every refusal body is
  `{"error": {...}}` with no FastAPI `detail`.
- **Migration as text (5).** The CHECK pins exactly the seven names; RLS is enabled, there is
  exactly one policy, it names `service_role` and neither `anon` nor `authenticated`;
  `payload_sha256` is `not null` + hex-CHECKed; the `field_names` regex is byte-identical in
  `analytics.ts`, python and the SQL function; and the **column whitelist**:
  `set(parsed_columns) == EXPECTED_COLUMNS` (30 names, written out in full), no
  `jsonb`/`json`/`hstore`/`bytea`, **and every `text`/`text[]` column is mentioned in a CHECK** —
  that second clause is what keeps working after this ticket, because a future `note text` fails
  it even if somebody also whitelisted the name.
- **Frozen-vocabulary parity (3).** `analytics.ts` is parsed with anchored regexes into
  `(names, per-event payload fields, envelope fields)`; the union's members are derived from
  their own `Literal` tags; the two are compared field by field. A parser self-check
  (`len(names) == 7`, envelope == 6 fields) stops the comparison from silently going vacuous if
  the TS file is refactored. Last, `BUILDERS` is validated against the models, so a typo in a
  test body cannot masquerade as a router bug.

```
$ make test
1..72 / # pass 72 / # fail 0            (node, unchanged)
540 passed, 4 skipped, 1 xfailed, 1 warning in 52.00s

$ make lint
/Users/bujin/Documents/Projects/SnapList/.venv/bin/python -m ruff check .
All checks passed!
pnpm -w lint → packages/shared lint: Done, apps/mobile lint: Done
```

## 4. The seven events, and exactly what the database pins for each

Every payload column is *forward*-constrained (required on its own event) **and**
*reverse*-constrained (absent everywhere else). The reverse half is the part that makes "the
payload cannot be smuggled onto another event" a database fact rather than a code convention.

| event | required by CHECK | enum / domain CHECKs | reverse constraint |
|---|---|---|---|
| `upload_started` | `source`, `capture_mode` | `source in (camera, library)`; `capture_mode in (single, batch)`; `batch_index >= 0` | `source`, `capture_mode`, `batch_index` NULL on every other event |
| `prettify_approved` | `decision`, `job_latency_ms`, `prettify_latency_ms`, `attrs_latency_ms` | `decision in (approve, retry, remove_bg_waitlist)`; the three latencies `>= 0` | all four NULL elsewhere |
| `attributes_edited` | `edited_field_count`, `fields_touched` | both `>= 0` (no `> 0`: the `0` bucket is the blind-accept metric); `field_names` ≤ 20, no NULL elements, each `^[a-z][a-z0-9_]*$` | all three NULL elsewhere |
| `export_clicked` | `marketplace`, `row_count`, `export_latency_ms` | `marketplace in (ebay, poshmark, vinted)`; `row_count >= 1`; latency `>= 0` | `export_latency_ms` only here; `marketplace`/`row_count` only on the two export events |
| `subscription_started` | `plan`, `paywall_variant`, `amount_cents`, `currency` | `plan in (starter, pro)`; `paywall_variant in (a, b, c)`; `amount_cents >= 0`; `length(currency) = 3` | `plan`, `amount_cents`, `currency` only here |
| `paywall_impression` | `paywall_variant`, `paywall_trigger`, `plan_offered` | `trigger in (upload_4th, on_model, export_limit)`; `plan_offered in (starter, pro)` | `paywall_trigger`, `plan_offered` only here |
| `export_delivered` | `marketplace`, `row_count`, `bytes`, `delivery_latency_ms` | `bytes >= 0`; latency `>= 0`; `row_count >= 1` | `bytes`, `delivery_latency_ms` only here |

`paywall_variant` is the single payload column two events share (`subscription_started` and
`paywall_impression`), on purpose: an impression and the subscription it converts into must be
joinable on the variant, and splitting the column would delete that join. The brief listed it as
"subscription_started only"; that would have made every `paywall_impression` insert fail. This is
the one place where the brief and `analytics.ts` conflicted and I followed `analytics.ts`.

## 5. What I do not stand behind

1. **`analytics.ts`'s `Envelope` has no `event_id`, and this route requires one.** The frozen
   zod `Envelope` is `{event, client_ts, session_id, subject, app_version, platform}` with
   `.strict()`, so `AnalyticsEvent.parse(body)` on the client **rejects** the very body
   `POST /v1/events` demands. E6-T1 must either send the id outside the validated object, or
   `analytics.ts` gains `event_id` in an `EVENTS_VERSION` bump (an ADR, and not mine to write).
   The API-side consequence today: the field is required, so the client has no legal way to omit
   it — but a client that validates with the shared schema first will fail before it gets here.
   `test_python_models_mirror_the_frozen_typescript_vocabulary` names this asymmetry out loud
   (`API_ADDITIVE_FIELDS`) instead of filtering it away silently.
2. **No rate limit, no per-subject quota, no auth on volume.** One authenticated seller can POST
   events as fast as the process answers; the only bound is the 8 KB ceiling. A `subject` bound
   to an account does not stop that account from writing a million rows. Needs a token bucket
   keyed on `subject` (or a gateway limit) before the funnel is load-bearing.
   *Superseded 2026-11-11 UTC:* `POST /v1/events` now has an in-process token bucket keyed on
   `caller.user_id` (not `subject`), burst 120, refill 1 token / 12 s, returning 429 with
   `retry_after_seconds` / `Retry-After` and `ErrorCode.INVALID_REQUEST`; it is created per
   `create_app()` instance on `app.state`. See `docs/QA.md` §10 R8. The original concern
   (coordination across workers/instances, and the `subject`-vs-`user_id` keying choice) is
   captured there as acknowledged limitations.
3. **`client_ts` is whatever the phone says.** Stored, shape-validated, normalised to UTC — and
   still forgeable. Every report must order and window on `received_at`; a dashboard that uses
   `client_ts` for "events in the last 7 days" is trivially gamed and also wrong for a device
   whose clock is off. There is no server-side correction and no skew metric yet.
4. **The Supabase-hosted RLS claim is unproven.** `0001` → `0002` → `0003` were applied clean
   (twice for `0003`, `-v ON_ERROR_STOP=1`) to a local vanilla **PostgreSQL 14.18** behind
   `infra/migrations/verify/local_supabase_shim.sql`, and the CHECKs, the per-item index and
   the hard-delete behaviour were measured on that database. That is not a Supabase project:
   the hosted environment, PG15, the RLS role policies (`anon` / `authenticated` /
   `service_role` grants) and PostgREST remain **UNVERIFIED** — `pg_policies` would show the
   policy exists, which is not the same claim. Two things I would check first on a real
   instance: that `service_role` (which is `bypassrls` in the shim) is the role the API
   connects as, and that no default `GRANT` on `public` gives `anon` table privileges that
   RLS then has to deny.
5. **`events_field_names_are_name_list()` is untested SQL.** It is the only place the
   per-element regex is enforced *in the database*, and it exists only because a CHECK cannot
   contain a subquery. If `unnest`/`~` inside an `immutable` SQL function does something I did
   not expect, `field_names` validation falls back to pydantic + the SQLite mirror — i.e. the
   Postgres table would be weaker than advertised and no test here would notice.
6. **`currency` is a 3-character text column.** `analytics.ts` says `z.string().length(3)`, so
   `usd`, `1 2` and `abc` are all legal payloads and all storable. A `^[A-Z]{3}$` check would be
   *stricter than the frozen shared schema*, which would mean the API refusing something the
   client was told was valid — so I did not add one. It is the only text column here that can
   carry three arbitrary characters; that is small, not zero.
7. **`edited_field_count <= fields_touched` is not enforced.** `usage_events` enforces exactly
   this pair (the ledger's version of the same two numbers), and this table does not, because
   zod does not and a stricter database would 500 on a payload the client validated. A report
   that divides one by the other can therefore produce >100%.
8. **Payload digest canonicalisation is my definition, not the contract's.** Sorted keys, no
   whitespace, `client_ts` normalised to UTC microseconds. That normalisation is why a retry
   which re-spells the timestamp (`Z` vs `.000Z`) still returns 202 — measured after QA pointed
   out the earlier claim here was wrong. The false positive is elsewhere: `field_names` order is
   semantically meaningless and the digest is order-sensitive, so `["colors","brand"]` retried as
   `["brand","colors"]` under the same id returns **409**. Loud-once is still the choice over
   silently picking a row, and the client fix is the same: keep the bytes you retry.
9. **Read side: nothing exists.** No `GET /v1/events`, no report query, no `export_delivered`
   ↔ `export_clicked` gap query — which means the export-failure rate that
   `docs/CONTRACTS.md` §7 calls a *required observable* is still only derivable by hand. E0-T5
   owns the reporting, and it now has a table to read.
10. **No `events` cleanup/retention policy.** Append-only, forever, no partitioning, no TTL. At
   v1 volume that is correct; at a hundred users it is a table nobody has planned for.
11. **The 413 is a body-size limit, not a schema-size limit.** A payload that is small in bytes
   but pathological in `field_names` count is caught by the 422 (`max_length=20`); there is no
   limit on the *number of events* per request because there is exactly one event per request.
12. **`test_a_body_over_eight_kilobytes_is_413` uses a `content-length` header, so the
   chunked-transfer path is untested.** The route re-checks `len(raw)` after reading, which is
   the honest second lock, but no test exercises it — `TestClient` always sets a length.

## 6. Two decisions worth arguing about

### 6.1 `EventAccepted` lives in the router, not in `schemas.py`

`apps/api/schemas.py` is the frozen ten-model wire contract; every model there is mirrored in
`packages/shared/src/schemas.ts` and checked by `test_contract_parity.py`. Adding a response
model there would force a zod mirror, a `CONTRACT_VERSION` bump and an ADR, to describe a body
only this one route returns to a client that already knows what it sent. `apps/api/responses.py`
set the precedent for the export batch body. The consequence to know about: the two booleans in
the 202 body are **not** part of the frozen contract and are not parity-checked; they are
documented here and in the route docstring instead.

### 6.2 `currency: "usd"` is accepted on purpose

See §5.6. The test `test_currency_is_three_characters_not_an_iso4217_pattern` pins the *weaker*
rule so that a future reviewer cannot "tighten" it into a contract violation without going red.

## 7. md5 of every file this ticket delivered

Recomputed **after** the audit fixes in §8 — the first version of this table was already stale
when it was written (the test file had been edited once more), which is the same stale-digest
failure this project keeps finding.

```
7b4491b8a7c5aa5bb556c77b312c89c3  infra/migrations/0002_events.sql
e54bc3c12284984b9ec94dd3fc082be1  infra/migrations/README.md
741d1a6fade609f7ac1dd223ce531069  apps/api/routers/events.py
3a88134edb281a5d585943776f538584  apps/api/repository.py
014fb7a0f1a7ab6d38232deaf725186a  apps/api/main.py
c33b89e1c71202178c5ab6950b26f04a  apps/api/tests/test_events.py
```

`docs/HANDOFF-engineer.md` itself is absent from that list on purpose: a file cannot contain its
own md5. The digest of this file after this section was written is recorded in the delivery
message, not here — `md5 -q docs/HANDOFF-engineer.md` reproduces it.

## 8. Audit round: three blocking findings, and what changed

An independent review of this ticket found three things worth more than the 60 tests I shipped.
All are fixed; this section exists so the next reader does not have to re-derive them.

**8.1 Five client errors were reported as `503 internal`.** `amount_cents = 10**30`,
`bytes = 2**63`, `job_latency_ms = 10**20`, `row_count = 10**19` and a `currency` of three NUL
bytes each raised `OverflowError` or `IntegrityError` from the driver, which `errors.py` maps to
"the server is broken". Every numeric payload field is now bounded by `INT4_MAX` (the Postgres
column width) and every string field excludes control characters, so the first refusal is a 422
with a field name. The database remains the second lock; a second lock that reports a 5xx is a
lock that pages someone.

**8.2 Two DoD numbers had no coverage at all.** The 8 KB ceiling and the 20-name cap were both
tested *against the constant that defines them* — the payload was built as
`"x" * (MAX_EVENT_BYTES + 512)`. Raising the constant to 64 KiB, and then to 1 GiB, left the
file green. Both tests now build their payload from a literal and assert the constant equals it,
so a change to either breaks the build instead of agreeing with itself.

**8.3 The SQLite mirror was looser than the migration it mirrors.** `subject`, `session_id`,
`app_version` and `payload_sha256` had shape CHECKs in Postgres and none in the table this build
actually writes to; a direct `append_analytics_event()` call accepted `subject =
"seller@example.com"` and `field_names = ["color:red"]`. Three of those four are expressible in
SQLite and are now enforced there; the one that is not (`field_names`' per-element regex, which
needs a subquery) is capped at 2000 bytes, with the router's pydantic pattern as the real gate.

Two smaller ones, same family: `field_names` elements had an alphabet but no length, so
`["a"*4000]` was a 7 KB lowercase text channel — now 64 characters, in the router, the SQLite
mirror and the Postgres function. And the reverse CHECKs (the "this column belongs to that event
only" constraints this ticket calls its core mechanism) are covered by **text** assertions only:
deleting one leaves the suite green. That is recorded, not solved — a SQL-level constraint test
needs a Postgres to run against.

**What the audit could not change:** the table has no `item_id`. `docs/METRICS.md` assumes
`export_delivered` carries one, and PLAN's per-item tool-time and activation-dedup gates need it.
That is a contract gap one level above this ticket, and E0-T5 inherits it. → **closed by E5-T6b
(ADR-008). See §9.**

## 9. E5-T6b — `item_id` on the envelope, graded per event

The gap §8 closed with was real and is now shut: `public.events` has no join key on the item, so
`tool_time_ms` per item, `activation_items_exported_7d` (deduped to one row per item) and the
`export_clicked` − `export_delivered` failure rate — a *required observable*, per
`docs/CONTRACTS.md` §7 — were three numbers nobody could compute. E0-T5 would have printed them
as counts of events: plausible, comparable to nothing, and wrong in the direction that looks
fine.

**9.1 The shape.** `item_id` is on the envelope and **graded per event** — required on
`upload_started`, `prettify_approved`, `attributes_edited`, `export_clicked`, `export_delivered`;
optional on `paywall_impression`; forbidden on `subscription_started`. Not "optional everywhere":
an optional field on a frozen envelope is how a metric dies quietly, because the emitter that
forgets still gets a 202 and the only symptom is a percentage drifting. The reasoning for each
grade, including why the paywall exception is factual rather than convenient, is in
`docs/ADR-008-events-item-id.md` — do not re-litigate it in a PR description.

**9.2 Where the grade is enforced.** Four places, and the count is the point:
zod per-event schemas (`analytics.ts`), pydantic per-event models (`routers/events.py`), two
CHECK constraints in `infra/migrations/0003_events_item_id.sql`, two table CHECKs in the SQLite
mirror (`repository.py`). The schemas catch the client; the database catches anything that
bypasses the route. `test_events.py::test_the_sqlite_mirror_refuses_a_subscription_with_an_item`
calls `append_analytics_event()` **directly**, with a *real* item id (a random one would raise on
the foreign key and prove nothing), which is the only version of that test that means something.

**9.3 Ownership, and the 404.** `bind_item()` looks the id up with
`get_item(..., user_id=caller.user_id, include_deleted=True)` and answers **404** for anything
else — 404 and not 403 because a 403 confirms that somebody else's item exists, the same call
`routers/items.py` makes. `include_deleted=True` is deliberate: soft-deleted items still own
their events, or a client retrying an offline-queued event after the seller tidied up would be
refused for a step that was legal when it happened. The check is *point in time* — "yours now",
not "yours then" — which is a limitation, not an oversight; see 9.6.

**9.4 `docs/METRICS.md` was corrected, not rewritten.** Four field names had drifted from the
frozen contract, and one of them (`stripe_customer_id`) is a **PII violation**, not just a
mismatch:

| was | is | why |
|---|---|---|
| `price_cents` | `amount_cents` | the contract's name; the row had a field that does not exist |
| `stripe_customer_id (hashed)` | not in the event at all | §7 bans PII; a hashed customer id is still a customer id. Read it from Stripe, keyed by subscription |
| `paywall_type (soft\|hard)` | `trigger` + `plan_offered` | `paywall_type` was never a wire field |
| `free_items_exported` | computed from `export_delivered` per `item_id` | the confounder is derivable, and a derived figure cannot disagree with the funnel it explains |

The table also gained an `item_id` column stating the grade per row, and the header now says the
wire names come from `CONTRACTS.md` §7 with the contract winning any conflict. That last line
existed as prose before and the table drifted anyway; it is now a standing instruction with the
three examples inline, because "check the contract" is advice nobody follows retrospectively.

**9.5 What the tests pin, in the two places that can actually fail.** Python: five events with a
legal item → 202; without → 422 naming `item_id`; with **another seller's** item → 404 *and no
row written*; `subscription_started` + item → 422; `paywall_impression` both ways → 202; the
round-trip through `get_analytics_event`; the 0003 text-level facts (column spelling, both
CHECKs by name, index column list, and **the absence of any ON DELETE action**); and a
python↔TS *grade* parity test that compares the requirement event by event — field-name parity
cannot see the difference between "graded" and "optional everywhere", which is the whole
subject of the ticket. Since the Corrigendum (§9.7) the decisive evidence is **behavioural,
not textual**: a real `delete from items` followed by whole-`EventRecord` equality across all
seven events, `pragma foreign_key_list(events)` introspection, and a direct
`update events set item_id = null` that must raise.
Node: `EVENTS_VERSION === "1.4.0"` as a literal, the envelope at eight fields, and the three
grades asserted against the schemas.

**9.6 Still not standing — read this before believing the report.**

1. **Only a local vanilla PostgreSQL 14.18 has run these migrations.** `0001` → `0002` → `0003`
   were applied clean (twice for `0003`, `-v ON_ERROR_STOP=1`) behind
   `infra/migrations/verify/local_supabase_shim.sql`; the CHECKs, the per-item index and the
   hard-delete behaviour were measured there, and `apps/api/tests/test_events.py` still asserts
   the text (both CHECKs, the index, the absence of an ON DELETE action) and exercises the
   SQLite mirror, which is the copy that runs. The Supabase-hosted environment, PG15, the RLS
   role policies and PostgREST remain **UNVERIFIED**, exactly as for 0001/0002. The file was
   corrected **before** it was ever applied to a Supabase project (§9.7), so no environment
   has the withdrawn FK and no data repair is pending.
2. **An upgraded SQLite *file* database is looser than a fresh one.** `alter table` can add a
   column but not a CHECK, so a `SNAPLIST_DB` file created before 0003 gets a plain
   `item_id text` with **no FK** but also without the uuid shape CHECK and the two grade CHECKs —
   it enforces the grade only in the router (`SqliteRepository._migrate_event_item_column`).
   Fresh databases enforce it twice. A file built by the *pre-fix* version that already carries
   the bad `ON DELETE SET NULL` FK is **refused with a `RuntimeError`** telling the operator to
   delete the dev DB; SQLite cannot drop a constraint with `ALTER TABLE`, and keeping it quietly
   would contradict the migration that ships. Dev-only path, written down rather than smoothed
   over. Note also that `pragma foreign_keys = on` is **per-connection and defaults OFF** in
   SQLite, so any external sqlite3 connection to a dev file has no FK enforcement at all.
3. **Ownership is a point-in-time check.** Events filed against an item that has since been
   *hard*-deleted 404 (soft deletes are fine — `include_deleted=True`), and an event filed while
   the item was yours stays yours. There is no audit of "who owned this item at `client_ts`",
   because the schema does not record item ownership history. The direction of residual risk is
   favourable: an attacker can only attach events to items they own. **Existing** event rows are
   unaffected by a hard delete and keep their original `item_id` (§9.7).
4. **There is no static referential integrity on `item_id`, and that is the design.** Removing
   the FK (§9.7) means a dangling uuid at rest is legal: the event outlives its item. Reports,
   including E0-T5's `tool_time_ms` and activation SQL, **must tolerate orphans** — filtering
   them out would silently shrink historical rates, exactly what the FK was supposed to
   prevent. Any writer that bypasses `bind_item` must validate the id itself; the shape CHECK
   proves it is a uuid, not that the item exists or belongs to the subject. If a data-subject
   erasure must remove the evidence too, it is an explicit, auditable
   `delete from events where item_id = ?` — not a silent constraint side effect.
5. **The per-item reports do not exist yet.** This ticket makes them *computable*; E0-T5 has to
   write the SQL. Everything `docs/METRICS.md` says about `tool_time_ms` and activation is still
   **UNMEASURED** — `make evals-strict` still fails on purpose, and nothing here changes that.
6. **Nothing validates that `item_id` is real at *emit* time on the client.** The client can
   build a valid body from a stale item id; the API answers 404 and the client must drop the
   event rather than retry it forever. That is a mobile-side outbox rule (E6-T1), not something
   this route can enforce.
7. **`paywall_impression`'s optionality is untested against a real client.** Both branches are
   covered by the suite; whether the settings-screen paywall actually exists yet is a product
   question, so the "no item" path is a schema that allows something nobody emits.

## 9.7 Corrigendum (2026-11-05) — the FK that could not work

An independent QA audit found a blocking defect in the version of E5-T6b described in §9.
`0003_events_item_id.sql` declared `item_id uuid references public.items (id) on delete set
null`, and the SQLite mirror copied it. The same file declares the forward CHECK that makes
`item_id` **required** on five events. `ON DELETE SET NULL` is an implicit `UPDATE`, and a CHECK
is re-evaluated on UPDATE, so the two constraints are mutually exclusive. Measured against a
real `SqliteRepository(":memory:")` with `pragma foreign_keys = on`:

| event | with the old FK | after the fix (no FK) |
|---|---|---|
| the five item events | `delete from items` **refused** by CHECK violation; row unchanged, FK silently degraded to `NO ACTION` | delete succeeds; row unchanged, `item_id` intact |
| `paywall_impression` | delete succeeds, event row **silently rewritten to `item_id = NULL`** | delete succeeds; row unchanged, `item_id` intact |
| `subscription_started` | delete succeeds, `item_id` stays NULL | delete succeeds, `item_id` stays NULL |

The FK therefore did the wrong thing on **both** halves, and §9's "an orphaned timestamp is
still a datapoint" was unachievable on the five events that matter. Every existing test was
green because `test_migration_0003_adds_item_id_with_a_set_null_foreign_key` asserted only the
SQL **text** — the same failure shape §8.2 documents for constants that were tested against
themselves, one level up.

**Fix (ADR-008 Corrigendum):** remove the FK outright. `item_id` is a bare `uuid` column in
Postgres (the type is the shape constraint) and `text` with a length + GLOB CHECK in SQLite;
existence and ownership remain in `bind_item`. `0003` was corrected **in place** — it was
untracked, never committed, and the pre-fix version was never applied to any database, so no
0004 and no data repair. The corrected file has since been applied clean twice to a local
vanilla PostgreSQL 14.18 behind the shim (§9.6 item 1).
`CASCADE`, `RESTRICT`/`NO ACTION` and "relax the forward CHECK" were all evaluated and rejected
(ADR-008 Alternatives rejected). No wire shape changed: `EVENTS_VERSION` stays 1.4.0,
`CONTRACT_VERSION` does not move (unchanged by this ADR), `docs/CONTRACTS.md` §7 and
`analytics.ts` untouched.

**What now proves it:** `test_hard_deleting_an_item_never_rewrites_an_event_row` (all seven
events, whole frozen-dataclass equality before/after a real hard delete),
`test_foreign_keys_are_enabled_and_events_item_id_has_no_delete_action` (real
`pragma foreign_key_list(events)` introspection + an `assets` cascade positive control),
`test_an_item_event_cannot_be_nulled_after_the_fact`, the non-uuid shape CHECK test, and the
pre-fix / pre-0003 file-database tests. Three mutations were run against a `/tmp` copy to
prove these tests actually go red: re-adding the bad FK, deleting `pragma foreign_keys = on`,
and relaxing the forward CHECK. Residuals from the fix are folded into §9.6 items 2 and 4.
