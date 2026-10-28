# HANDOFF — lead

Role: **lead (architect)** · Run date: 2026-10-28 · Scope: planning + scaffolding only. No
product feature was implemented (no segment/prettify/vton algorithm, no endpoint logic).

---

## 1. What shipped

| deliverable | path | state |
|---|---|---|
| Plan: 10 epics / **46 tickets** | `docs/PLAN.md` | done, every ticket has id · owner · allowed paths · acceptance command · DoD (verified: 0 tickets without an acceptance block) |
| Measurement contract (proxy metrics) | `docs/PLAN.md` §"Measurement contract" | tool time p50 ≤ 35 s / p90 ≤ 60 s, fields-touched ≤ 5, no stopwatch anywhere |
| Contracts (pydantic ⇄ zod) | `docs/CONTRACTS.md` | wire contract **v1.0.0**; events vocabulary **1.1.0**; reason_code UI copy §6 |
| Analytics vocabulary | `packages/shared/src/analytics.ts` | 6 frozen events, zod union, `.strict()`, 11 tests |
| pydantic source of truth | `apps/api/schemas.py` | real, validated by tests |
| zod mirror | `packages/shared/src/schemas.ts` | real, parity-tested against python through `node` |
| ADRs | `docs/ADR-001-stack.md` … `ADR-005-v1-scope-and-billing.md` | stack, analytics, anonymous session, async on-model, v1 cuts |
| FastAPI app | `apps/api/main.py`, `apps/api/routers/items.py` | `/health` 200 real; `/v1/*` 501 stubs with the contract error envelope |
| Vision scaffolds | `packages/vision/{segment,matte,rectify,prettify,attrs,vton,dedupe,types,cli}.py` | docstrings + signatures, all raise `StageNotImplemented` |
| Expo app | `apps/mobile/` | one screen rendering "SnapList"; `tsc --noEmit` clean |
| Marketing stub | `apps/web/index.html` | static placeholder, growth role replaces it in E7-T5 |
| Evals harness | `evals/run.py` | runs, prints `UNMEASURED` for all 12 metrics, writes `evals/reports/vision.md` |
| Infra stubs | `infra/modal_app.py`, `infra/migrations/README.md` | explicit 501/NotImplemented; the README now carries the decided schema constraints |
| Makefile | `Makefile` | `install test lint evals dev` (+ `dev-web dev-mobile fmt contract clean`) |
| Dependencies | `docs/DEPS.md` | 17 rows actually installed (versions read from the lockfiles, not guessed) |
| Runbook | `docs/RUNBOOK.md` | per-credential: where to get it, how to verify, which epic it blocks, local workaround |
| Extras | `docs/class-diagram.mermaid`, `docs/sequence-diagram.mermaid`, `.env.example`, `.gitignore` | cheap to delete if unwanted |


## 2. Acceptance — real output

Run from `/Users/bujin/Documents/Projects/SnapList`.

### `make install` → exit 0

```
$ make install
uv sync
Resolved 31 packages in 0.66ms
Audited 29 packages in 0.01ms
/Users/bujin/.workbuddy/binaries/node/versions/22.22.2-6/bin/pnpm install
Scope: all 4 workspace projects
Already up to date
Done in 233ms using pnpm v11.22.0
```

(The very first `pnpm install` was a cold 4m 09s with 986 packages resolved — Expo is the
weight. Subsequent runs are cached.)

### `make test` → exit 0

```
$ make test
/Users/bujin/.workbuddy/binaries/node/versions/22.22.2-6/bin/pnpm -w test
$ pnpm -r --if-present test
Scope: 3 of 4 workspace projects
packages/shared test$ pnpm build && node --test dist/test/*.test.js
packages/shared test: $ tsc -p tsconfig.json
packages/shared test: TAP version 13
packages/shared test: # Subtest: contract version matches the python side
packages/shared test: ok 1 - contract version matches the python side
...
packages/shared test: # tests 11
packages/shared test: # pass 11
packages/shared test: # fail 0
packages/shared test: Done
/Users/bujin/Documents/Projects/SnapList/.venv/bin/python -m pytest
................................                                         [100%]
=============================== warnings summary ===============================
.venv/lib/python3.11/site-packages/starlette/testclient.py:37
  ... DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated ...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
32 passed, 1 warning in 0.65s
```

11 node tests + 32 pytest tests, **0 failures, 0 skips** (re-run after round-2 changes:
**22 node tests** + 32 pytest, still 0 failures — see §10). The 4 zod-parity tests skip only when
`packages/shared/dist` is absent; `make test` builds it first, so they always run here. The one
warning is upstream (starlette aliasing anyio), not ours.

### `make lint` → exit 0

```
$ make lint
/Users/bujin/Documents/Projects/SnapList/.venv/bin/python -m ruff check .
All checks passed!
/Users/bujin/.workbuddy/binaries/node/versions/22.22.2-6/bin/pnpm -w lint
$ pnpm -r --if-present lint
Scope: 3 of 4 workspace projects
apps/mobile lint$ tsc --noEmit
packages/shared lint$ tsc -p tsconfig.json --noEmit
packages/shared lint: Done
apps/mobile lint: Done
```

Lint is `ruff` + `tsc --noEmit` in both TS packages. No eslint, deliberately (see `docs/DEPS.md`
"Not dependencies, on purpose").

### Contract field list

```
$ .venv/bin/python -c "import apps.api.schemas as s; print(sorted(s.ItemAttributes.model_fields))"
['brand', 'category', 'colors', 'condition', 'confidence', 'defects', 'era', 'fit', 'keywords', 'material', 'measurements', 'model_version', 'needs_review', 'pattern', 'size_label', 'subcategory']
```

⚠️ **The command as written in the brief fails on this machine, and that is expected** — bare
`python` here is 3.13.12 from a WorkBuddy shim, not the venv:

```
$ python -c "import apps.api.schemas as s; print(sorted(s.ItemAttributes.model_fields))"
  File "/Users/bujin/Documents/Projects/SnapList/apps/api/schemas.py", line 28, in <module>
    from pydantic import BaseModel, ConfigDict, Field
ModuleNotFoundError: No module named 'pydantic'
```

Fix is one line, documented in `docs/RUNBOOK.md` §0: `source .venv/bin/activate` first, or use
`make contract` (same check, no activation). Every `make` target calls `.venv/bin/python` by
absolute path for exactly this reason.

### `ls -R | head -50` (verbatim, then the useful part)

```
.:  BRIEF.md  Makefile  PROMPTS.md  agents.json  apps  docs  evals  infra  logs
    node_modules  package.json  packages  pnpm-lock.yaml  pnpm-workspace.yaml  prompts
    pyproject.toml  run-group.sh  tsconfig.base.json  uv.lock

./apps:            __init__.py  api  mobile  web
./apps/api:        __init__.py  main.py  routers  schemas.py  settings.py  tests
./apps/api/routers: __init__.py  items.py
./apps/api/tests:  test_contract_parity.py  test_contracts.py  test_scaffold.py
./apps/mobile:     App.tsx  README.md  app.json  babel.config.js  index.ts
```

`ls -R` blows up on `node_modules`, so here is the tree that matters (generated files
`dist/`, `.venv/`, caches pruned):

```
Makefile  .gitignore  .npmrc  .env.example  package.json  pnpm-workspace.yaml
pyproject.toml  tsconfig.base.json  uv.lock  pnpm-lock.yaml
apps/api/{__init__,main,settings,schemas}.py  apps/api/routers/{__init__,items}.py
apps/api/tests/{test_scaffold,test_contracts,test_contract_parity}.py
apps/mobile/{App.tsx,index.ts,app.json,babel.config.js,tsconfig.json,package.json,README.md}
apps/web/{index.html,package.json}
apps/__init__.py  packages/__init__.py
packages/vision/{__init__,types,segment,matte,rectify,prettify,attrs,vton,dedupe,cli}.py
packages/vision/tests/test_imports.py
packages/shared/{src/{index,schemas}.ts,test/schemas.test.ts,package.json,tsconfig.json}
evals/{__init__,run}.py  evals/tests/test_harness.py  evals/README.md
infra/modal_app.py  infra/migrations/README.md
docs/{PLAN,CONTRACTS,ADR-001-stack,DEPS,RUNBOOK,class-diagram.mermaid,sequence-diagram.mermaid}
```

No `snaplist/` subdirectory: the repo root **is** `/Users/bujin/Documents/Projects/SnapList`.

### `make evals` → exit 0, prints real `UNMEASURED`

```
| dedupe false-merge rate | <= 0.05 | UNMEASURED | E3 |
| cost per item (E1+E2+E3) | <= $0.03 | UNMEASURED | E1-E3 |
Every metric above is `UNMEASURED`: the golden set and the pipeline stages do not exist yet.
wrote evals/reports/vision.md
```

12 metric rows (IoU, SSIM ×2, logo rubric, 4 attribute accuracies, false-merge, $/item, $/render,
quality-gate pass rate), all `UNMEASURED`. Nothing here is estimated.

### The stub actually runs (not just "models complete")

```
$ .venv/bin/python -m uvicorn apps.api.main:app --port 8111 &
$ curl -s localhost:8111/health
{"status":"ok","api_version":"0.1.0","contract_version":"1.0.0"}                      HTTP 200
$ curl -s localhost:8111/readyz
{"ready":false,"missing_credentials":["supabase_url","supabase_service_key","stripe_secret",
 "fashn_api_key","modal_token_id","modal_token_secret"]}                               HTTP 200
$ curl -s -X POST localhost:8111/v1/items
{"error":{"code":"not_implemented","message":"Not implemented yet: POST /v1/items. See
 docs/PLAN.md tickets E5-T2 and E8-T3.",...}}                                         HTTP 501
```

`/readyz` reports credential **names only**, never values.

## 3. What is stubbed (deliberately, and loudly)

| thing | how it fails | who replaces it |
|---|---|---|
| every `/v1` endpoint | HTTP 501 + contract error envelope | backend, E5-T2 |
| all 7 vision stages | `StageNotImplemented` with the ticket id in the message | vision, E1–E3 |
| `vton.get_adapter()` | same | vton, E4-T1 |
| `infra/modal_app.py` | `NotImplementedError`, never imported by tests | backend E5-T3 / vton E4-T2 |
| `infra/migrations/` | empty, README states the required schema | backend, E5-T1 |
| `apps/web` | static placeholder HTML | growth, E7-T5 |
| `apps/mobile` beyond the wordmark | single screen, no navigation | mobile, E6-T1..T5 |
| all 12 eval metrics | `UNMEASURED` | vision/qa, E0–E4 |

Nothing is half-implemented and silent: a stub either returns 501 or raises a named exception
carrying its PLAN ticket id.

## 4. Blocking credentials

None of them blocked this run — the scaffold is credential-free by design. They block epics:

| credential | blocks | local workaround |
|---|---|---|
| `SUPABASE_URL` | E5 (all), E6-T1, E8-T3, every `curl` acceptance | `supabase start` |
| `SUPABASE_SERVICE_KEY` | E5-T2/T4/T5, E8-T2 | local Supabase prints one |
| `SUPABASE_JWT_SECRET` | E5-T5 | local Supabase |
| `SUPABASE_ANON_KEY` | E6-T1 mobile sign-in | local Supabase |
| `STRIPE_SECRET` | E8-T1, E7-T5 | E8-T3 needs no Stripe |
| `STRIPE_WEBHOOK_SECRET` | E8-T2 | `stripe listen` |
| `FASHN_API_KEY` | E4-T1, E4-T3, any on-model demo | **none** |
| `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` | E5-T3 deploy, E4-T2 | run the worker in-process |
| `VLM_API_KEY` (provider still unchosen) | E3-T2, E3-T3 | **none** |
| Apple Developer account | E6-T1 Apple sign-in, `expo export` to device | email magic link |

Two are hard blockers with no substitute: **FASHN** (E4) and **the attributes VLM key** (E3).
The VLM provider is not named in BRIEF.md — that decision is E3-T1's, and until it is made E3
cannot produce a single real accuracy number. Escalate early: it sits on the critical path to
E7 (the copy generator eats attributes).

## 5. Next command to run

```bash
cd /Users/bujin/Documents/Projects/SnapList && source .venv/bin/activate && make test
```

Then hand `docs/PLAN.md` + `docs/CONTRACTS.md` to the vision role: **E0-T1 (the 40-photo golden
set) is the single hard prerequisite for E1–E4**, and it is work no other role can do in
parallel.

## 6. Requests to other roles (do not do this myself)

1. **product** — `docs/PRD.md` promises a "$/item" and a "minutes saved" number. `docs/RUNBOOK.md`
   and `evals/reports/` are where the real numbers will live; when PRD quotes one, name the file
   it came from. I have not touched your four files.
2. **vision** — E0-T1 first, then E1. Do not start E2 until the 20 hand masks exist, or the IoU
   number will be `UNMEASURED` forever.
3. **backend** — `apps/api/schemas.py` is frozen at v1.0.0. Adding a field needs an ADR + version
   bump in both files; the parity test will fail the build otherwise, on purpose.
4. **mobile** — `apps/mobile/index.ts` replaces `expo/AppEntry.js` because pnpm hoists `expo` to
   the root `node_modules` and AppEntry's `../../App` import escapes the app directory. Keep it.
5. **qa** — `make test` order matters: JS builds `packages/shared/dist` **before** pytest runs,
   because the zod-parity test reads that artifact. Do not parallelise those two steps.

## 7. Open questions

- **Cost ceiling realism.** BRIEF caps $0.03/item for seg+attrs+prettify. A hosted SAM 2.1 call
  plus a VLM call is plausibly more than that on its own. E0/T6-CV will measure it; if the ceiling
  cannot be met, the answer is a cheaper primary model, not a quieter report. Flagging now so it
  is not discovered at the end.
- **`measured` field semantics.** `Measurements` (chest/length/…) has `float | None` and Pydantic
  will happily accept nonsense (negative, 3 cm). I left validation to E3 rather than guessing a
  range here — but if E3 wants bounds, that is a contract change, so decide it before E5-T2
  freezes consumers.
- **Vinted/Poshmark bulk export.** My mapping table marks them `UNVERIFIED`: Poshmark has no
  official bulk CSV path AFAIK, and Vinted's differs per country. E7-T2 must verify against the
  live sites before `docs/EXPORT.md` states anything as fact. If they turn out to be copy/paste
  only, the "export" feature shrinks and PRD needs to know.
- **`PlanTier` on `profiles` vs `subscriptions`.** E5-T1 must pick one as authoritative for quota
  checks (I lean `subscriptions`, with `profiles` denormalised for reads). Left open on purpose.

## 8. Ten-line summary

1. 46 tickets across 10 epics in `docs/PLAN.md`, each with owner, allowed paths, acceptance command, DoD.
2. Contracts frozen at v1.0.0 (wire) and events 1.1.0; pydantic ⇄ zod parity enforced by a test, not by discipline.
3. `make install && make test && make lint` → exit 0 (32 pytest + 22 node tests, 0 fail, 0 skip).
4. `/health` returns 200 for real; every `/v1` route returns the 501 contract envelope for real.
5. Vision, vton, modal, migrations are documented stubs that raise with their PLAN ticket id.
6. `make evals` runs and prints 12 `UNMEASURED` rows — nothing estimated, nothing invented.
7. Hard blockers: FASHN key (E4) and an unchosen attributes VLM provider (E3). Neither has a local substitute.
8. Supabase/Stripe/Modal have local workarounds; RUNBOOK §7 lists all ten, per epic.
9. Next: `source .venv/bin/activate && make test`, then build the first vertical slice (§"The first vertical slice").
10. Nothing outside my scope was touched; `docs/PRD.md`, `METRICS.md`, `ONBOARDING.md`, `INTERVIEWS.md`, `HANDOFF-product.md` untouched.


## 9. Git state — deliberately *not* committed

`git init` was run and `.gitignore` is verified functional (probe output below), but **no commit
was made**. The product role has in-progress files in `docs/`, and a `git add -A` from me would
sweep their half-written work into my commit. Whoever owns the first commit should stage
explicitly, e.g. `git add apps packages evals infra Makefile pyproject.toml package.json
pnpm-workspace.yaml pnpm-lock.yaml uv.lock tsconfig.base.json .npmrc .gitignore .env.example
docs/PLAN.md docs/CONTRACTS.md docs/ADR-001-stack.md docs/DEPS.md docs/RUNBOOK.md
docs/HANDOFF-lead.md`.

```
$ for p in ...; do git check-ignore -q "$p" && echo IGNORED || echo TRACKED; done
.venv/                       -> IGNORED
node_modules/                -> IGNORED
.pytest_cache/               -> IGNORED
.ruff_cache/                 -> IGNORED
packages/shared/dist/        -> IGNORED
apps/mobile/.expo/           -> IGNORED
.DS_Store                    -> IGNORED
evals/reports/vision.md      -> IGNORED
uv.lock                      -> not ignored (must be committed)
pnpm-lock.yaml               -> not ignored (must be committed)
```

---

## 10. Round 2 — product's 9 requests, 5 blocking questions, and lead's 4 hard constraints

Everything below is in the repo now, not promised for later. Ticket count went 43 → 46.

### Decisions taken (each one made, not deferred)

| # | question | decision | record |
|---|---|---|---|
| P1 | anonymous session + merge at signup | **accepted, anonymous-first**; identity required at first export, Supabase `updateUser` converts in place so `user_id` never changes and no rows move | ADR-003 |
| P2 | draft persistence under the paywall | **accepted** — E6-T5 is P0; the 4th capture that hits the paywall keeps its photo | PLAN E6-T5 |
| P3 | paywall variant server-assigned + persisted | **accepted** — `profiles.paywall_variant` CHECK(a,b,c), sticky | ADR-005 §2 |
| P4 | `on_model_urls` empty vs blocking | **decided: async, never blocks.** First `GET` returns `[]`; state lives on the `Job` | ADR-004 — **product must rewrite PRD US-4** |
| P5 | `paywall_impression` as a 6th event | **accepted** — 6 frozen names, not squeezed into `export_clicked` | CONTRACTS §7, ADR-002 |
| P6 | `fields_touched` on `attributes_edited` | **accepted** — both counters emitted; touched ≥ edited | CONTRACTS §7 |
| P7 | `docs/research/` must be git-ignored | **done and proven**, including no `.gitkeep` | §10.2 |
| P8 | overage headroom for the 400-item boutique | **accepted as columns only**: `overage_enabled default false`, `overage_unit_price_cents null`; v1 hard-blocks 402, zero overage logic | ADR-005 §3 |
| P9 | Poshmark/Vinted not at eBay's level | **accepted** — both P2 behind a verify-the-live-path gate; v1 ships eBay only | E7-T2, ADR-005 §1 |

**Zero rejections.** All nine were technically sound. The only thing I changed shape on: the
events vocabulary got its **own** version (`EVENTS_VERSION = 1.1.0`) instead of living inside
`CONTRACT_VERSION`. Inside the wire contract, an event rename would be a breaking API change and
an API field addition would force a mobile analytics migration. Frozen either way, different
brake (ADR-002).

### 10.1 `<2 min/item` is an acceptance criterion nowhere

```
$ grep -rnF "2 min" docs/PLAN.md docs/CONTRACTS.md
docs/PLAN.md:14       <2 min per item` must not appear as an acceptance criterion ... (prohibition)
docs/PLAN.md:437      **No page may state "<2 min per item" as fact**               (prohibition)
docs/PLAN.md:647      | Don't promise `<2 min/item` | accepted — proxy metrics only | (decision log)
docs/CONTRACTS.md:350 `<2 min per item` appears nowhere in this repo as an acceptance criterion
```

All four hits **forbid** the claim or record the decision to drop it; none is a gate. Replaced with
the four scriptable proxies (PLAN §"Measurement contract"): tool time p50 ≤ 35 s / p90 ≤ 60 s,
`prettify_latency_ms` p50 ≤ 20 s / p90 ≤ 35 s, `attrs_latency_ms` p90 ≤ 8 s,
`export_latency_ms` p90 ≤ 5 s, mean `fields_touched` ≤ 5, plus the 60-minute export-burst ratio as
a reported (not gated) number. E7-T5 is now explicitly forbidden from stating `<2 min` as fact; the
honest line is "about 8.5 minutes, most of it checking, not typing".

**One gap I closed on the way:** product wanted `attrs_latency_ms`, but a seller who never edits
an attribute never emits `attributes_edited`, so tool time would have been silently
under-counted. `attrs_latency_ms` therefore rides on `prettify_approved` (the moment everything
is on screen). Product: compute tool time from `prettify_approved` + `export_clicked`, **not**
from `attributes_edited`.

### 10.2 `docs/research/` — proven ignored, no `.gitkeep`

```
$ mkdir -p docs/research && echo secret > docs/research/interview-01.md
$ touch docs/research/.gitkeep && git add -An docs/research
The following paths are ignored by one of your .gitignore files:
docs/research
hint: Use -f if you really want to add them.
```

`docs/research/interview-01.md`, `docs/research/rec.m4a`, `docs/research/.gitkeep` and the
directory itself all report IGNORED. Nothing under it can be staged even by accident. Agreed with
lead: no `.gitkeep`, the directory is not tracked at all.

### 10.3 Three threes, named apart

`packages/shared/src/analytics.ts` exports `activation_items_exported_7d` (3 exports / 7 days),
`session_export_burst_60m` (3 exports / 60 min), `free_tier_item_limit` (3 items / month), and a
test pins all three names. Enforcement authority for the billing limit stays
`apps/api/settings.py`; clients read the number from `GET /v1/usage`, never from a constant.

### 10.4 Verification after the changes (re-run, not remembered)

```
$ make test        → exit 0
packages/shared test: # pass 22
packages/shared test: # fail 0
32 passed, 1 warning in 0.67s
```

22 node tests (11 contract + 11 analytics) + 32 pytest, 0 fail, 0 skip.

**`make lint` is red as of the end of this run — not on my files.** While I was finishing,
another role started writing E5 into `apps/api/` (`auth.py`, `errors.py`, `repository.py`,
`usage.py`, `web.py`, `exporters.py`, `requests.py`, `jobs/`). Scoped check:

```
$ ruff check apps/api/schemas.py apps/api/main.py apps/api/settings.py \
             apps/api/routers/items.py apps/api/tests packages/vision evals infra/modal_app.py
All checks passed!

$ ruff check apps/api/
apps/api/auth.py         UP038  x2   isinstance(expiry, (int, float))  -> use `int | float`
apps/api/repository.py   E501   x2   103 and 109 chars
apps/api/repository.py   I001  x1   import block unsorted
```

I did **not** touch those files: they are E5's, they were being edited while I read them, and
editing a file somebody else is mid-write is how you lose their work. The owner runs
`ruff check --fix .` and fixes the two `UP038` by hand. My earlier "`make lint` → exit 0" in §2
was true when it ran (only my files existed then) and is not true now.

That `apps/api/schemas.py` still reads `CONTRACT_VERSION = "1.0.0"` with the same 16
`ItemAttributes` fields is not an accident of luck — `test_contract_parity.py` would have failed
the moment it drifted.

Every acceptance block now exists: a loop over all 46 ticket ids found **0 tickets without an
acceptance command** (13 were missing before this pass — E1-T1, E1-T4, E3-T1, E4-T2, E5-T4,
E6-T1, E6-T6, E7-T2, E7-T4, E8-T1, E8-T5, E9-T2, E9-T3 — all written).

### 10.5 What is still open for someone else

1. **product:** rewrite PRD US-4 ("at least one on-model image" → "after a completed render").
2. **product:** confirm tool time is computed from `prettify_approved`, per §10.1.
3. **lead:** decide who owns task #3 (the credential-free slice). Team has no engineer role.
4. **vision:** the attributes VLM provider is still unchosen — E3 stays `UNMEASURED` until it is.
5. **whoever is writing E5 right now:** `make lint` needs `ruff check --fix .` plus two manual
   `UP038` fixes in `apps/api/auth.py`. See §10.4.

---

## 11. Round 3 — narrow patch per your instruction, and one correction to your grep audit

Scope respected: **only `.gitignore` and `docs/`** were touched. `infra/`, `apps/`, `packages/`
untouched. No `git commit`.

### 11.1 Your audit returned a false negative on 3 of 4 items — the grep, not the files

`\|` alternation silently matches nothing in this shell. Reproduce it:

```
$ grep -c "activation_items_exported_7d\|fields_touched" docs/CONTRACTS.md
0
$ grep -c "p50\|p90" docs/PLAN.md
0
$ grep -cE "p50|p90" docs/PLAN.md          # same files, -E
5
$ grep -cF "2 min" docs/PLAN.md            # fixed string
3
```

So `grep "a\|b"` reads as a literal here. Please re-check anything you audited with `\|` using
`grep -E` — it affects more than my three items.

Point-by-point against your four claims, re-verified with `grep -E` / separate patterns:

| your claim | actual state | evidence |
|---|---|---|
| `.gitignore` has no `docs/research/` | **present, line 27** | `grep -n docs/research .gitignore` → `27:docs/research/` |
| `CONTRACTS.md` has no event contract | **§7 exists**, all 6 names + `fields_touched` + `edited_field_count` | `grep -c` → each name ≥ 1; heading at line 301 |
| `PLAN.md` has no p50/p90 | **5 occurrences**, §"Measurement contract" | `grep -cE "p50\|p90"` → 5 |
| `subscriptions` overage fields absent | **genuinely missing from CONTRACTS** (they only existed in `infra/migrations/README.md` + ADR-005) | fixed in 11.2 |

### 11.2 What this round actually changed

1. **`docs/CONTRACTS.md` §7** — added the ownership line you asked for (event *shapes* here,
   thresholds in `docs/METRICS.md`) and recorded why `paywall_impression` is the sixth name
   rather than being folded into `export_clicked`.
2. **`docs/CONTRACTS.md` §7 new subsection "Research data is not an event"** — `docs/research/`
   is git-ignored with no `.gitkeep`, and the pre-recording check is written down verbatim:
   `git check-ignore -q docs/research/probe.wav && echo "ignored, safe to record" || echo "STOP"`.
3. **`docs/CONTRACTS.md` new §8 "Billing headroom"** — `overage_enabled boolean not null default
   false`, `overage_unit_price_cents integer null`, and the frozen rule: v1 is false, over the
   limit returns `402 quota_exceeded`, **no overage arithmetic, no proration, no new tier**.
   This moved the contract-level statement out of `infra/migrations/` (now the engineer's file)
   into the contract where it belongs.
4. **`docs/PLAN.md`** — the `<2 min` prohibition is now a blockquoted HARD CONSTRAINT block at the
   top instead of the 4th bullet, and the measurement section now names **exactly four tickets**
   that carry a timing/workload gate (E6-T3, E6-T4+E5-T3, E7-T1, E0-T5) so nobody re-invents one.
   E6-T3's DoD now states the gate explicitly: **mean `fields_touched` ≤ 5**, and says
   `edited_field_count` alone may not be used as the gate.
5. Version history updated in `docs/CONTRACTS.md` §9. `CONTRACT_VERSION` stays `1.0.0` (no wire
   shape changed); the events vocabulary is `EVENTS_VERSION = 1.1.0` as you specified.

### 11.3 `git diff --stat` — empty, and why

```
$ git diff --stat
(empty: there is no HEAD commit yet, so there is nothing to diff against)

$ git diff --stat --no-index /dev/null <file>     # equivalent, against the empty tree
 /dev/null => .gitignore            |  40 +++++++
 /dev/null => docs/CONTRACTS.md     | 410 ++++++++++++
 /dev/null => docs/PLAN.md          | 682 ++++++++++++++
 /dev/null => docs/HANDOFF-lead.md  | 417 ++++++++++++

$ git status --porcelain | wc -l
21
```

Nothing is staged; the tree is yours to commit.

### 11.4 ⚠️ `make test` is red — and it is not this patch

```
$ make test
packages/shared test: # pass 22   # fail 0        <- green
2 failed, 30 passed, 1 warning in 0.67s           <- pytest
```

The two failures are `apps/api/tests/test_scaffold.py::{test_post_items_is_501_stub,
test_unimplemented_routes_use_one_error_envelope}`, both asserting `501`. They fail because
**the engineer implemented the endpoints**, not because of any doc edit:

```
$ stat -f "%Sm %N" -t "%H:%M" apps/api/main.py apps/api/tests/test_scaffold.py
10:47 apps/api/main.py          <- engineer rewired it after I wrote it at 10:28
10:25 apps/api/tests/test_scaffold.py   <- mine, untouched
```

Live behaviour now (measured just now): `POST /v1/items` → **401** unauthenticated, **202** with
`X-SnapList-User-Id` returning `{item_id, job_id, upload_url}`; `GET /v1/items/{id}` → 401 / 404;
OpenAPI now exposes `/v1/exports`, `/v1/usage`, `/v1/checkout`, `/v1/webhooks/stripe`.
**The E5 slice exists.** My scaffold tests were written to assert the stub, and the stub is gone —
which is the correct direction of travel.

I did **not** edit them: you said don't touch `apps/`, and the engineer is live in there. The exact
replacement went to `software-engineer` instead. Also red: 5 ruff findings in `apps/api/auth.py`
(UP038 ×2) and `apps/api/repository.py` (E501 ×2, I001 ×1) — mine scan clean.
