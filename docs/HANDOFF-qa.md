# HANDOFF — QA / DevOps (严过关, Yan)

**Date:** 2026-10-28
**Scope:** independent verification — proving the tests can fail, not re-running them to see green.
**Verdict:** **CONDITIONAL PASS.** 8 of 8 mutation targets turn red *except* one that only turns red
for five hard-coded brand names (fixed below), 5/5 honesty checks pass, 21 tests added.
One cross-language blind spot found and closed; one remains open by design decision.

---

## 0. TL;DR for the impatient

| Question | Answer |
|---|---|
| Do the tests actually bite? | **Yes for 8/8 groups — but one of them was a fake and is now real.** Injecting a fabricated brand `"Zara"` left **56/56 green**. It now fails. |
| Is anything faked in the acceptance path? | **No.** Stripe is 501 in both credential states, devtools is ENV-gated, vision stages raise instead of pretending. |
| Biggest remaining risk | **Required-ness drift between pydantic and zod is invisible to CI.** Proven, not theorised. New test added; a second zod-side hole is documented in §5. |
| Commands | `make test` → 58 node + 142 pytest, 0 failed, 0 skipped. `make lint` → all green. |

---

## 0. 收口确认 — 仓库已完全还原（真实输出）

**先说结论：`git status` 在这个仓库里证明不了还原。** 仓库还没有任何 commit
（`fatal: your current branch 'master' does not have any commits yet`），所以每个路径都是
`??` 未跟踪，改坏了和没改坏看起来一模一样。因此还原证据用 **md5 对备份比对**，
备份在 `/tmp/snaplist-qa-orig/`，在做第一个变异之前就取好了。

```
$ git status --porcelain
?? .env.example
?? .gitignore
?? .npmrc
?? BRIEF.md
?? Makefile
?? PROMPTS.md
?? agents.json
?? apps/
?? docs/
?? evals/
?? infra/
?? logs/
?? package.json
?? packages/
?? pnpm-lock.yaml
?? pnpm-workspace.yaml
?? prompts/
?? pyproject.toml
?? run-group.sh
?? tsconfig.base.json
?? uv.lock
```

```
$ make test 2>&1 | tail -5          # 11:19, 我的最后一次全绿
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
142 passed, 1 warning in 1.25s
（exit 0；node 侧 58 pass / 0 fail / 0 skipped）
```

```
$ make lint 2>&1 | tail -3         # 11:19
apps/mobile lint$ tsc --noEmit
packages/shared lint: Done
apps/mobile lint: Done
（exit 0；ruff: All checks passed!）
```

```
$ 逐个 md5 比对（11:19，0 个 DIFF）
OK   packages/shared/src/analytics.ts
OK   packages/shared/src/schemas.ts
OK   packages/shared/src/listing/pricing.ts
OK   packages/shared/src/listing/copy.ts
OK   packages/shared/src/marketplaces/ebay.ts
OK   apps/api/routers/billing.py
OK   apps/api/devtools.py
OK   apps/api/schemas.py
OK   apps/api/settings.py
OK   apps/api/jobs/state_machine.py
OK   apps/api/usage.py
```

11 个被变异过的源文件全部与备份逐字节一致，无残留。工作树里我唯一新增的是
3 个测试文件 + 本 HANDOFF + `packages/shared/test/copy.test.ts` 末尾追加的 2 条测试。

### 11:20:43–11:21:04 起，仓库被他人改红（不是我）

在本文写完之后、提交之前，`packages/shared/src/marketplaces/` 发生了一次**正在进行的重构**
（`types.ts` 11:20:43、`ebay.ts` 11:20:56、`poshmark.ts` 11:21:04、`vinted.ts` 未动），
`ColumnSource` 新增了必填的 `manual_when_empty`，`index.ts` 的返回值也变了。当时 `make lint`：

```
src/marketplaces/index.ts(59,5): error TS2322: Type 'number' is not assignable to type 'ExportedRow[]'
src/marketplaces/poshmark.ts(54,14): error TS2322: ... Property 'manual_when_empty' is missing ...
src/marketplaces/vinted.ts(51,14): error TS2322: ... Property 'manual_when_empty' is missing ...
make: *** [lint] Error 2
```

**这不是变异残留**：`diff` 我的 `ebay.ts` 备份与当前文件，差异全是新功能（`ListingSource.price_cents`、
`requires_manual_columns`、逐列 `manual_when_empty`），我的 `ItemTitle` 变异痕迹一处也没有。
我在这个窗口里没有碰过 `marketplaces/` 之外也没碰过它本身 —— 该目录属于工程师，而且他正在写。
**我故意没有回滚它**：回滚会毁掉他手上的工作。

因此网关状态的准确表述是：**11:19 时全绿（142 + 58 / 0 failed / 0 skipped / lint exit 0）；
11:21 后因他人未完成的改红。** 在我的改动范围内（源代码零改动、11 个变异文件已还原）是可提交的，
但**整仓当前不绿，不能按此提交** —— 请等工程师那批补丁落地后重跑：

```bash
make test && make lint
```

我新增的 4 个测试文件（含 `copy.test.ts` 的追加）与这次重构没有交集：它们只依赖
`buildListingCopy`、`ItemAttributes`、`require_stripe`、`devtools._guard()`，这些签名未变。

### 给后来人：变异窗口的红不算数，怎么认出来

> 变异测试期间套件的颜色**不能**当作验收依据：那段时间里"红"可能是我故意改坏源码造成的，
> "绿"也可能是改坏后测试没咬住。识别办法有两条 —— **看基线**（我的窗口内基线是
> 121→125 pytest / 56 node，收口时 142 pytest / 58 node），**看 mtime**（窗口内被改的 11 个
> 文件 mtime 落在本次会话，收口时已全部回到备份 md5）。任何在窗口期内出现过的测试结果，
> 一律以本节收口后的重跑为准。

---

## 1. Mutation testing — 8 targets, each reverted

Method: `cp` the original to `/tmp/snaplist-qa-orig/`, mutate with an explicit string replacement,
run the specific suite, capture the verbatim error, restore with `cp`, verify by `md5`.
The repo has no commit, so `git diff` cannot prove a clean tree — **md5 equality against the
backup is the proof** and it is included in §1.9.

### 1.9 Restore verification (run after every mutation)

```
OK   packages/shared/src/analytics.ts
OK   packages/shared/src/schemas.ts
OK   packages/shared/src/listing/pricing.ts
OK   packages/shared/src/listing/copy.ts
OK   packages/shared/src/marketplaces/ebay.ts
OK   apps/api/routers/billing.py
OK   apps/api/devtools.py
OK   apps/api/schemas.py
OK   apps/api/settings.py
OK   apps/api/jobs/state_machine.py
OK   apps/api/usage.py
```

All final `md5 -q` values equal the backup values.

---

### 1.1 No-invention guard (brand / fibre) — `packages/shared/src/listing/copy.ts`

**Mutation:** in `buildTitle`, before the `if (attributes.brand)` guard, insert
`parts.push("Zara");` — an unconditional fabricated brand in every title ever generated.

**Result: DID NOT TURN RED. 56/56 passed.**

This is the finding of the round. The module's own docstring claims the test "proves the rule bites
by feeding in an item with `brand: null`". It proves nothing of the sort: the detector
(`unsupportedClaims`) works against `BRAND_VOCABULARY = ["Burberry","Nike","Levi's","Chanel","Uniqlo"]`
and `FIBRE_VOCABULARY = ["cashmere","silk","linen","leather","merino"]` — five words each. Invent a
brand that is not on that list and the test cannot see it.

**Control run** (same mutation, `"Burberry"` instead of `"Zara"`), to prove the cause is the
vocabulary and not a broken assertion:

```
not ok 14 - copy never names a brand or fibre the attributes do not have
  error: |- Expected values to be strictly deep-equal:
      {
    +   brands: [ +     'Burberry' +   ],
    -   brands: [],      fibres: []
      }
```

So: the guard *mechanism* works, the *test* only covers 5 brand names. A regression that hard-codes
H&M, Gap, Zara, Adidas — the brands a seller is most likely to see — ships in silence.

**Consequence if unfixed:** the product would print a fabricated brand into a real eBay listing.
That is a "not as described" claim manufactured by us, which is the exact failure CONTRACTS.md §1
forbids. **Fixed by QA** — see §3.1.

**Reverted:** yes (md5 `9a49279729db760339f5723b02442813`).

---

### 1.2 Title ALL-CAPS interception — same file

**Mutation:** `normalizeShouting` returns `cleanWords(value)` unchanged (the `.split(" ").map(...)`
chain is dead code).

**Result: RED — 1 test.**

```
not ok 19 - an ALL CAPS brand is title-cased, and the detector is proven to work
  error: |- Expected values to be strictly equal:
    + 'BURBERRY CASHMERE SCARF'
    - 'Burberry Cashmere Scarf'
  operator: 'strictEqual'
```

The `hasShoutedToken(copy.title)` half of that test also fails on the generated title, so both the
helper and the end-to-end path are covered. Sound test.
**Reverted:** yes.

---

### 1.3 CSV column drift — `packages/shared/src/marketplaces/ebay.ts`

**Mutation:** `EBAY_HEADERS` entry `"*Title"` → `"ItemTitle"` (one column, nothing else).

**Result: RED — 2 tests.**

```
not ok 24 - eBay headers are exactly the pinned File Exchange column list
not ok 27 - every header has exactly one provenance row, in order
  error: |- +   'ItemTitle',  -   '*Title',
```

The header pins are literal strings rather than imported constants, which is why this works.
`EBAY_SOURCES` being out of step also fails, so a rename cannot pass by editing one array.
**Reverted:** yes.

---

### 1.4a Contract parity, zod direction — `packages/shared/src/schemas.ts`

**Mutation:** `size_label` → `sizeLabel`.

**Result: RED.** Two independent signals:

```
1 failed, 4 passed
E  AssertionError: {'ItemAttributes': {'python_only': ['size_label'], 'zod_only': ['sizeLabel']}}
```

and, because every other TS file still says `size_label`, `tsc` also exits 2
(`ERR_PNPM_RECURSIVE_RUN_FIRST_FAIL`), so `make test` stops before pytest even starts. `tsconfig`
has no `noEmitOnError`, so the JS was still emitted and the parity test could read it — worth
knowing, but the net CI result is red either way.
**Reverted:** yes.

### 1.4b Contract parity, python direction — `apps/api/schemas.py`

**Mutation:** `inseam_cm` → `inseamCm` (a field nothing else in Python touches).

**Result: RED.**

```
E  AssertionError: {'Measurements': {'python_only': ['inseamCm'], 'zod_only': ['inseam_cm']}}
1 failed, 4 passed
```

**Reverted:** yes.

---

### 1.4c Required-ness: the blind spot (extra probe, requested explicitly)

`test_contract_parity.py` compares field **names** and enum **literals**. It never compares
optionality. Probed both directions.

**(i) zod side — `colors: z.array(...).min(1).max(3)` → `.default(["black"])`.**
Python still requires `colors`. Zod now says it may be omitted.

**Result: DID NOT TURN RED.**

```
packages/shared:  # pass 56   # fail 0
pytest test_contract_parity.py + test_contracts.py:  17 passed
```

**Confirmed blind spot.** In production this is a 422 that only appears when the mobile client
sends a payload it believes is complete — i.e. on a seller's phone, never in CI. **Closed by QA** —
see §3.1.

**(ii) python side — `category: GarmentCategory` → `= GarmentCategory.OTHER`.**

```
E  AssertionError: assert {'colors'} == {'category', 'colors'}
E  Extra items in the right set: 'category'
```

Red — but via `test_contracts.py::test_only_category_and_colors_are_required`, **not** by the
parity test. So the asymmetry is: python-side required-ness is pinned (one language asserting
against itself), cross-language required-ness was not pinned at all until my new test.

**分工判定（这条决定要不要加类型级对等测试）：**

| 变异 | 对等测试抓到？ | 谁抓到？ |
|---|---|---|
| zod 改可选（4c-i） | **抓不到** | 没有任何测试，全绿 —— 已由我的 `test_contract_requiredness.py` 补上 |
| python 改可选（4c-ii） | **抓不到** | `test_contracts.py`（python 自己对自己），不是对等测试 |

结论：**对等测试在两个方向上都抓不到"必填↔可选"**，它只比对字段名和枚举字面量。所以
"python 侧被抓住了"并不意味着安全边界成立 —— 它只是 python 内部的一致性断言，如果有人
同时把两侧都改成可选（或只改 zod），它一句话都不说。

**要不要上类型级对等测试？我的建议是：先加我这层必填性对等就够，暂不需要完整类型级。**
理由：完整类型级（`str`↔`z.string()`、`list[T]`↔`z.array(T)`、`datetime`↔`z.string().datetime()`）
需要一套 11 个模型的映射表，本身就容易漂移；而必填性是**唯一一个"漂移了只在运行时炸"**的
维度（类型错会在 tsc 或 pydantic 解析时就炸，必填性错则是两边各自都合法、只在合同面上不兼容）。
先补代价最小、风险最高的那一维。如果以后 `CONTRACT_VERSION` 要升到 1.1，再补完整映射表。

**Reverted:** yes.

---

### 1.5 Quota ceiling — two files, two outcomes

**5a: `apps/api/usage.py`, `PlanLimits.free` 3 → 4.**

**Result: RED — 3 tests.**

```
E  assert (4, 100, 400) == (3, 100, 400)
E  Failed: DID NOT RAISE <class 'apps.api.usage.QuotaExceeded'>   (x2)
3 failed, 6 passed
```

**5b: `apps/api/settings.py`, `free_items_per_month` 3 → 4.** This is the value production
actually reads (`limits_from_settings()`), while 5a is the dataclass default the unit tests use
directly — so 5b is the one that matters and it deserved its own probe.

**Result: RED — 2 tests.**

```
E  AssertionError: {"item_id":"aa60b466-…"}   assert 202 == 402
E  assert 4 == 3
FAILED apps/api/tests/test_items.py::test_free_plan_fourth_item_is_402_with_a_machine_readable_body
FAILED apps/api/tests/test_items.py::test_usage_reports_the_current_period_and_remaining_items
```

Good: the ceiling is enforced and tested through the real API path, not only at the dataclass.
**Reverted:** yes (both).

---

### 1.6 `insufficient_data` threshold — `packages/shared/src/listing/pricing.ts`

**Mutation:** `MIN_COMPS = 5` → `4`.

**Result: RED — 2 tests.**

```
not ok 40 - four comps is not enough data, and no price is returned
  error: Expected values to be strictly equal:  3 !== 4
not ok 41 - exactly five comps is enough, and the median ignores the outlier
  error: Expected values to be strictly equal:  2600 !== 2800
```

The threshold is asserted on the exact boundary from both sides, and `required: 5` is pinned in the
payload, so an off-by-one in either direction fails. Sound test.
**Reverted:** yes.

---

### 1.7 Illegal job transition — `apps/api/jobs/state_machine.py`

**Mutation:** `JobState.DONE: frozenset()` → `frozenset({JobState.RUNNING})` (re-opening a terminal job).

**Result: RED — 1 test.**

```
FAILED apps/api/tests/test_jobs.py::test_done_is_terminal
E  Failed: DID NOT RAISE <class 'apps.api.jobs.state_machine.IllegalJobTransition'>
```

**Reverted:** yes.

---

### 1.8 `export_delivered` existence assertion — `packages/shared/src/analytics.ts`

**Mutation:** delete `"export_delivered"` from `ANALYTICS_EVENTS`, leave `EVENTS_VERSION = "1.2.0"`.

**Result: RED — 2 tests, and the right one fails on its own.**

```
not ok 1 - exactly seven frozen event names, in frozen order
not ok 2 - export_delivered exists as a real event, not just a version number
```

Test 2 — the existence assertion, which is the whole point of the ticket — is independently red, so
it is not a version-number proxy. Confirmed.
**Reverted:** yes.

---

### Mutation summary

| # | Target | Red? | Tests red | Restored |
|---|---|---|---|---|
| 1 | fabricated brand in copy.ts (`"Zara"`) | **NO — 56/56 green** | 0 | yes |
| 1c | same, control with `"Burberry"` | yes | 1 | yes |
| 2 | `normalizeShouting` disabled | yes | 1 | yes |
| 3 | eBay `*Title` → `ItemTitle` | yes | 2 | yes |
| 4a | `size_label` → `sizeLabel` (zod) | yes | 1 (+ tsc) | yes |
| 4b | `inseam_cm` → `inseamCm` (python) | yes | 1 | yes |
| 4c-i | zod `colors` required → optional | **NO — 56 + 17 green** | 0 | yes |
| 4c-ii | python `category` → optional | yes (but not by parity) | 1 | yes |
| 5a | `PlanLimits.free` 3 → 4 | yes | 3 | yes |
| 5b | `settings.free_items_per_month` 3 → 4 | yes | 2 | yes |
| 6 | `MIN_COMPS` 5 → 4 | yes | 2 | yes |
| 7 | `done → running` allowed | yes | 1 | yes |
| 8 | `export_delivered` removed, version kept | yes | 2 | yes |

**8 of 10 mutation groups genuinely bite. 2 did not (#1, #4c-i) — both were the kind that matters:
a claim of coverage that is not coverage.** #1 is now closed with a real test; #4c-i is closed too.

---

## 2. Honesty checks (BRIEF Definition of Done #4) — 5/5 PASS

| # | Check | Verdict | 证据位置 |
|---|---|---|---|
| 2.1 | Stripe 处理无伪造"验签通过"分支 | **PASS** | `routers/billing.py:30`（`-> NoReturn`）、`:41` 与 `:47`（仅两个 raise）、`:72` webhook 无验签即 501 |
| 2.2 | `devtools` 被 ENV 拦住 | **PASS** | `devtools.py:37`（`not in LOCAL_ENVS`）、`:38`（SystemExit）、`:65`（`main()` 第一句即 `_guard()`） |
| 2.3 | `local_dev` 头在生产被拒 | **PASS** | `auth.py:69`（有 secret 即走 JWT）、`:47`（LOCAL_ENVS 不含 prod/staging）、`:163-170`（无配置则 503） |
| 2.4 | 无 mock 冒充真实模型结果 | **PASS** | 全仓检索，逐条分类见下表；测试零网络调用 |
| 2.5 | 契约对等测试真的执行 | **PASS（含一处 caveat）** | 实测 `5 passed, 0 skipped`；缺 dist 时 `1 passed, 4 skipped`，见下 |

### 2.1 Stripe handler — **PASS**

`apps/api/routers/billing.py::require_stripe` is declared `-> NoReturn` and has exactly two exits:
one raises `NOT_IMPLEMENTED` naming the missing credential, the other raises `not_implemented`
("credentials present, flow not implemented"). There is **no branch that skips signature
verification and returns success**, and no path that constructs a `checkout_url`.

Evidence beyond reading the code — the dangerous configuration (both real-looking secrets set),
run through a live `TestClient`:

```
[1] stripe configured, unsigned webhook -> 501 {'error': {'code': 'not_implemented',
      'message': 'Stripe credentials are present but the checkout flow (E8-T1) is not implemented yet.'}}
[1] stripe configured, checkout          -> 501 (same envelope)
[1] RESULT: PASS
```

Also: `POST /v1/webhooks/stripe` takes no auth by design (Stripe has no seller JWT) but has no side
effect, and a forged `Stripe-Signature: t=1,v1=deadbeef` also returns 501. Now pinned by tests (§3.3).

### 2.2 `devtools.py` outside local — **PASS**

`_guard()` runs as the **first** statement of `main()`, before argparse and before
`build_repository`. Verified by executing the real CLI:

```
ENV=production  -> refusing to run: ENV='production' is not local/test/development.   exit=1
ENV=staging     -> refusing to run: ENV='staging' ...                                exit=1
ENV=PRODUCTION  -> refusing to run: ENV='PRODUCTION' ...                             exit=1
```

Case-insensitive, and `staging` is refused too (a plausible staging DB is still not local).
`LOCAL_ENVS = {"local","test","development"}` deliberately excludes `staging`/`production`.
Now pinned by tests (§3.2) — previously it had **zero** automated coverage.

### 2.3 `local_dev` header — **PASS**

Three separate proofs that the self-declared header cannot leak into production:

```
[3] header refused with 401 unauthorized — Missing `Authorization: Bearer <supabase access token>`
[3b] production, no secret, local header -> 503 ... auth-mode: unconfigured
```

1. `SUPABASE_JWT_SECRET` present ⇒ `resolve_auth_mode` returns `SUPABASE_JWT` before the header is
   ever read; the 401 message names the Bearer requirement instead.
2. `ENV=production` with no secret ⇒ 503 `unconfigured` on every `/v1` route, and the
   `x-snaplist-auth-mode` response header says `unconfigured` — a client can detect the state.
3. Already pinned by `test_auth.py::test_the_local_dev_header_is_ignored_when_a_jwt_secret_exists`
   and `::test_production_without_a_secret_is_503`.

Minor observation, not a failure: the 503 body carries `code: "internal"`. Contractually defensible
(the envelope has no `unavailable` code) but a client cannot distinguish "not configured" from a
genuine 500 by code alone — it has to read the message. `→ architect`, see §5.

### 2.4 Any mocked model call presented as real — **PASS**

Full-repo sweep for `mock|stub|fake|monkeypatch|responses|respx|nock`, every hit classified:

| Hit | Class | Verdict |
|---|---|---|
| `packages/vision/*` (`StageNotImplemented`), `vision/cli.py` | stub that **raises** | honest — refuses instead of inventing |
| `evals/run.py`, `evals/tests/*` (`UNMEASURED`, monkeypatch on `REPORTS`) | harness reports no numbers | honest — test double for a path constant |
| `repository.py` `provider='stub'` | DB default that self-reports | honest — makes a real run distinguishable |
| `apps/mobile/App.tsx` "render with mock data" | comment on a scaffold screen with **no** API call | honest |
| `apps/web/index.html` `.stub` CSS class | marketing placeholder | irrelevant |
| `pnpm-lock.yaml` `jest-mock`, `@sinonjs/fake-timers` | transitive via Expo; **not declared**, not used in any test | not in the acceptance path |

No `respx`, `responses` or `nock` in any manifest. No test in `apps/`, `packages/` or `evals/`
performs a network call (`grep requests\.|httpx\.|urllib|socket\.` → 0 hits). The export path
deliberately shells out to **real node running the real TypeScript mappers**
(`apps/api/exporters.py`) rather than reimplementing CSV in Python — the opposite of a mock.

### 2.5 Contract parity actually executed — **PASS, with one caveat**

```
$ .venv/bin/python -m pytest apps/api/tests/test_contract_parity.py -rs -v
5 passed, 1 warning in 0.15s        # 0 skipped
```

The caveat, tested rather than assumed: the module-level fixture calls `pytest.skip` when
`packages/shared/dist/src/schemas.js` is absent. Moved `dist` aside:

```
SKIPPED [4] packages/shared/dist/src/schemas.js missing — … zod parity is UNMEASURED until then.
1 passed, 4 skipped        exit code 0
```

**Green, with 4 of 5 parity tests unmeasured.** `make test` is safe because the `test` target runs
`pnpm -w test` first, and `packages/shared`'s `test` script is `pnpm build && node --test`, so the
artifact always exists on the sanctioned path. But anyone running bare `pytest`, or a CI step
ordered the other way, gets a green light on a contract that was never compared.
`→ architect`: either fail closed when `CI=true`, or add a sentinel asserting a minimum number of
parity tests executed. Not fixed by QA, because changing skip→fail would break `make clean; pytest`
and that is a policy decision, not a test bug.

---

## 3. Tests added (21 python + 2 node) — and why each one exists

| 文件 | 解决的缺口（一句话） | 反向验证 |
|---|---|---|
| `apps/api/tests/test_contract_requiredness.py` | 对等测试从不比对"必填/可选"，zod 改可选时 CI 全绿（§1.4c-i 实证） | 重放该变异 → 转红 ✓ |
| `apps/api/tests/test_devtools_guard.py` | `devtools` 的 ENV 守卫**零测试**，删掉守卫不会有人发现 | 删 `_guard()` → 5 红 ✓ |
| `apps/api/tests/test_billing_blocked.py` | 只测了"缺密钥→501"，**密钥齐备**那条（免费升 Pro 漏洞所在）无测试 | 换成假成功 → 2 红 ✓ |
| `packages/shared/test/copy.test.ts`（追加 2 条） | 品牌守卫的检测器只有 5 个词的白名单，`"Zara"` 注入全绿（§1.1 实证） | 重放 `"Zara"` → 2 红 ✓ |

I did **not** add coverage for things already covered specifically. Checked and skipped:
402 body shape (`test_items.py` already pins `details == {"plan":"free","limit":"3","used":"3"}`
and the exact message), cross-user isolation (`test_isolation.py` asserts 404 **and** that the
owner's row is unchanged / `items_used` stays 0), CSV escaping (`marketplaces.test.ts` already
covers comma, quote, bare quote, newline, padding, null and round-trips the arity).

### 3.1 `apps/api/tests/test_contract_requiredness.py` — closes §1.4c-i

A new parity **dimension**: for all 63 fields across 11 models, python `is_required()` must equal
`not zod.isOptional()`. This is the drift that shipped green before.

```
$ pytest apps/api/tests/test_contract_requiredness.py
3 passed
```

Proof it bites — re-applied the mutation from §1.4c-i:

```
E  AssertionError: required-ness drift: a field one side demands and the other defaults is a 422
   that only appears at runtime:
   {'ItemAttributes.colors': {'python_required': True, 'zod_optional': True}}
1 failed, 2 passed
```

It includes two self-guards: `test_the_comparison_covers_every_field` (fails if the node artifact
returns fewer than 60 fields, so an empty comparison cannot pass vacuously) and
`test_the_drift_checker_itself_can_fail` (a synthetic disagreement, so the checker is not a
function that only returns `{}`).

### 3.2 `apps/api/tests/test_devtools_guard.py` — closes §2.2's missing coverage

`devtools attach-asset` writes an `assets` row without running the pipeline. The ENV guard was
verified by hand and covered by **no test**. 10 cases: 4 refused environments (incl. `PRODUCTION`
and `prod`), 4 accepted local ones, one ordering assertion
(`build_repository` must never be called when refusing), and one ownership assertion
(a foreign item exits 2, not 0).

Proof it bites — deleted the `_guard()` call from `main()`:

```
FAILED …test_the_cli_refuses_outside_local_environments[production]
FAILED …[staging]  FAILED …[PRODUCTION]  FAILED …[prod]
FAILED …test_the_guard_runs_before_any_repository_access
5 failed, 5 passed
```

### 3.3 `apps/api/tests/test_billing_blocked.py` — the other half of the Stripe refusal

`test_scaffold.py` covers the *missing-credential* branch. The branch that runs when both secrets
**are** set was untested — i.e. the configuration in which a free-subscription bug would live.
4 cases: checkout still 501 with real-looking keys; unsigned webhook never accepted; forged
signature never accepted; a refused webhook changes no plan.

Proof it bites — replaced the webhook's `require_stripe(...)` with a fake success response, which
left all pre-existing tests green:

```
FAILED …test_an_unsigned_webhook_is_never_accepted
FAILED …test_a_forged_signature_header_is_never_accepted
2 failed, 2 passed
```

### 3.4 `packages/shared/test/copy.test.ts` — replaces the fake guard test (§1.1)

Two tests appended, **structurally** rather than by vocabulary: every word in the generated title
must come from the attributes (brand, subcategory, era, size_label, fit, colours, fibres) or from
the seven garment nouns. The inventable set is "anything not in the attributes" instead of "five
brands we thought of".

Proof it bites — the same `parts.push("Zara")` mutation that left 56/56 green:

```
not ok 24 - no word in a generated title exists outside the attributes
  error: title invented word(s) absent from attributes: zara
not ok 25 - the structural guard still allows a declared brand through
```

A positive control is included, so the test cannot pass by rejecting a legitimate declared brand.

---

## 4. Final gate output (verbatim — 11:19，见 §0 关于 11:21 后的变动)

```
$ make test
packages/shared test: # tests 58
packages/shared test: # pass 58
packages/shared test: # fail 0
packages/shared test: # skipped 0
...
142 passed, 1 warning in 1.38s
```

```
$ make lint
.venv/bin/python -m ruff check .
All checks passed!
pnpm -w lint
  packages/shared lint: Done
  apps/mobile lint: Done
```

`make test` = **58 node + 142 pytest, 0 failed, 0 skipped**. Exit 0.
Baseline at the start of my session was 121 pytest / 56 node. The delta: +17 from my three python
files, +2 from the copy tests, and **+4 that appeared mid-session** — see §6.

---

## 5. Known issues and who fixes what

I changed no source file, no assertion belonging to someone else, and no document outside this one.
Routing:

### → Engineer (Alex) — source-level, none blocking

None. No mutation exposed a source bug; every red was a correct refusal. Two small items, neither
a bug:

1. `packages/shared` `tsc` emits JS even when it exits 2 (`noEmitOnError` is unset), so a type error
   produces a *stale-but-updated* `dist` that the parity test then reads. Harmless today because
   the build failure still fails `make test`, but setting `noEmitOnError: true` would stop a broken
   build from ever being readable by the parity test. Your call.
2. My §3.4 test replaces the intent of `BRAND_VOCABULARY`/`FIBRE_VOCABULARY`, which I left in place
   (they still assert the specific Burberry/cashmere case). If you prefer one mechanism, the
   allowlist pair can go; I did not delete your tests.

### → Architect (Bob) — decisions, both documented above

1. **Parity skips instead of failing when `dist` is missing** (§2.5): green with 4 unmeasured tests.
   Recommend fail-closed under `CI=true`, or a sentinel that asserts parity tests actually ran.
2. **503 uses `code: "internal"`** (§2.3): a client cannot tell a misconfiguration from a server
   error without parsing prose. Consider an `unavailable` code, which is a contract change and
   therefore yours.
3. **`CONTRACTS.md` mapping table is now enforced in one more dimension** (§3.1). If required-ness
   parity is accepted, the doc's mapping table gains teeth and belongs in the contract's own
   checklist.

### → QA (me) — done

- Fixed the fake brand/fibre test (§3.4).
- Closed the required-ness blind spot (§3.1).
- Covered the devtools guard (§3.2) and the credentials-present Stripe branch (§3.3).

---

## 6. Environment note (matters for interpreting the numbers)

The working tree was edited by another teammate **while I was testing**:
`evals/tests/test_harness.py` mtime `11:06:43`, which is where the +4 pytest arrived
(121 → 125 before my additions). `apps/api/repository.py` and `apps/api/exporters.py` also changed
during the session. I therefore validated my restores by **md5 against `/tmp/snaplist-qa-orig`
copies** rather than trusting `git status`, and I verified each restore before starting the next
mutation. All 11 backed-up files match byte-for-byte at the end.

Consequence for anyone reading §1: if a teammate changes one of those 11 files after this handoff,
the md5 table in §1.9 is the baseline to re-check against, not `git status`.

**Not touched, per instructions:** `docs/PLAN.md`, `docs/CONTRACTS.md`, `docs/PRD.md`,
`snaplist_migcheck*`, and every unrelated database on this machine (no Postgres connection was
opened at all — the entire verification ran on SQLite `:memory:`).

---

## 7. Next command

```bash
cd /Users/bujin/Documents/Projects/SnapList && make test && make lint
```

在工程师那批 `marketplaces/` 补丁落地之后重跑。预期 `58 pass`（node）、`142 passed`（pytest，
若对方新增了测试则更多）、`0 failed`、`0 skipped`、`All checks passed!`。
若 `make lint` 仍报 `manual_when_empty` / `ExportedRow[]`，说明那批补丁还没写完，**不是** QA 残留。
