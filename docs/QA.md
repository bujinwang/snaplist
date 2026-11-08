# SnapList — QA.md

**Ticket E0-T5 — telemetry gate. Status: shipped at `73a95ba` (`HEAD` = `origin/master`).
This document is the honesty ledger for `python -m evals.telemetry`.**

This document records what was measured, what was merely predicted, what is deliberately
unmeasured, and which conclusions may not be carried over to production. A number that appears in
a report but has no sample behind it is not evidence; a report that prints `UNMEASURED` with a
reason is.

**Follow-up closure (this commit).** E0-T5 shipped under an allowed-paths whitelist that forbade
touching `Makefile`, `pyproject.toml`, and `evals/telemetry/tests/test_makefile_wiring.py`, so
two residuals escaped: **R4** (the `make evals` / `make evals-strict` targets still ran the
vision harness, not the gate) and **R7** (`pyproject.toml` `testpaths` did not collect the
telemetry suite, so a default `pytest` ran 0 telemetry cases). Both are now **CLOSED** by a
follow-up ticket that edited exactly those three files plus this document. §10 records the
closure; §9 and §11 carry the re-measured numbers. The 18-file E0-T5 inventory (§12) is unchanged
except for the md5 of `test_makefile_wiring.py` (its `xfail` tripwire is gone, replaced by a real
wiring assertion).

Baseline for the original ticket: `751fa5f`. The working tree also carries the untracked `.kilo/`
(untouched); the 6.5 GB SDXL weights were not touched.

---

## 1. Scope and the allowed-paths red line

Bert ruling 2 is absolute. This ticket delivers **exactly 18 files**:

- 8 modules: `evals/telemetry/{__init__,__main__,spec,model,compute,source,report,gate}.py`
- 9 test files: `evals/telemetry/tests/{fixtures,test_spec_literals,test_compute,test_source,
  test_report,test_exit_codes,test_mutations,test_population_and_orphans,
  test_makefile_wiring}.py`
- 1 document: `docs/QA.md`

**NOT touched (by E0-T5 itself)**: `docs/PLAN.md`, `Makefile`, `evals/run.py`, `pyproject.toml`,
`apps/api/repository.py`, `apps/api/usage.py`, `docs/CONTRACTS.md`, `infra/migrations/**`,
any schema, any write path, any emitter, `.kilo/`. No new dependency: the gate is pure Python
standard library. No file is written at runtime — the report is stdout-only and the database is
opened `mode=ro`.

A follow-up ticket (the R7+R4 closure, committed after `73a95ba`) edited `Makefile`,
`pyproject.toml`, `evals/telemetry/tests/test_makefile_wiring.py`, and this document. Those four
edits are outside the original 18-file scope by design and are recorded in §10; they add no new
files and no new runtime behaviour — they only wire the existing gate into `make` and into the
default `pytest` collection.

The delivered gate is `python -m evals.telemetry`. The `make` targets `evals`, `evals-strict`,
and `evals-report` are now wired to it (§11): `make evals` is the dev/CI gate
(`--gate --allow-inconclusive`), `make evals-strict` the release gate (`--gate`), `make
evals-report` report-only. The vision harness survives as `make evals-vision`. R4 (the wiring
gap) is **CLOSED** (§10); the `xfail(strict=True)` tripwire that guarded it is removed and
replaced by a real subprocess assertion in `test_makefile_wiring.py`
(`test_make_evals_strict_is_wired_to_the_telemetry_gate`), which resolves `make -n evals-strict`
to a recipe containing `evals.telemetry` (not `evals.run`) and runs the gate on a 30-item
baseline to exit 0.

## 2. Running it, and the four exit codes

```
python -m evals.telemetry --since 7d                          # report mode, always exit 0
python -m evals.telemetry --since 7d --gate                   # release gate, exit 0/1/2/3
python -m evals.telemetry --since 7d --gate --allow-inconclusive   # pre-launch CI
python -m evals.telemetry --since 7d --gate --db <path> --now <ISO-8601 UTC Z>
python -m pytest evals/telemetry/tests -q                     # explicit acceptance command
```

`--now` exists so tests pin the half-open window without touching the clock; it is not a
production knob. `--since` accepts only `7d`; anything else prints `UNMEASURED` to stderr and
exits 3.

| exit | meaning | `--allow-inconclusive` |
|---|---|---|
| 0 | all eight gated rows evaluated and under threshold | unchanged |
| 1 | FAIL — a threshold breached on sufficient sample (`n >= 30`) | **never** downgraded |
| 2 | INCONCLUSIVE — insufficient sample for at least one row | downgraded to 0, with the reason still printed |
| 3a | DATA ERROR — source absent (no file / no `events` table) | downgraded to 0, with the banner still printed |
| 3b | DATA ERROR — source unusable (unparseable rows, or required property missing from > 20 % of qualifying rows) | **never** downgraded |

Precedence `3 > 1 > 2 > 0` is a **priority, not a numeric ordering**. `spec.EXIT_RANK` maps
`3→30, 1→20, 2→10, 0→0`; `max()` over the integer codes would let an inconclusive (2) outrank
a real regression (1). `gate.aggregate_exit()` computes **every** contribution before ranking
(no short-circuit), downgrades per contribution (only labels `INCONCLUSIVE` and
`SOURCE_ABSENT_3A`) and then picks the top rank. Report-only rows are not in `report.metrics`
and therefore never contribute. A downgraded 3a can never hide a FAIL.

Every gated row prints three fields — `value | threshold | verdict` — plus a cross-unit
`n=30 items (from 3 sellers)` count. When `n` is insufficient the value column is the literal
`-`, never a number computed from the sample we just called too small. ANSI colour appears only
when stdout is a TTY (and `NO_COLOR` is unset); the `VERDICTS:` machine-readable summary line is
always plain text. The banner `GATE NOT EVALUATED — n=0. This is NOT a pass.` appears if and only
if **every** gated row has `n == 0`.

## 3. Layering and the single judgement

`compute.py` is the functional core: zero I/O, `now` injected. `source.py` is the only module
that opens a database, via `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`. It must **never**
construct `apps.api.repository.SqliteRepository`: that constructor runs `executescript(_SCHEMA)`
and migrations, which would turn a genuinely absent source (3a) into an empty-but-present table
(`n = 0`). `gate.py` is the only module that knows exit codes. `__main__.py` does argparse,
prints and raises `SystemExit`.

There is exactly **one** exclusion threshold: `spec.EXCLUDED_RATE_MAX = 0.20`. Both
`verdict_for()` and `source_state()` read it.

- `verdict_for()` decides exactly one thing **per metric**: `excluded_rate > 0.20` (strict `>`).
  `completeness = 1 - excluded_rate` is printed and never compared; there is deliberately no
  `COMPLETENESS_MIN` constant in `spec.py`, and `test_spec_literals.py` asserts its absence.
- `source_state()` is a **data-source quality classification**, not a second verdict. It maps
  no file / no `events` table → `SOURCE_ABSENT_3A`, unparseable rows → `SOURCE_UNUSABLE_3B`,
  and `attribution_missing_rate > 0.20` → `SOURCE_UNUSABLE_3B` (PLAN:250 exit-3 cause,
  "required property missing from > 20 % of qualifying rows"). The two rates have **different
  denominators**: `excluded_rate` divides by items entering a metric, `attribution_missing_rate`
  divides by refund ledger rows. The PLAN:148-150 prohibition is against comparing the same
  threshold twice in mirrored form; it does not remove PLAN:250's own source classification.
  **The guard is retained**: without it, a silently failed refund attribution leaves the refunded
  items in the sample, `excluded_rate` stays 0, the verdict layer never fires, and the gate goes
  green. M9a asserts `--gate`=3 with and without `--allow-inconclusive`.

## 4. Refund attribution — what is actually measurable

`apps/api/usage.py::refund_one()` does not pass `item_id`, so **`job_refunded.item_id` is always
NULL** in production. Reading that column directly excludes zero items and prints a confident
`excluded_refunded=0`. The gate therefore uses the ledger self-join, expressed as a LEFT JOIN so
that unattributable refund rows still appear and can be counted:

```sql
-- normative form (addendum 1, A)
select distinct e.item_id
  from usage_events r
  join usage_events e on e.job_id = r.job_id and e.event = 'item_enqueued'
 where r.event = 'job_refunded' and e.item_id is not null;
```

`source.py` selects **only** the two join keys (`r.job_id`, `e.item_id`); `job_refunded.item_id`
is never read. Shape validation applies only to those selected keys, never blanket over
`usage_events`. `EventRow.item_id` is `str | None`, not a UUID object; `field_names` is JSON text
and no gate uses it, so it is neither read nor parsed. The partial unique indexes in
`apps/api/repository.py` guarantee at most one `item_enqueued` row per `job_id`.

Classification (Q2, generic wording because PG `SET NULL` is a **real** producer):

- **NULL join key = evidence absence** → counted in `attribution_missing_rate` →
  `AttributionStatus.DEGRADED`, never 3b. "Absence of a feature is not corruption of it"
  (PLAN:277-279).
- **Non-NULL but malformed id = unparseable** → `SourceState.SOURCE_UNUSABLE_3B` (and
  `AttributionStatus.UNVERIFIED`). No `lower()` and no `UUID()` coercion: two distinct items must
  never be merged into one.

`excluded_refunded` and the failure mix count **distinct `item_id`s**, not rows (addendum 1, C).
One item with three refund jobs is one exclusion; counting rows would push `excluded_rate` past
0.20 and manufacture a fake 3b. M7 pins this. `failure_mix` likewise counts one item per reason
code.

`refund_rows_total == 0` → `INTACT`, `missing_rate = 0.0`, no division, and the report prints
`excluded_refunded=0 (n=0 refund rows)` with its sample count. When attribution is DEGRADED a
bare `excluded_refunded=0` is **never** printed.

**Four disclosures** whenever `0 < rate <= 0.20` (Q3, M9b):

1. report header: `refund_attribution=degraded(n=k)`
2. exclusion line: `excluded_refunded=UNMEASURED (n=k refund rows unattributable)`
3. machine summary: `REFUND_ATTRIBUTION=DEGRADED(n=k)` on the `VERDICTS:` line
4. this document's named residual, **R5** (§10)

`source_state()` stays `OK`, the exit code comes from the eight gated rows (∈ {0,1,2}) and is
**never** forced to 2 — forcing 2 would let `--allow-inconclusive` erase the degradation. The
report may legitimately be green; the four disclosures are what stop that green from being a lie.

Bert ruling 1 (ADR-008 Corrigendum): a **soft-deleted item still owns its events** — the
historical rate must not shrink. Exclusions use only positive evidence that survives a hard
delete (a surviving `jobs` row with a contract reason code, or a refund item recovered from the
ledger). A hard-deleted orphan is **not** excluded; it is reported as `orphan_item_ids=<n>`.
PLAN(d)'s "deleted" is read as "refunded / hard-deleted and refunded", **never** as "no row in
`items`". `failure_mix_unattributable=<n>` counts items that have events but no surviving jobs
row (addendum 1, D); it is an honest residual of no-FK + cascade, not data corruption.

## 5. Backend boundary — MEASURED vs UNVERIFIED

Report header pins `source=sqlite:<path>`, `backend=local-dev`,
`refund_attribution=sqlite-only`, `percentile=nearest-rank`, plus population, half-open window
and git SHA.

> **`excluded_refunded` 只在 SQLite dev DB 上是 MEASURED（该库 `usage_events` 无 FK）；在
> Postgres/Supabase 上按 `0001_init.sql:231-232` 为 UNMEASURED by construction；Postgres reader
> 是 residual，E0-T5 不承诺生产数字。**

> **范围声明**：8 个 latency/workload gate 与 activation 主样本来自 `events`，
> `events.item_id` 两侧都无 FK、硬删后保留 → **主样本 backend-independent**；脆弱的只有
> `excluded_refunded` / failure mix 归因。

Filtering and sorting use `received_at` (fixed-width ISO-8601 UTC Z with microseconds) only,
**never** `client_ts`; the window is half-open `[now-7d, now)`. Percentiles are hand-written
nearest-rank (`ceil(p*n)-1`); `statistics.quantiles` and `numpy.percentile` are not used.

### 5.1 SQLite — MEASURED (BEFORE/AFTER transcript, verbatim)

The fixture recreates the real FK asymmetry (addendum 1, B): `jobs.item_id` is
`references items(id) on delete cascade` with `pragma foreign_keys = on`, while `usage_events`
and `events.item_id` have **no FK**. 1 item / 3 failed jobs / 3 enqueued / 3 refunded / 4 funnel
events.

```
pragma foreign_keys = 0
pragma foreign_key_list(usage_events) = []
pragma foreign_key_list(events) = []
pragma foreign_key_list(jobs) = [(0, 0, 'items', 'item_id', 'id', 'NO ACTION', 'CASCADE', 'NONE')]
BEFORE items/jobs/usage_events/events = (1, 3, 6, 4)
BEFORE ledger self-join distinct item_id = ['00000001-0000-4000-8000-000000000000']
delete from items -> rows deleted = 1
AFTER items/jobs/usage_events/events = (0, 0, 6, 4)
AFTER ledger self-join distinct item_id = ['00000001-0000-4000-8000-000000000000']
AFTER surviving events.item_id = ['00000001-0000-4000-8000-000000000000']
```

Hard delete therefore removes `items` 1→0 and `jobs` 3→0 (all `reason_code`s lost), leaves
`usage_events` 6→6 and `events` 4→4 with their original ids, and the ledger self-join still
returns one item. `test_source.py::test_hard_delete_leaves_events_and_ledger_surviving` and
`test_mutations.py::test_m8_hard_deleted_orphan_is_reported_not_excluded` pin this behaviour.

### 5.2 Postgres — MEASURED, on one exact configuration only

**MEASURED — vanilla PostgreSQL 14.18 (Homebrew) + `verify/local_supabase_shim.sql` +
superuser role (`bujin`, bypasses RLS)**, temporary database `lead_pg_attribution_probe`.
`psql -v ON_ERROR_STOP=1`, shim → `0001_init.sql` → `0002_events.sql` →
`0003_events_item_id.sql`, all rc=0. FK introspection (`confdeltype`):

```
     tbl      |          conname          | confdeltype
--------------+---------------------------+-------------
 jobs         | jobs_item_id_fkey         | c     (CASCADE)
 usage_events | usage_events_item_id_fkey | n     (SET NULL)
 usage_events | usage_events_job_id_fkey  | n     (SET NULL)
```

BEFORE (`BEFORE_RC=0`): 1 item, 1 job, 2 ledger rows (`item_enqueued` + `job_refunded`), both
carrying both join keys; ledger self-join returns 1 row
(`11111111-1111-4111-8111-111111111111`).

AFTER `delete from public.items where id = '1111…'` → `DELETE 1` (`AFTER_RC=0`):

```
    step     |     event     | job_id | item_id
-------------+---------------+--------+---------
 ledger rows | item_enqueued |        |
 ledger rows | job_refunded  |        |
(2 rows)

  step  | items | jobs | ledger | ledger_job_id_null | ledger_item_id_null
--------+-------+------+--------+--------------------+---------------------
 counts |     0 |    0 |      2 |                  2 |                   2
(1 row)

-- same ledger self-join AFTER hard delete --
 item_id
---------
(0 rows)

-- naive (broken) exclusion: refund rows with a non-null item_id --
 naive_excluded_refunded
-------------------------
                       0
(1 row)

-- refund rows total / attributable --
 refund_rows_total | refund_rows_with_item_id
-------------------+--------------------------
                 1 |                        0
(1 row)
```

So on this configuration: `jobs` 1→0 (CASCADE), `usage_events` survives with **both** join keys
NULL (2/2 each), ledger self-join 1→0 rows, `naive_excluded_refunded=0`,
`refund_rows_total=1 / refund_rows_with_item_id=0`. This is precisely the shape in which a
`excluded_refunded=0` would be printed while nothing is excluded — the reason the DEGRADED
disclosure and the `source_state()` guard exist. Temporary DB dropped:
`dropdb lead_pg_attribution_probe` → `DROPPED`;
`select count(*) from pg_database where datname='lead_pg_attribution_probe'` = **0**.

One fixture caveat, stated so the evidence is not over-read: the probe gave the `job_refunded`
row an `item_id` too, so the `SET NULL` action is directly observable. Production
`refund_one()` leaves it NULL. The self-join does **not** read `r.item_id`, so this difference
cannot change the attribution result; the AFTER zero rows come from `job_id` becoming NULL and
`e.item_id is not null`, which is the production shape.

> **证据边界（QA.md 必须原样写死）**：`PostgresEventSource` 的**读取路径**（attribution /
> failure mix / orphan 分类，含 `usage_events` SET NULL 经 ledger self-join 的 DEGRADED 判定）已在
> vanilla PG14.18 + shim + superuser 上由 `evals/telemetry/tests/test_pg_integration.py` MEASURED；
> **真实 RLS 角色（`service_role` / `authenticated`）下的行可见性**仍 UNVERIFIED（superuser 绕过
> RLS）；Supabase 托管 PG、PG15、PostgREST 一律 UNVERIFIED；不得外推到生产数字。

**UNVERIFIED — Supabase managed PG, PostgreSQL 15, real RLS role policies (row visibility under the
`service_role`/`authenticated` roles), PostgREST, and any production number derived from
`usage_events` attribution.** A read-only `PostgresEventSource` IS shipped by E0-T5 (R3, see
ADR-009) and its read path is MEASURED on vanilla PG14.18 + shim + superuser; the honest gap that
remains is RLS row-visibility, which the local superuser run does not exercise.

**Do not cite the 0003 hard-delete probe as evidence for `usage_events` SET NULL.** That earlier
probe inserted only `items`/`events` and read back only `events.item_id`; it says nothing about
the ledger. The evidence above is the separate `lead_pg_attribution_probe` run.

M9 is deliberately **not** named a PG behaviour test. It only asserts that
「报表在归因退化时不假绿」 — the report does not go green when attribution is degraded.

## 6. Reconciliation with frozen documents (no wording changed)

- **PLAN:124 spec bug.** The `tool_time_ms` pseudocode takes its third term from the
  `export_delivered` row's `export_latency_ms`. `apps/api/repository.py` reverse CHECK
  (`export_latency_ms is null or event = 'export_clicked'`) makes that physically impossible:
  `export_delivered` cannot carry the column. The gate takes the third term from
  `export_clicked.export_latency_ms`. PLAN's wording is left untouched; this reconciliation
  lives here and is pinned behaviourally by
  `test_population_and_orphans.py::test_tool_time_uses_click_latency_not_delivery_latency`
  (click = 1111 ms, delivery = 9999 ms → tool time 3111 ms, not 11999 ms). Delivery is **not**
  a prerequisite for tool time.
- **PLAN:117-136 normative, §12.2(c) single-event draft void.** Tool time SUMS
  `prettify_latency_ms` across **all** `prettify_approved` retries; `attrs_latency_ms` comes
  from the approve attempt; `prettify_latency_ms` as a point attribute (the `prettify_p50/p90`
  gates) is the explicit one-row-per-item exception and takes the latest attempt.
  `test_tool_time_sums_retries_but_point_attribute_takes_latest` pins both.
- **§12.2(g) `export_clicked` draft void.** `activation_7d` counts distinct
  `export_delivered` item ids ≥ 3 per seller, divided by sellers with ≥ 1 delivery in the same
  window; `n` counts **sellers** (min 20); reported, never gated.
- **`export_burst_60m`** has no producer in `CONTRACTS.md` §7 → prints `UNMEASURED` with the
  reason, never a fabricated 0.
- **seller identity** is the uuid **suffix** of `subject` (ADR-003 promotes `anon_<uuid>` to
  `usr_<uuid>` without changing the identity).
- `completeness = 1 - excluded_rate` is printed and never judged; `fields_p50`,
  `activation_7d`, `export_burst_60m` and `completeness` are the four report-only rows.

## 7. Data mutations M0–M9 (M9 split a/b)

Every block below is the **verbatim stdout of a real `python -m evals.telemetry` subprocess**
with `--now 2026-11-05T00:00:00.000000Z` and `NO_COLOR=1`, against a freshly built SQLite file.
Correctness here is proven by database behaviour and process exit codes, never by asserting SQL
or Makefile text. Exit-code summary:

| mutation | what changed | `--gate` | `--gate --allow-inconclusive` |
|---|---|---|---|
| M0 | 30 complete items / 3 sellers | 0 | 0 |
| M1 | `prettify_latency_ms = 25000` (threshold breach) | 1 | 1 |
| M2 | only 5 items (n < 30) | 2 | 0 |
| M3 | no `events` table (3a) | 3 | **0** |
| M4 | malformed non-NULL `item_id` (3b) | 3 | **3** |
| M5 | 10/30 items without `export_clicked` → `excluded_rate = 0.33` | 3 | 3 |
| M6 | 10 items soft-deleted | 0 | 0 |
| M7 | 1 item with 3 refund jobs → distinct(item_id) | 0 | 0 |
| M8 | hard delete (jobs cascade, events + ledger survive) | 0 | 0 |
| M9a | all refund join keys NULL | 3 | **3** |
| M9b | 1 of 10 refund rows unattributable (`rate = 0.10`) | 0 | 0 |

M3 and M4 have their **own** `--allow-inconclusive` tests (0 and 3 respectively) in both
`test_exit_codes.py` and `test_mutations.py`. M7 pins the ledger self-join + distinct count.
**M9b is the only assertion with teeth against "green while degraded"**: exit 0 is legitimate,
but the output must contain `UNMEASURED`, `degraded`, `VERDICTS:` and
`REFUND_ATTRIBUTION=DEGRADED(n=1)`, and must not contain a bare `excluded_refunded=0`.

```
----- BEGIN M0 -----
$ python -m evals.telemetry --db <M0> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=0
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M0.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M0 -----
----- BEGIN M1 -----
$ python -m evals.telemetry --db <M1> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=1
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M1.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50        30  25000 ms       <= 20000 ms    FAIL  n=30 items (from 3 sellers)
prettify_p90        30  25000 ms       <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  27000 ms       <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  27000 ms       <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=7 FAIL=1 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M1 -----
----- BEGIN M1_allow -----
$ python -m evals.telemetry --db <M1_allow> --now 2026-11-05T00:00:00.000000Z --since 7d --gate --allow-inconclusive
exit=1
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M1.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50        30  25000 ms       <= 20000 ms    FAIL  n=30 items (from 3 sellers)
prettify_p90        30  25000 ms       <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  27000 ms       <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  27000 ms       <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=7 FAIL=1 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M1_allow -----
----- BEGIN M2 -----
$ python -m evals.telemetry --db <M2> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=2
SnapList telemetry gate — E0-T5
population=5 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M2.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50         5  -              <= 20000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
prettify_p90         5  -              <= 35000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
tooltime_p50         5  -              <= 35000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
tooltime_p90         5  -              <= 60000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
attrs_p90            5  -              <= 8000 ms     UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
export_p90           5  -              <= 5000 ms     UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
fields_mean          5  -              <= 5           UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
fields_p90           5  -              <= 12          UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M2 -----
----- BEGIN M2_allow -----
$ python -m evals.telemetry --db <M2_allow> --now 2026-11-05T00:00:00.000000Z --since 7d --gate --allow-inconclusive
exit=0
SnapList telemetry gate — E0-T5
population=5 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M2.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50         5  -              <= 20000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
prettify_p90         5  -              <= 35000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
tooltime_p50         5  -              <= 35000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
tooltime_p90         5  -              <= 60000 ms    UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
attrs_p90            5  -              <= 8000 ms     UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
export_p90           5  -              <= 5000 ms     UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
fields_mean          5  -              <= 5           UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)
fields_p90           5  -              <= 12          UNMEASURED (n=5 < 30)  n=5 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M2_allow -----
----- BEGIN M3 -----
$ python -m evals.telemetry --db <M3> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=3
SnapList telemetry gate — E0-T5
population=0 items (from 0 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M3.db
backend=local-dev
refund_attribution=sqlite-only
source_state=SOURCE_ABSENT_3A

GATE NOT EVALUATED — n=0. This is NOT a pass.

gate                 n  value          threshold      verdict
prettify_p50         0  -              <= 20000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
prettify_p90         0  -              <= 35000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
tooltime_p50         0  -              <= 35000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
tooltime_p90         0  -              <= 60000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
attrs_p90            0  -              <= 8000 ms     UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
export_p90           0  -              <= 5000 ms     UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
fields_mean          0  -              <= 5           UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
fields_p90           0  -              <= 12          UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)

fields_p50=- (report only)
activation_7d=UNMEASURED (n=0 sellers < 20), n=0 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=- (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M3 -----
----- BEGIN M3_allow -----
$ python -m evals.telemetry --db <M3_allow> --now 2026-11-05T00:00:00.000000Z --since 7d --gate --allow-inconclusive
exit=0
SnapList telemetry gate — E0-T5
population=0 items (from 0 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M3.db
backend=local-dev
refund_attribution=sqlite-only
source_state=SOURCE_ABSENT_3A

GATE NOT EVALUATED — n=0. This is NOT a pass.

gate                 n  value          threshold      verdict
prettify_p50         0  -              <= 20000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
prettify_p90         0  -              <= 35000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
tooltime_p50         0  -              <= 35000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
tooltime_p90         0  -              <= 60000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
attrs_p90            0  -              <= 8000 ms     UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
export_p90           0  -              <= 5000 ms     UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
fields_mean          0  -              <= 5           UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
fields_p90           0  -              <= 12          UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)

fields_p50=- (report only)
activation_7d=UNMEASURED (n=0 sellers < 20), n=0 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=- (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M3_allow -----
----- BEGIN M4 -----
$ python -m evals.telemetry --db <M4> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=3
SnapList telemetry gate — E0-T5
population=31 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M4.db
backend=local-dev
refund_attribution=sqlite-only
source_state=SOURCE_UNUSABLE_3B

gate                 n  value          threshold      verdict
prettify_p50        31  1000 ms        <= 20000 ms    PASS  n=31 items (from 3 sellers)
prettify_p90        31  1000 ms        <= 35000 ms    PASS  n=31 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           31  1000 ms        <= 8000 ms     PASS  n=31 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=0.9677 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=1
orphan_item_ids=1
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M4 -----
----- BEGIN M4_allow -----
$ python -m evals.telemetry --db <M4_allow> --now 2026-11-05T00:00:00.000000Z --since 7d --gate --allow-inconclusive
exit=3
SnapList telemetry gate — E0-T5
population=31 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M4.db
backend=local-dev
refund_attribution=sqlite-only
source_state=SOURCE_UNUSABLE_3B

gate                 n  value          threshold      verdict
prettify_p50        31  1000 ms        <= 20000 ms    PASS  n=31 items (from 3 sellers)
prettify_p90        31  1000 ms        <= 35000 ms    PASS  n=31 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           31  1000 ms        <= 8000 ms     PASS  n=31 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=0.9677 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=1
orphan_item_ids=1
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M4_allow -----
----- BEGIN M5 -----
$ python -m evals.telemetry --db <M5> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=3
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M5.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        20  -              <= 35000 ms    DATA_UNAVAILABLE (excluded_rate=0.33 > 0.20)  n=20 items (from 3 sellers)
tooltime_p90        20  -              <= 60000 ms    DATA_UNAVAILABLE (excluded_rate=0.33 > 0.20)  n=20 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          20  -              <= 5000 ms     DATA_UNAVAILABLE (excluded_rate=0.33 > 0.20)  n=20 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=0.6667 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=5 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=3 REFUND_ATTRIBUTION=INTACT
----- END M5 -----
----- BEGIN M6 -----
$ python -m evals.telemetry --db <M6> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=0
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M6.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M6 -----
----- BEGIN M7 -----
$ python -m evals.telemetry --db <M7> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=0
SnapList telemetry gate — E0-T5
population=31 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M7.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=0.9677 (mirror of excluded_rate; printed, never judged)
excluded_refunded=1 (n=3 refund rows, distinct item_id)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M7 -----
----- BEGIN M8 -----
$ python -m evals.telemetry --db <M8> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=0
SnapList telemetry gate — E0-T5
population=31 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M8.db
backend=local-dev
refund_attribution=sqlite-only

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=0.9677 (mirror of excluded_rate; printed, never judged)
excluded_refunded=1 (n=3 refund rows, distinct item_id)
failure_mix_unattributable=1
orphan_item_ids=1
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
----- END M8 -----
----- BEGIN M9a -----
$ python -m evals.telemetry --db <M9a> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=3
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M9a.db
backend=local-dev
refund_attribution=sqlite-only
refund_attribution=degraded(n=3)
source_state=SOURCE_UNUSABLE_3B

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=UNMEASURED (n=3 refund rows unattributable)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=DEGRADED(n=3)
----- END M9a -----
----- BEGIN M9a_allow -----
$ python -m evals.telemetry --db <M9a_allow> --now 2026-11-05T00:00:00.000000Z --since 7d --gate --allow-inconclusive
exit=3
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M9a.db
backend=local-dev
refund_attribution=sqlite-only
refund_attribution=degraded(n=3)
source_state=SOURCE_UNUSABLE_3B

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=UNMEASURED (n=3 refund rows unattributable)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=DEGRADED(n=3)
----- END M9a_allow -----
----- BEGIN M9b -----
$ python -m evals.telemetry --db <M9b> --now 2026-11-05T00:00:00.000000Z --since 7d --gate
exit=0
SnapList telemetry gate — E0-T5
population=30 items (from 3 sellers)
window=[2026-10-29T00:00:00.000000Z, 2026-11-05T00:00:00.000000Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=751fa5f
source=sqlite:/var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-qa-38s1xu9x/M9b.db
backend=local-dev
refund_attribution=sqlite-only
refund_attribution=degraded(n=1)

gate                 n  value          threshold      verdict
prettify_p50        30  1000 ms        <= 20000 ms    PASS  n=30 items (from 3 sellers)
prettify_p90        30  1000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p50        30  3000 ms        <= 35000 ms    PASS  n=30 items (from 3 sellers)
tooltime_p90        30  3000 ms        <= 60000 ms    PASS  n=30 items (from 3 sellers)
attrs_p90           30  1000 ms        <= 8000 ms     PASS  n=30 items (from 3 sellers)
export_p90          30  1000 ms        <= 5000 ms     PASS  n=30 items (from 3 sellers)
fields_mean         30  3              <= 5           PASS  n=30 items (from 3 sellers)
fields_p90          30  3              <= 12          PASS  n=30 items (from 3 sellers)

fields_p50=3 (report only)
activation_7d=UNMEASURED (n=3 sellers < 20), n=3 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=1.0000 (mirror of excluded_rate; printed, never judged)
excluded_refunded=UNMEASURED (n=1 refund rows unattributable)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=DEGRADED(n=1)
----- END M9b -----
```

## 8. Code mutations — 8 operator flips, all in `/tmp` copies

**The real repository was never mutated.** A pristine copy of `evals/` + `pyproject.toml` was
made under `/tmp/sl-mutations-*`; each flip was applied to the copy, a targeted set of tests was
run there, and the file was then restored from the pristine bytes. `pristine_md5` equals
`restored_md5` in all 8 cases, and the real repo's `compute.py` / `spec.py` md5 are identical
before and after.

| # | operator flip | real-repo file | went red | a test that went red |
|---|---|---|---|---|
| 1 | `rate > 0.20` → `>=` | `compute.py` | yes | `test_verdict_for_uses_one_strict_judgement_in_one_direction` |
| 2 | `ceil(p*n)-1` → `ceil(p*n)` | `compute.py` | yes | `test_nearest_rank_matches_the_pinned_examples` |
| 3 | `< end` → `<= end` | `compute.py` | yes | `test_window_is_half_open_on_utc_received_at` |
| 4 | `EXIT_RANK` 1/2 swapped | `spec.py` | yes | `test_exit_codes_and_their_priority_rank_map` |
| 5 | `3b` added to downgrade whitelist | `spec.py` | yes | `test_only_inconclusive_and_3a_are_downgradable` |
| 6 | `value="-"` → computed value | `compute.py` | yes | `test_insufficient_sample_value_is_the_literal_dash` |
| 7 | retry SUM → last attempt only | `compute.py` | yes | `test_tool_time_sums_retries_but_point_attribute_takes_latest` |
| 8 | one-row-per-item → per-event | `compute.py` | yes | `test_build_timelines_is_one_row_per_item_not_per_event` |

The table above and the verbatim runner output below are two records of **two different test
selections** of the same eight mutations, which is why the first failure pytest reports differs
for five of the rows. Neither record is claimed to be a full-suite run; the column is named
`a test that went red` rather than `first failing test` precisely because which test is reported
first depends on the subset that was run. **Every test name appearing in either list exists in
this repository** — each was looked up individually:
`test_nearest_rank_matches_the_pinned_examples`,
`test_window_is_half_open_on_utc_received_at`,
`test_exit_codes_and_their_priority_rank_map`,
`test_only_inconclusive_and_3a_are_downgradable`,
`test_build_timelines_is_one_row_per_item_not_per_event` in `test_compute.py` /
`test_spec_literals.py`; `test_nearest_rank_percentile_is_hand_written`,
`test_half_open_window_uses_received_at_never_client_ts`,
`test_tool_time_sums_retries_but_point_attribute_takes_latest` in
`test_population_and_orphans.py`; `test_fail_outranks_inconclusive_by_rank_not_numeric_value`
in `test_exit_codes.py`; `test_m4_3b_is_three_and_never_downgrades` in `test_mutations.py`.
The conclusion is unaffected: **8/8 flips went red, 8/8 md5 were restored, and the real
repository was never modified.**

Verbatim runner output (md5, pytest rc, red confirmation, final proof):

```
WORK = /var/folders/rp/p3w9v7l94pncr567w4sb9nhm0000gn/T/sl-mutations-cr8xhmgf
real_repo_md5_before = {'evals/telemetry/spec.py': '173623ee357563672ee6edffcf27990d', 'evals/telemetry/compute.py': '50bbe07225c74c40a68d0da31f97b125'}
1. `>` -> `>=` on excluded_rate
  file=evals/telemetry/compute.py
  pristine_md5=50bbe07225c74c40a68d0da31f97b125  mutated_md5=37e947f745e063bcfd5510cf1b3d8f61  restored_md5=50bbe07225c74c40a68d0da31f97b125
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_compute.py::test_verdict_for_uses_one_strict_judgement_in_one_direction
2. `ceil(p*n)-1` -> `ceil(p*n)`
  file=evals/telemetry/compute.py
  pristine_md5=50bbe07225c74c40a68d0da31f97b125  mutated_md5=f45b0d5bcfec667f10c1df57ebcbd617  restored_md5=50bbe07225c74c40a68d0da31f97b125
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_population_and_orphans.py::test_nearest_rank_percentile_is_hand_written
3. `< end` -> `<= end`
  file=evals/telemetry/compute.py
  pristine_md5=50bbe07225c74c40a68d0da31f97b125  mutated_md5=f7abe163e491b4bbd42b337dbc643292  restored_md5=50bbe07225c74c40a68d0da31f97b125
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_population_and_orphans.py::test_half_open_window_uses_received_at_never_client_ts
4. EXIT_RANK 1/2 swapped
  file=evals/telemetry/spec.py
  pristine_md5=173623ee357563672ee6edffcf27990d  mutated_md5=6f667483b6142510aa91d48502a530cd  restored_md5=173623ee357563672ee6edffcf27990d
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_exit_codes.py::test_fail_outranks_inconclusive_by_rank_not_numeric_value
5. 3b added to DOWNGRADABLE_STATES
  file=evals/telemetry/spec.py
  pristine_md5=173623ee357563672ee6edffcf27990d  mutated_md5=576c994faaa5069cce925f5ba6a0b63f  restored_md5=173623ee357563672ee6edffcf27990d
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_mutations.py::test_m4_3b_is_three_and_never_downgrades
6. `value="-"` -> computed value
  file=evals/telemetry/compute.py
  pristine_md5=50bbe07225c74c40a68d0da31f97b125  mutated_md5=c89387d414dbbe6133502e834578ff3c  restored_md5=50bbe07225c74c40a68d0da31f97b125
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_report.py::test_insufficient_sample_value_is_the_literal_dash
7. retry SUM -> last attempt only
  file=evals/telemetry/compute.py
  pristine_md5=50bbe07225c74c40a68d0da31f97b125  mutated_md5=ebc25b76a38e3cb9a04a6ce588172139  restored_md5=50bbe07225c74c40a68d0da31f97b125
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_population_and_orphans.py::test_tool_time_sums_retries_but_point_attribute_takes_latest
8. one-row-per-item -> per-event
  file=evals/telemetry/compute.py
  pristine_md5=50bbe07225c74c40a68d0da31f97b125  mutated_md5=9791e3bf78be830695eee05acbcb969e  restored_md5=50bbe07225c74c40a68d0da31f97b125
  pytest_rc=1  went_red=True  summary=FAILED evals/telemetry/tests/test_population_and_orphans.py::test_tool_time_sums_retries_but_point_attribute_takes_latest

=== FINAL ===
all_went_red = True
all_md5_restored = True
real_repo_md5_after  = {'evals/telemetry/spec.py': '173623ee357563672ee6edffcf27990d', 'evals/telemetry/compute.py': '50bbe07225c74c40a68d0da31f97b125'}
real_repo_untouched  = True
mutations = 8
```

## 9. Four gates — measured numbers

| gate | command | rc | measured |
|---|---|---|---|
| 1 | `python -m pytest evals/telemetry/tests -q` | **0** | `99 passed, 0 xfailed` (the R4 tripwire is gone — see §1/§10) |
| 1b | `python -m pytest -q` (whole repo, `testpaths`) | **0** | `692 tests, 0 failures, 5 skipped` — **the telemetry suite is now collected by default** (R7 closed) |
| 2 | `pnpm -w test` | **0** | `Scope: 3 of 4 workspace projects`, `76 tests / 76 pass / 0 fail` |
| 3 | `.venv/bin/ruff check .` | **0** | `All checks passed!` |
| 4 | `git diff --check` on the tracked edits + `git diff --no-index --check /dev/null` per new file | **0** | 0 bytes of output; the three tracked edits (`Makefile`, `pyproject.toml`, `test_makefile_wiring.py`) are whitespace-clean |

Gate 1 is now `99 passed, 0 xfailed`: the `xfail(strict=True)` R4 tripwire that the original
ticket had to leave in place (it could not touch `Makefile`) is removed, and
`test_makefile_wiring.py` carries a real subprocess assertion instead (§1). The whole-repo
`testpaths` in `pyproject.toml` **now includes** `evals/telemetry/tests`, so a default
`python -m pytest -q` collects the telemetry suite and gate 1b rises from the E5-T6b baseline of
`588 passed, 4 skipped, 1 xfailed` to `692 tests, 0 failures, 5 skipped` (the +99 are exactly the
telemetry cases, 0 of them xfailed). Row 1b is now genuine gate evidence, not just a regression
confirmation — the collection gap that was residual **R7** is closed. The `5 skipped` are
pre-existing tests gated on an optional dependency / network; the single pre-existing
starlette/anyio `DeprecationWarning` remains, unrelated to this ticket.

Gate 4 measures whitespace on the new files, not on tracked edits. All 18 deliverables are
untracked, so a bare `git diff --check` returning rc=0 is **no evidence at all** about them —
it only covers tracked modifications. `git add -N` is *intent-to-add*: it registers the paths in
the diff without staging any content, so `git diff --check` then sees the new files; that run
produced 0 bytes of output (rc=0). A separate cross-check,
`git diff --no-index --check /dev/null <file>`, was run over all 18 files and found 0 files with
whitespace issues. The index was reset immediately afterwards with
`git reset -q -- docs/QA.md evals/telemetry`: `git diff --cached --name-only` is empty and no
intent-to-add entries remain. Timing figures in the table are from the engineer's run and may
vary between runs.

Environmental note (not a code defect): pytest's built-in `tmp_path` fixture hits this
sandbox's `Path.mkdir(exist_ok=True)` shim with `EEXIST` when the shared basetemp already
exists. `fixtures.py` therefore supplies a shim-safe `tmp_path` built on
`tempfile.mkdtemp()`, so the **plain** acceptance command above passes without `--basetemp`.

## 10. Named residuals (deliberately NOT solved here)

- **R1 (#18) — `failure_mix_unattributable=<n>`.** Items with events but no surviving `jobs`
  row lose their `reason_code` by ADR-008's no-FK design plus `jobs` CASCADE. Adding a
  reason_code signal requires changing a write path or a migration — both outside the
  allowed-paths whitelist and in conflict with Bert ruling 2. This ticket invents no data and
  changes no schema; it reports the count as an honest residual. Follow-up ticket required. **CLOSED (E0-T5 R1–R3): accepted as a permanent, intentional honesty residual — no schema change, no write-path change. Documented in ADR-009.**
- **R2 (#19) — `0001_init.sql:231-232` `on delete set null` unchanged.** The two FK actions on
  `usage_events.item_id` / `job_id` are outside the whitelist, so E0-T5 does not touch them.
  `usage_events` is the quota ledger; `units` / `user_id` are what quota is derived from, so
  nulling the join keys may be deliberate for billing. Its consequence for analytics attribution
  is measured in §5.2. **Recommend a follow-up ADR** deciding whether analytics needs a
  separate, deletion-proof attribution key, before anyone quotes `excluded_refunded` on
  Postgres. **CLOSED (E0-T5 R1–R3): the SET NULL FK actions are retained by design; ADR-009 records the decision and the follow-up ADR pointer.**
- **R3 — Postgres reader.** **CLOSED (E0-T5 R1–R3):** a read-only `PostgresEventSource` now ships as an OPTIONAL dependency group (`pg`, `psycopg[binary]`, not installed by default `uv sync`). It is selected only when `SNAPLIST_PG_DSN` is set and reuses the SAME `_classify_attribution` as SQLite, so SET NULL NULLs are still DEGRADED (evidence absence) and `excluded_refunded` is now MEASURED on Postgres too. The read path is verified against real PG14.18 (superuser) by `evals/telemetry/tests/test_pg_integration.py` (skipped unless psycopg + psql + a local Postgres are present). `UNVERIFIED` stays reserved for the not-yet-measured-under-real-RLS case (row visibility under `service_role`/`authenticated`).
- **R4 — Makefile wiring. CLOSED (follow-up commit after `73a95ba`).** `Makefile` was outside
  the E0-T5 whitelist; the original ticket left `make evals` → `evals.run --suite vision
  --allow-inconclusive` and `make evals-strict` → `evals.run --suite vision --gate`, guarded by
  an `xfail(strict=True)` tripwire in `test_makefile_wiring.py`. The follow-up wired both targets
  to `python -m evals.telemetry` (`make evals` = `--gate --allow-inconclusive`; `make
  evals-strict` = `--gate`; plus `make evals-report` = report-only and `make evals-vision` =
  the preserved vision harness), deleted the tripwire, and added
  `test_make_evals_strict_is_wired_to_the_telemetry_gate`, which proves the wiring with a real
  subprocess (`make -n evals-strict` resolves to a recipe containing `evals.telemetry` and not
  `evals.run`, and the gate exits 0 on a pinned-`--now` 30-item baseline). **Known make
  limitation (documented in the Makefile): GNU Make 3.81 (macOS stock) collapses any non-zero
  recipe exit to `2`, so through this make the wrapper distinguishes only pass (0) vs fail (2);
  the precise FAIL(1)/INCONCLUSIVE(2)/DATA_UNAVAILABLE(3) codes survive only when the gate is
  invoked directly.** That granularity loss is a make limitation, not a gate defect, and is not
  hidden.
- **R5 — `0 < attribution_missing_rate <= 0.20` DEGRADED.** PLAN:250 sets the 20 % tolerance
  for a required property missing from qualifying rows; below that cut the source is classified
  `OK`, so the eight gated rows legitimately decide the exit code (∈ {0,1,2}). It is **never**
  mapped to exit 2, because `--allow-inconclusive` would then erase the degradation. Instead the
  four disclosures of §4 are mandatory. This is the "green with hidden degradation" case; M9b
  is its guard.
- **R6 — `export_burst_60m`.** No producer exists in `CONTRACTS.md` §7; the row prints
  `UNMEASURED` plus the reason. Defining it is a contract change, not a gate change.
- **R7 — `pyproject.toml` `testpaths`. CLOSED (follow-up commit after `73a95ba`).** The original
  ticket could not edit `pyproject.toml` under the allowed-paths whitelist, so the whole-repo
  `testpaths` excluded `evals/telemetry/tests` and a default `pytest` collected 0 telemetry
  cases. The follow-up added `"evals/telemetry/tests"` to `testpaths`; a default
  `python -m pytest -q` now collects the suite (gate 1b = `692 tests, 0 failures, 5 skipped`,
  up from the `588 passed, 4 skipped, 1 xfailed` E5-T6b baseline — the +99 are the telemetry
  cases, 0 xfailed). The explicit acceptance command `python -m pytest evals/telemetry/tests -q`
  still works and is now redundant with the default collection; both paths run the same 99
  tests. R4's old tripwire never covered `testpaths`; that gap is closed here, independently.

## 11. The `make` targets — now the E0-T5 gate (R4 closed)

Both `make evals` and `make evals-strict` now invoke `python -m evals.telemetry` (the vision
harness survives as `make evals-vision`). Output below was captured on this checkout
(`git_sha=73a95ba`) with `NO_COLOR=1` against a **non-existent** `SNAPLIST_DB` — the
`SOURCE_ABSENT_3A` / 3a case, i.e. nothing has been measured yet.

```
$ make evals                      # dev / CI gate: --gate --allow-inconclusive
rc=0
exec .venv/bin/python -m evals.telemetry --since 7d --gate --allow-inconclusive
SnapList telemetry gate — E0-T5
population=0 items (from 0 sellers)
source_state=SOURCE_ABSENT_3A

GATE NOT EVALUATED — n=0. This is NOT a pass.

VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
```

```
$ make evals-strict               # release gate: --gate (no --allow-inconclusive)
rc=2   (make 3.81 collapses the gate's real exit 3 to 2; it prints "make: *** [evals-strict] Error 3")
exec .venv/bin/python -m evals.telemetry --since 7d --gate
SnapList telemetry gate — E0-T5
population=0 items (from 0 sellers)
source_state=SOURCE_ABSENT_3A

GATE NOT EVALUATED — n=0. This is NOT a pass.

VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
```

```
$ make evals-report               # report only, never gates
rc=0
exec .venv/bin/python -m evals.telemetry --since 7d
... (same body; VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0) ...
```

On a real 30-item baseline (all 8 gated rows PASS) both `make evals` and `make evals-strict`
return `rc=0` with `VERDICTS: PASS=8 FAIL=0 INCONCLUSIVE=0 DATA_UNAVAILABLE=0`. The only
behavioural difference between the two is how an unmeasured / inconclusive pipeline is treated:
`make evals` downgrades 2 and 3a to 0 (dev must not be blocked by absence), `make evals-strict`
does not (a release cannot ship an unmeasured pipeline). **Exit-code granularity note:** GNU
Make 3.81 (macOS stock) collapses any non-zero recipe exit to `2`, so through `make` the
distinction FAIL(1)/INCONCLUSIVE(2)/DATA_UNAVAILABLE(3) is lost — run
`python -m evals.telemetry --since 7d --gate [--allow-inconclusive]` directly to get the exact
code. This is a make limitation, recorded here and in the Makefile, not a gate defect.

## 12. File inventory and frozen md5

| file | md5 |
|---|---|
| `evals/telemetry/__init__.py` | `5cfed77371c084e4d1772455d21b0781` |
| `evals/telemetry/__main__.py` | `5ce9981fb2571ee7243c4093e4a11802` |
| `evals/telemetry/compute.py` | `50bbe07225c74c40a68d0da31f97b125` |
| `evals/telemetry/gate.py` | `5aa2b04c75e8b4ac91cffc7c41c5d224` |
| `evals/telemetry/model.py` | `5892648fcc0806a281bc6d177673bb63` |
| `evals/telemetry/report.py` | `d5a927f522273cf268e484940eba76b8` |
| `evals/telemetry/source.py` | `5c42cdb002b588da86858d88e67b7601` |
| `evals/telemetry/spec.py` | `173623ee357563672ee6edffcf27990d` |
| `evals/telemetry/tests/fixtures.py` | `a4fdbaf1d63b5108369046810c9e9b23` |
| `evals/telemetry/tests/test_compute.py` | `ec575ff2e7f529777c6f14fd74f4b82b` |
| `evals/telemetry/tests/test_exit_codes.py` | `830b407029a399a7beedfd07507844e3` |
| `evals/telemetry/tests/test_makefile_wiring.py` | `3c17a1669ee46720f8648a4a0f1d9ec1` |
| `evals/telemetry/tests/test_mutations.py` | `bea7ab249fd204f8ff13fd790a08efd3` |
| `evals/telemetry/tests/test_population_and_orphans.py` | `8c0c996ff056bd7f99108baab339b016` |
| `evals/telemetry/tests/test_report.py` | `da2e349ce7cc454eea920b6faa5baa60` |
| `evals/telemetry/tests/test_source.py` | `4c2509de496d4b79f7940efbaee951ac` |
| `evals/telemetry/tests/test_spec_literals.py` | `28aba755ead0055ca94c8e8070581e27` |

`docs/QA.md`'s own md5 is reported in the handoff to team-lead (a document cannot freeze its
own hash). 17 Python files + this document = the 18 whitelisted files.

## 13. E0-T4 — regression gate and cost dashboard

**Status 2026-11-06: both tools exist and were exercised; every cost cell is `UNMEASURED` and
the `<= $0.03/item` verdict is UNREACHABLE, not passing.** No number in this section was
estimated, and no cell was filled by hand.

PLAN E0-T4 owns `infra/ci/gates/**` plus this document. It asked for two artefacts: a regression
gate over the metrics the eval harness actually measures, and a cost dashboard whose numbers are
**read out of `evals/reports/cost.md`** rather than typed into a table. Both exist, with 65
stdlib tests. What could not be delivered is a *number*: `evals/reports/cost.md` does not exist,
no stage has ever run against a paid provider, and there are no production `usage_events`. That
is the same blocker `packages/vision/BLOCKED.md` records for E4. E0-T5 did not fill this
section, does not borrow its numbers, and does not pre-judge its gate.

### 13.1 What is on disk

| file | what it does | exits |
|---|---|---|
| `infra/ci/gates/regression_gate.py` | compares `evals.run.measurements()` (imported in-process: no subprocess, no network, no provider call) against `baseline.json` | `0` compared and held · `1` regression · `2` inconclusive |
| `infra/ci/gates/cost_dashboard.py` | parses per-stage `$/item` + sample counts out of `evals/reports/cost.md`; renders `UNMEASURED` when there is nothing to parse | `0` rendered · `1` over budget (fully measured + `--fail-on-unmeasured`) · `2` not evaluable |
| `infra/ci/gates/baseline.json` | **deliberately empty** (`"metrics": {}`) — see §13.5 | — |
| `infra/ci/gates/README.md` | baseline schema + `cost.md` format contract | — |
| `infra/ci/gates/tests/` | 65 `unittest` cases, standard library only | — |

Four refusals are the substance of the ticket, not decoration:

* **The gate never exits 0 having compared nothing.** `compared == 0` is hard-wired to `2`, and
  `2` prints `GATE INCONCLUSIVE … this is NOT a pass`.
* **A hand-edited baseline cannot gate fewer metrics than it describes.** `sets` and
  `mask_sources` must enumerate exactly the same metric names as `metrics`; deleting a row from
  `metrics` alone is refused (`2`) instead of comparing one metric fewer and printing PASS. That
  was QA finding 1 on this ticket, and it is fixed fail-closed with no schema change.
* **A baseline is written on purpose or not at all.** `--update-baseline` stamps `git_sha`, the
  timestamp, and the set + mask sources each number came from; it refuses a non-finite value,
  refuses a metric whose own provenance says its references are machine-made (unless
  `--allow-machine-references`), and refuses to drop a metric the previous baseline had (unless
  `--allow-dropped-metrics`).
* **The dashboard cannot be told a number.** There is no default, no `--assume`, no fallback
  constant: every cell is either parsed out of the report or the literal `UNMEASURED` next to the
  reason it is not a number.

### 13.2 The exact commands, run on this checkout (`git_sha=1beee75`)

```
$ .venv/bin/python infra/ci/gates/regression_gate.py
SnapList regression gate — E0-T4
baseline: infra/ci/gates/baseline.json
          no baselined metric — nothing to compare

metric                                   baseline    current     delta     tol  verdict
---------------------------------------------------------------------------------------
prettify SSIM (inside mask)                     —     0.9936         —       —  NOT IN BASELINE
segmentation mean IoU                           —     0.9299         —       —  NOT IN BASELINE
---------------------------------------------------------------------------------------
  NOT IN BASELINE · prettify SSIM (inside mask): measured in this run, but the baseline has no row to compare against
  NOT IN BASELINE · segmentation mean IoU: measured in this run, but the baseline has no row to compare against
  harness · prettify hallucinated-text rubric: UNMEASURED — the manual 1-5 rubric is E2-T3; no human has scored anything yet, and a machine cannot stand in for it

GATE INCONCLUSIVE — exit 2: this is NOT a pass.
  reason: infra/ci/gates/baseline.json is an explicitly empty baseline ("metrics": {}) — nothing to compare
  the only two metrics measurable in this checkout (segmentation IoU on `ai:`/`algorithmic:` references, prettify SSIM on synthetic labels) are pipeline checks, not DoD numbers — see infra/ci/gates/README.md.
  create a baseline on purpose: python infra/ci/gates/regression_gate.py --update-baseline
rc=2
```

```
$ .venv/bin/python infra/ci/gates/cost_dashboard.py
SnapList cost dashboard — E0-T4
report: evals/reports/cost.md (ABSENT)
target: <= $0.03/item for E1+E2+E3

| stage | what it covers | $/item | samples | status |
|---|---|---|---|---|
| E1 | segmentation | UNMEASURED | — | UNMEASURED |
| E2 | rectify + prettify | UNMEASURED | — | UNMEASURED |
| E3 | attributes + dedupe | UNMEASURED | — | UNMEASURED |
| E4 | on-model render (per render, not per item) | UNMEASURED | — | UNMEASURED |
| **E1+E2+E3** | **cost per item** | UNMEASURED | — | UNMEASURED |

Why the UNMEASURED cells are UNMEASURED:
  E1, E2, E3, E4: evals/reports/cost.md does not exist — no stage has ever run against a paid provider (E4 on-model is a stub; FASHN_API_KEY and MODAL_TOKEN are absent, packages/vision/BLOCKED.md) and there are no production usage_events, so no $/item has ever been billed

E1+E2+E3 total: UNMEASURED
  E1+E2+E3 total is UNMEASURED — evals/reports/cost.md does not exist — no stage has ever run against a paid provider (E4 on-model is a stub; FASHN_API_KEY and MODAL_TOKEN are absent, packages/vision/BLOCKED.md) and there are no production usage_events, so no $/item has ever been billed. E1/E2/E3 run locally on onnxruntime + classical CV: that produces wall-clock seconds, not a billed $/item
verdict: UNMEASURED — the <= $0.03/item verdict is UNREACHABLE, not passing: no measured $/item exists for any stage

E4 is listed for context only: it is priced per render, is BLOCKED in this build (packages/vision/BLOCKED.md), and is not part of the per-item total.

Parse notes:
  - the file does not exist, so nothing could be parsed
rc=0
```

```
$ .venv/bin/python infra/ci/gates/cost_dashboard.py --fail-on-unmeasured
… (same table) …
COST BUDGET NOT EVALUATED — exit 2. This is NOT a pass: the <= $0.03/item verdict was never computed.
rc=2
```

```
$ .venv/bin/python -m unittest discover -s infra/ci/gates/tests -t infra/ci/gates/tests
.................................................................
----------------------------------------------------------------------
Ran 65 tests in 1.273s

OK
rc=0
```

**All three gate codes are reachable, measured on the real harness** (temp baseline under
`$TMPDIR`, real measurements, nothing written into the repo):

```
$ python infra/ci/gates/regression_gate.py --baseline $T/baseline.json --update-baseline
refusing to write a baseline: these metrics were measured against references that are not hand-made: segmentation mean IoU
BASELINE NOT WRITTEN — nothing was changed.
rc=2

$ python infra/ci/gates/regression_gate.py --baseline $T/baseline.json --update-baseline --allow-machine-references
wrote baseline: $T/baseline.json
  generated_at=2026-11-06T23:32:21+00:00 git_sha=1beee75e5df0 metrics=2
  prettify SSIM (inside mask): 0.9936 (n=9, fixtures/prettify, tolerance 0.0100) [new metric]
  segmentation mean IoU: 0.9299 (n=20, golden, tolerance 0.0100) [new metric]  [machine references acknowledged]
  WARNING: this baseline gates a pipeline check only. It does not satisfy the E1-T4 hand-mask DoD and must not be quoted as one.
BASELINE WRITTEN — the gate compares against it from the next run on.
rc=0

$ python infra/ci/gates/regression_gate.py --baseline $T/baseline.json
prettify SSIM (inside mask)                0.9936     0.9936   +0.0000  0.0100  PASS
segmentation mean IoU                      0.9299     0.9299   +0.0000  0.0100  PASS
GATE PASS — exit 0: 2 metric(s) compared, none below baseline - tolerance. …
rc=0

# the same baseline with segmentation inflated to 0.99 — a synthetic regression
$ python infra/ci/gates/regression_gate.py --baseline $T/baseline.json
segmentation mean IoU                      0.9900     0.9299   -0.0601  0.0100  REGRESSION
GATE REGRESSION — exit 1: segmentation mean IoU came in below baseline - tolerance. …
rc=1
```

**QA finding 1, fixed 2026-11-06 — a hand-deleted baseline row used to shrink the gate
silently.** Deleting `prettify SSIM (inside mask)` from `metrics` while leaving `sets` and
`mask_sources` alone compared one metric fewer and printed `PASS`. The three maps must now cover
exactly the same names, so the file is rejected instead:

```
$ # delete that row from "metrics" only; sets/mask_sources still list it
$ python infra/ci/gates/regression_gate.py --baseline $T/b.json
… table …
GATE INCONCLUSIVE — exit 2: this is NOT a pass.
  reason: $T/b.json: sets does not describe the same metrics as metrics — missing from sets: [], not in metrics: ['prettify SSIM (inside mask)']. A metric row deleted by hand would otherwise shrink the gate silently; rebuild the file deliberately with --update-baseline --allow-dropped-metrics (that flag records the coverage you are giving up)
  the file was rejected as a whole: no row in it was trusted, so none of the numbers above was gated. This is not the same as having no baseline.
rc=2
```

The residual this cannot close: an edit that removes a metric from all three maps consistently is
indistinguishable from a deliberately smaller baseline — no file-level check can see intent, so
`--update-baseline`'s drop guard plus git history are what remain.

That `$T` baseline is **not** committed: §13.5 explains why this ticket deliberately ships the
baseline empty instead of shipping exactly the file the third command above just proved passes.

`make evals` is pasted because the ticket's acceptance asks for it. It is the **E0-T5 telemetry
gate, not this dashboard** — there is no cost gate to run — but it is the same absence, reported
the same way (`n=0`, `GATE NOT EVALUATED`):

```
$ NO_COLOR=1 make evals
exec /Users/bujin/Documents/Projects/snaplist/.venv/bin/python -m evals.telemetry --since 7d --gate --allow-inconclusive
SnapList telemetry gate — E0-T5
population=0 items (from 0 sellers)
window=[2026-10-30T23:33:04.565759Z, 2026-11-06T23:33:04.565759Z)  half-open UTC on received_at
percentile=nearest-rank
git_sha=1beee75
source=sqlite:<unset>
backend=local-dev
refund_attribution=sqlite-only
source_state=SOURCE_ABSENT_3A

GATE NOT EVALUATED — n=0. This is NOT a pass.

gate                 n  value          threshold      verdict
prettify_p50         0  -              <= 20000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
prettify_p90         0  -              <= 35000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
tooltime_p50         0  -              <= 35000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
tooltime_p90         0  -              <= 60000 ms    UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
attrs_p90            0  -              <= 8000 ms     UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
export_p90           0  -              <= 5000 ms     UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
fields_mean          0  -              <= 5           UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)
fields_p90           0  -              <= 12          UNMEASURED (n=0 < 30)  n=0 items (from 0 sellers)

fields_p50=- (report only)
activation_7d=UNMEASURED (n=0 sellers < 20), n=0 sellers (reported, not gated)
export_burst_60m=UNMEASURED (no producer in CONTRACTS.md §7; QA proxy only)
completeness=- (mirror of excluded_rate; printed, never judged)
excluded_refunded=0 (n=0 refund rows)
failure_mix_unattributable=0
orphan_item_ids=0
failure_mix: bad_photo=0 no_garment=0 low_confidence=0 vton_rejected=0

VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
rc=0
```

`make evals` exiting 0 here is the documented `--allow-inconclusive` downgrade, not a pass —
same rule as §11: CI green is not a gate pass.

### 13.3 The cost table (generated by `cost_dashboard.py --update-doc` — do not hand-edit)

The block below is machine-written. Running
`.venv/bin/python infra/ci/gates/cost_dashboard.py --update-doc` replaces everything between the
two markers from `evals/reports/cost.md`; if the file does not exist the block is regenerated
with `UNMEASURED` in every cell and the reason. Nothing inside the markers is ever typed by hand.

<!-- E0-T4:COST-DASHBOARD:START -->
<!-- generated by infra/ci/gates/cost_dashboard.py --update-doc at 2026-11-06T23:54:38+00:00 git_sha=1beee75e5df0 · source: evals/reports/cost.md (ABSENT) -->

**Source:** `evals/reports/cost.md` — ABSENT. Target: `<= $0.03/item` for E1+E2+E3.

| stage | what it covers | $/item | samples | status |
|---|---|---|---|---|
| E1 | segmentation | UNMEASURED | — | UNMEASURED |
| E2 | rectify + prettify | UNMEASURED | — | UNMEASURED |
| E3 | attributes + dedupe | UNMEASURED | — | UNMEASURED |
| E4 | on-model render (per render, not per item) | UNMEASURED | — | UNMEASURED |
| **E1+E2+E3** | **cost per item** | UNMEASURED | — | UNMEASURED |

**E1+E2+E3 total:** `UNMEASURED`

> E1+E2+E3 total is UNMEASURED — evals/reports/cost.md does not exist — no stage has ever run against a paid provider (E4 on-model is a stub; FASHN_API_KEY and MODAL_TOKEN are absent, packages/vision/BLOCKED.md) and there are no production usage_events, so no $/item has ever been billed. E1/E2/E3 run locally on onnxruntime + classical CV: that produces wall-clock seconds, not a billed $/item

**Verdict:** **UNMEASURED — the <= $0.03/item verdict is UNREACHABLE, not passing: no measured $/item exists for any stage**

E4 is listed for context only: it is priced per render, is BLOCKED in this build (`packages/vision/BLOCKED.md`), and is not part of the per-item total.

Parse notes:
- the file does not exist, so nothing could be parsed

<!-- E0-T4:COST-DASHBOARD:END -->

### 13.4 What has to exist before any cell can hold a number

Nothing about this is blocked on code; all of it is blocked on credentials, paid runs, or data
nobody has produced yet.

1. **A paid provider path that actually runs.** `evals/reports/cost.md` is the dashboard's only
   input and it does not exist. It needs one row per stage (`E1`, `E2`, `E3`) carrying a billed
   `$/item` **and** the sample count behind it. Format contract:
   `infra/ci/gates/README.md`.
2. **A per-item billing basis for E1/E2/E3.** These stages currently run locally
   (`onnxruntime` u2net + classical CV), which produces wall-clock seconds, not a billed
   `$/item`. A local run's cost is not `$0.00/item` — it is *not a billed quantity*, and the
   dashboard refuses to publish the former as if it were the latter.
3. **E4 unblocked, separately.** `FASHN_API_KEY` / `MODAL_TOKEN` are absent and
   `infra/modal_vton.py` is a stub (`packages/vision/BLOCKED.md`). E4 is excluded from the
   `<= $0.03/item` total by construction — it is priced per render — so unblocking E4 is not a
   prerequisite for the E1+E2+E3 verdict.
4. **Then, and only then:** `cost_dashboard.py --update-doc` fills §13.3, and
   `cost_dashboard.py --fail-on-unmeasured` starts returning `0` (within budget) or `1` (over)
   instead of `2`.
5. **A regression baseline, decided on purpose.** See §13.5.

### 13.5 Why the cells are `UNMEASURED` and why the baseline ships empty

`infra/ci/gates/baseline.json` ships with `"metrics": {}`, and the gate therefore exits `2` with
the reason and the command to fix it. That is the delivered decision, not an oversight. The only
two metrics this checkout can measure are:

| metric | value | n | why it is not a bar |
|---|---|---|---|
| segmentation mean IoU | 0.9299 | 20 | references are `ai:google-ai-mode-nano-banana` + `algorithmic:matting`, not hand masks — a pipeline check, not the E1-T4 DoD number (worst case 0.6053) |
| prettify SSIM (inside mask) | 0.9936 | 9 | SSIM of the output against its own input on fixtures with synthetic labels; 1.0 means nothing changed, so it is a destruction budget, not a cleaning score |

Baselining either one would convert a self-referential measurement into "the bar", and every
later `PASS` would hide the caveat that the report body carries. The same reasoning applies to
the cost cells: with no `cost.md`, no paid run and no production `usage_events`, the honest
value of every `$/item` cell is `UNMEASURED`, and **the `<= $0.03/item` verdict is UNREACHABLE —
not passing, not failing, simply never computed.**

Three named residuals in this section's scope:

* **CLOSED.** `pyproject.toml`'s `testpaths` does not list `infra/ci/gates/tests`, so a plain `pytest` does
  **not** collect the 65 cases. They are run with the explicit `unittest discover` command in
  §13.2. Editing `pyproject.toml` is outside E0-T4's allowed paths. — *Status:* `pyproject.toml`
  now lists `infra/ci/gates/tests`; a plain `pytest` collects all 65 cases (793 = 728 + 65), and
  the legacy `unittest discover` command still works.
* **REPLACED.** `.github/workflows/ci.yml` (also outside this ticket's paths) does not call either tool, so
  neither runs on a PR yet. When it is wired: `regression_gate.py`'s `2` must be treated as
  *not a pass* — a `|| true`, or accepting any non-1 code, would restore exactly the false green
  this ticket removes. `--fail-on-unmeasured` is the flag for the blocking form. — *Status:*
  replaced by the new named residual in §13.6: `ci.yml` step `gates_report` now runs both tools
  on every PR as a non-blocking report step, with the narrow rc whitelist documented there.
* **CLOSED BY WIRING.** **`cost_dashboard.py` exits 0 in report mode even when the verdict is `FAIL`** — a report is not
  a gate — so any CI wiring must pass `--fail-on-unmeasured`, or a breached `$0.03/item` budget
  prints `FAIL` and returns green: the same false green as the regression gate's `2`, one flag
  away. — *Status:* `steps.sh` always invokes `cost_dashboard.py --fail-on-unmeasured`, so the
  report-mode 0-on-`FAIL` path cannot make CI green by itself.

### 13.6 E0-T4 wiring closure

`gates_report` is a **non-blocking report step**, deliberately not a gate. Both tools run in
their strictest form on every PR; only `rc=2` together with the tool's own NOT-A-PASS marker is
downgraded to a `::warning::`. A green `gates_report` never means a gate passed.

**Named residual — E0-T4 wiring.** `ci.yml` step `gates_report` runs both E0-T4 tools on every PR. In this checkout `regression_gate.py` exits `2` (deliberately empty baseline) and `cost_dashboard.py --fail-on-unmeasured` exits `2` (`evals/reports/cost.md` does not exist). The step preserves both real exit codes, prints both full reports, and downgrades **exactly** those two `2`s to `::warning::` annotations that literally say "this is NOT a pass". **CI green therefore means "both reports were rendered", not "either gate passed."** It is not `|| true`, not `continue-on-error`, and not "accept any non-1 code": `1` (regression / budget breach), `>=3` (unexpected) and `2` without the tool's own NOT-A-PASS marker still fail the job. This residual is **deliberately temporary** and closes under the upgrade conditions below. No number in this section was estimated and no cell was filled by hand.

Upgrade to a blocking gate — **all three must hold simultaneously**, each mechanically decidable:

* **U1 — hand masks land.** `evals/golden/manifest.json` contains hand-drawn masks
  (`mask_source` starts with `hand`, following the `evals/run.py::_is_hand_made_source` /
  `evals/tools/seg_iou.py::_is_hand_made` convention) and the count meets E0-T1's 20.
  **`evals/golden/**` is a copyright-frozen zone; this ticket does not touch it.** U1 can only be
  triggered by a future ticket that owns permission over that directory — otherwise this
  residual becomes a **permanent** residual by construction.
* **U2 — the baseline is filled on purpose.** `infra/ci/gates/baseline.json` is populated via
  `--update-baseline` (if the references are still machine-sourced, that requires an explicit
  `--allow-machine-references` plus a recorded audit note), and `metrics` is non-empty.
* **U3 — the cost report is billed.** `evals/reports/cost.md` is produced by a **billing
  harness**, and both tools can reach `0` or `1` — i.e. `--fail-on-unmeasured` no longer
  returns `2`.

When U1 ∧ U2 ∧ U3 hold, the exact lines to change:

1. In `infra/ci/steps.sh`, delete the two triage `if`/`elif` blocks inside
   `step_gates_report()`; replace `return "$bad"` with, in sequence, `return "$rg_rc"` /
   `return "$cost_rc"` / `return 0` (fail on the first non-zero, otherwise 0).
2. **Rename** the step `gates_report` → `regression_cost_gates` in exactly six places:
   `steps.sh`'s function name, its `_emit` argument, the `STEP_ORDER` item, the `main()` case
   arm, `ci.yml`'s `- name:` and its `run:` line. Keeping "report" in the name would make the
   name lie about a now-blocking gate.
3. Keep `--fail-on-unmeasured`: it is the blocking form, and dropping it would reintroduce the
   report-mode 0-on-`FAIL` false green.
4. Mark this residual **CLOSED** here, and sync `infra/ci/README.md`'s step map and
   `infra/ci/gates/README.md`.

## 14. Handoff state

- E0-T5 shipped at `73a95ba` (`HEAD` = `origin/master`). The R7+R4 closure (this document's
  edits plus `Makefile`, `pyproject.toml`, `test_makefile_wiring.py`) is committed in the
  follow-up and pushed.
- `.kilo/` remains untracked and untouched; the 6.5 GB SDXL weights were not touched, not
  removed, and no cleanup was performed.
- The 18-file E0-T5 inventory (§12) is unchanged except for the md5 of
  `test_makefile_wiring.py` (the tripwire is gone, replaced by a real wiring assertion).

---

## 15. E3-T3 / E3-T4 — real measurements on the locally frozen golden set

**Status: MEASURED on this machine; the numbers are local, not committed.** This round turned
`attr category accuracy`, `attr colour accuracy`, `attr pattern accuracy`,
`attr material accuracy` and `dedupe false-merge rate` from `UNMEASURED` into real numbers in
`python -m evals.run --suite vision`. The four attribute numbers are read from a frozen
predictions artifact; the harness itself never calls the network.

### 15.1 Commands and the collection/computation split

```
python -m evals.tools.collect_attrs       # one-shot: 40 real VLM calls, writes the artifact
python -m evals.metrics.attrs             # pure: writes evals/reports/attrs.md
python -m evals.metrics.dedupe            # pure: writes evals/reports/dedupe.md
python -m evals.run --suite vision        # acceptance command, zero network
```

`evals/tools/collect_attrs.py` is the ONLY module that touches the network or reads a
credential. It parses `.env` line by line **in Python** (`k, v = line.split("=", 1)`) into
`os.environ` — never `source .env`, which eats the quotes inside
`SNAPLIST_VLM_EXTRA_BODY`. It writes `evals/reports/attrs_predictions.json` with predictions
and metadata only: no photo bytes, no base64, no key, no authenticated URL fragment. Error
strings are scrubbed of credential values. `evals/metrics/attrs.py` and
`evals/metrics/dedupe.py` are pure functions over files on disk: no socket, no credential, no
VLM. `evals/run.py` only reads the artifact and returns `UNMEASURED` with a named reason when
it is absent or corrupt.

### 15.2 What was actually collected

| | |
|---|---|
| golden set | `evals/golden/manifest.json`, 40 entries, **read only** (copyright-frozen zone) |
| provider / model | `http` / `deepseek-flash` (from `.env`; no key value recorded anywhere) |
| n | 40 — the whole manifest, failures included |
| n_measured / n_failed | **39 / 1** (`hard-04` failed schema validation after the adapter's one repair retry: `evidence.defects` was an extra input) |
| prompt / completion / total tokens | **55320 / 10913 / 66233** |
| provider round-trip total | 127.333 s over 40 calls |
| adapter retry count | 16 (the adapter's built-in single schema-repair retry; no second retry layer was added, so token totals are not silently undercounted) |
| artifact | `evals/reports/attrs_predictions.json` (gitignored) |

The single failure is **not** skipped: `evals/metrics/attrs.py` counts it in `n` as a MISS on
all four fields. `StageMeta` does not exist for a failed call, so any tokens it consumed are
not observable; the artifact states this explicitly rather than estimating them.

### 15.3 E3-T3 results — exact `hits/n`, no rounding up

Matching rules (frozen, identical in shape for all four fields; full text in the module
docstring and `evals/reports/attrs.md`): strings are `.strip().lower()`-normalised;
`category` and `pattern` are exact string equality (`unknown` never matches a concrete value);
`colors` is a **primary-colour hit** (first predicted colour occurs in the ground-truth list);
`material` is a **non-empty list intersection** (empty prediction = MISS; `colors_exact_list`
is reported separately as a stricter secondary statistic).

| metric | target | hits/n | accuracy | verdict |
|---|---|---|---|---|
| attr category accuracy | >= 0.95 | 37/40 | **0.9250** | **DOES NOT MEET** (shortfall 0.0250) |
| attr colour accuracy | >= 0.90 | 26/40 | **0.6500** | **DOES NOT MEET** (shortfall 0.2500) |
| attr pattern accuracy | >= 0.85 | 39/40 | **0.9750** | **MEETS** |
| attr material accuracy | >= 0.80 | 11/40 | **0.2750** | **DOES NOT MEET** (shortfall 0.5250) |

Secondary statistics: `colors_exact_list` = 19/40 = 0.4750; `material` empty predictions =
26 of 40 rows; failed rows with no prediction = 1. Three of four targets are missed. The
numbers above are the measured numbers; nothing was rounded up, no failed or empty prediction
was excluded, and no threshold was moved.

### 15.4 The `material` verdict is an evidence-policy consequence, not a bug

`apps/api/schemas.py::ItemAttributes` and `packages/vision/attrs_adapters/base.py` require
`material` to contain ONLY fibres with visual or care-label evidence; with no such evidence the
adapter MUST emit `[]`. The golden photos are catalogue shots with no legible care labels, so
`material=[]` is the CORRECT output for most of them (26 of 40 rows), and an empty list can
never intersect a non-empty ground-truth material list. **`>= 0.80` is UNREACHABLE under the
current evidence policy. This is NOT a bug and NOT a model defect.** This round did **NOT**
relax the evidence policy, did **NOT** widen the adapter, did **NOT** change the VLM prompt,
and did **NOT** edit any ground truth to chase the threshold. Reaching 0.80 would require a
product decision to change the schema contract or a golden set with visible care labels —
neither is in this ticket's scope.

### 15.5 Confusion summary

`evals/reports/attrs.md` contains the confusion list **verbatim**: 68 rows = 47 primary misses
+ 21 `colors_exact_list` secondary-statistic misses, each with id / field / ground truth /
predicted / reason. Primary misses by field: `material` 29, `colors` 14, `category` 3,
`pattern` 1. The dominant material pattern is `[]` against a single-fibre ground truth; the
dominant colour pattern is a plausible but different primary colour (e.g. `tan` vs `brown`,
`navy` vs `grey`); `hard-04` contributes four misses from one schema validation failure.
The per-entry table in the same report shows all 40 rows, so the denominator is visible.

### 15.6 E3-T4 result

`python -m evals.metrics.dedupe` is a thin wrapper around
`packages.vision.dedupe.run_fixture()` — the same `_score_candidate()` policy the API uses;
no comparison logic, threshold or sha256 logic was rewritten. Over the 20 shipped fixture
pairs (`n_pairs=20`, `n_duplicate=10`, `n_distinct=10`):

**false_merge_rate = 0 / 10 = 0.0000 — MEETS the `<= 0.05` target.**

The denominator is `n_distinct`, not `n_pairs`: a false merge can only occur on a distinct
pair. `evals/reports/dedupe.md` lists all 20 per-pair verdicts plus the raw `run_fixture()`
output. Caveats that travel with the number: `exact` pairs short-circuit on sha256 (their
1.0 is the layer's definition, not a measured capability); `near` positives are same-source
transforms, so near recall on this fixture is an upper bound and the thresholds are
in-sample; the colour gate is defended by unit tests, not observable on these 20 pairs.

### 15.7 The clean-CI caveat — UNMEASURED there is NOT a regression

These four attribute numbers come from the **locally frozen, uncommitted** `evals/golden`
photos (web-sourced, all rights reserved — `evals/golden/**` is a read-only copyright-frozen
zone and was not touched). `evals/reports/*` is excluded by `.gitignore:35`, so a clean CI
checkout has no `attrs_predictions.json`. On such a checkout the four attribute rows in
`python -m evals.run --suite vision` are `UNMEASURED` with an explicit reason naming
`python -m evals.tools.collect_attrs`. **That is the harness refusing to guess, not a
regression and not a failing gate** (the gate itself is still E0-T5). The dedupe row is
different: its fixtures ship with the repo, so it is measured on a clean checkout too.
`evals/reports/cost.md` is deliberately not filled — no real billing rate was supplied — and
the E2-T3 manual hallucination rubric stays `UNMEASURED` because a machine cannot stand in
for a human scorer.

### 15.8 Allowed-paths whitelist exception (recorded, as instructed)

PLAN E3-T3 lists only `evals/metrics/attrs.py` and `evals/reports/attrs.md` as allowed paths,
but its acceptance command is `python -m evals.run --suite vision`. To reach that command this
round **deliberately crossed the whitelist**: it wired `evals/run.py` (two new `_measure_*`
helpers inside `measurements()` only; `TARGETS`, `render()`, and all seg_iou/gate code are
unchanged) and added `evals/tools/collect_attrs.py` as the one-shot collector. E3-T4 added
`evals/metrics/dedupe.py`. Also touched: `evals/tools/__init__.py` (one docstring line, to
record the collector as the single deliberate network exception), `evals/tests/
test_attrs_metric.py`, `evals/tests/test_dedupe_metric.py`, and this document (the tracked
handoff doc). No file under `evals/golden/**`, `.env`, `apps/api/schemas.py`,
`packages/vision/attrs_adapters/**`, `packages/vision/dedupe.py`, `Makefile`, or
`pyproject.toml` was modified. Nothing was committed, staged or pushed by this round.

### 15.9 Tests and verification

20 new unit tests (`evals/tests/test_attrs_metric.py`, `evals/tests/test_dedupe_metric.py`)
cover the four matching rules, `unknown` pattern, primary-colour hit vs strict list equality,
failed entries staying in the denominator as misses, empty `material` as a miss, raw `hits/n`
floats, the verbatim confusion list, missing/corrupt artifact producing harness
`UNMEASURED` with a named reason, `run_fixture` reuse (identity + call-count checks), the
`n_distinct` denominator, and explicit socket guards. All tests use synthetic fixtures and
are zero-network. `make test`, `ruff check .` and `git diff --check` are green.
