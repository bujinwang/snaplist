# E0-T3 — CI pipeline: lint + test + evals on every PR

Five files, all inside the ticket's allowed paths (`.github/workflows/**`,
`infra/ci/**`). No source, test, config, or `docs/**` file was touched.

## What this is

One GitHub Actions job, `ci`, plus a **local equivalence runner**. Both drive the
same `infra/ci/steps.sh` functions, so there is exactly one place where a
command and its ordering are defined. `ci.yml` calls each step as a separate
`run:` line; `local_runner.py` calls `steps.sh all`. `steps.sh` is the single
source of truth — the wiring is not a claim about YAML text, it is an execution
relationship that the runner verifies.

The **triangle of enforcement** (the reason this ticket exists):

| edge | owner | what it catches |
|---|---|---|
| python code ↔ TypeScript code, plus runtime behaviour | `make lint` / `make test` (ruff, tsc, contract tests, `test_contract_parity.py`, JS tests, pytest) | name / nullability / enum-order drift, behaviour regressions |
| **frozen document ↔ real code constants** | **`infra/ci/check_contracts.py` (`contract_gate`)** | **a version bump in code with a stale headline; an enum reordered in code with a stale fenced block; a renamed event with a stale table — all invisible to the test suite** |
| job wiring / green / red | `ci.yml` + `infra/ci/local_runner.py` | steps not actually invoked, a gate nobody has seen go red |

Three design decisions worth stating plainly:

* **Every blocking step calls the underlying command directly, never `make`.**
  GNU Make 3.81 (macOS stock) collapses any non-zero recipe exit to `2`.
  Routing through make would destroy `evals.telemetry`'s real `0/1/2/3` exit
  codes. `steps.sh` keeps the top-of-file mapping to the Makefile targets.
* **`contract_gate` is step 2, before `install`.** It is pure standard library,
  needs no venv, no `node_modules`, no network and no build, so a broken frozen
  contract fails the job in seconds. It also cannot be affected by anything an
  install might change — the failure mode `docs/DEPS.md` records for
  `fetch-models` changing `runtime.probe()` defaults.
* **Zero new dependencies.** Both `.py` files use only the standard library.
  GitHub Actions `uses:` entries are workflow tooling, not project runtime
  dependencies, so DoD #5 is not triggered and `docs/DEPS.md` is untouched.
  Installs are frozen (`uv sync --frozen`, `pnpm install --frozen-lockfile`),
  so no lockfile is rewritten.

The gate is **bidirectional**, not a string-existence check: Python constants
are read with `ast` from `apps/api/schemas.py`, TypeScript constants with
restricted regexes from `analytics.ts` / `schemas.ts`, and document values are
extracted from CONTRACTS.md; the two sides are compared. No business constant,
version, event name or enum value is hardcoded in `check_contracts.py` (the only
literal table is `WORD_TO_NUM`, language-level number words). The document path
is fixed via `Path(__file__).resolve().parents[2]` and there is deliberately
**no CLI flag** to point it at a different file. Any missing file, parse error
or unextractable anchor is fail-closed (`CONTRACT_GATE: FAIL <id> ...`, rc=1).

Anchors A1–A8 are implemented; **A9 is not** (item-id grading is too brittle and
would degrade into a text assertion). A structural check requires all nine
`## 1.` … `## 9.` headings, so deleting a whole section cannot bypass the gate:
it is guaranteed to turn **red**, but *which anchor id fires depends on the
first check that fails*. QA measured that deleting the `## 4.` heading hits
**A7** (the section goes empty, so A7's table parse fails before STRUCT runs),
not STRUCT. The guarantee is "red", never "a particular anchor id".
`CONTRACT_GATE: OK (9 anchors checked)` counts 8 semantic anchors + 1 structural
check — a real count, not a literal.

**A hash seal was deliberately NOT adopted** (no `infra/ci/contracts.sha256`):
it covers only the document side, is trivially defeated by "edit then refresh
the hash", and turns a typo into a red build. Named residual: this gate protects
the **frozen semantic anchors**, and does **not** promise that an arbitrary
paragraph of prose being edited turns CI red. If you need that, it needs an ADR.

`CONTRACT_VERSION` is deliberately **not** compared to the document's `v1.0.0`
status: §9 says that value "does not move" and they are different quantities;
hardcoding equality would create an out-of-the-box red gate. Equality of the two
code-side `CONTRACT_VERSION` values is already covered by
`test_contract_parity.py`; only the §9 "does not move" sentence is checked here,
as a sentinel.

## Step map

Order is identical in `ci.yml`, `steps.sh`'s `STEP_ORDER`, and
`local_runner.py`'s assertions. `pnpm -w test` **must** precede pytest
(Makefile:60–63: JS builds `packages/shared/dist`, which the fail-closed python
parity test reads).

| # | step | command (verbatim, from `steps.sh`) | Makefile target | failure semantics |
|---|---|---|---|---|
| 1 | `toolchain_probe` | `python3 --version && node --version && pnpm --version && uv --version && git rev-parse HEAD` | — | blocking; prints the real toolchain versions (DoD #3) |
| 2 | `contract_gate` | `python3 infra/ci/check_contracts.py` | — (E0-T3's own) | blocking, **before install**; fail-fast on frozen-contract drift |
| 3 | `install` | `uv sync --frozen && pnpm install --frozen-lockfile` | `make install` | blocking; frozen, no lockfile rewrite |
| 4 | `lint_py` | `.venv/bin/python -m ruff check .` | `make lint` (1/2) | blocking; also lints `infra/ci/*.py` |
| 5 | `lint_ts` | `pnpm -w lint` | `make lint` (2/2) | blocking; tsc `--noEmit` |
| 6 | `test_js` | `pnpm -w test` | `make test` (1/2) | blocking; builds `packages/shared/dist` |
| 7 | `test_py` | `.venv/bin/python -m pytest` | `make test` (2/2) | blocking; includes parity + telemetry suites |
| 8 | `evals` | `.venv/bin/python -m evals.telemetry --since 7d --gate --allow-inconclusive` | `make evals` | blocking; **real rc preserved**, plus a non-blocking `::warning::` when `GATE NOT EVALUATED` is printed |
| 9 | `evals_report` | `.venv/bin/python -m evals.telemetry --since 7d` | `make evals-report` | report-only; the command itself exits 0 |
| 10 | `dirty_check` | `git status --porcelain --untracked-files=all` | — | blocking; exact filename whitelist — **collapsed directory-level lines are no longer accepted**; no tracked-file pollution, no unexpected artefacts |

`evals` captures the gate's output to a temp file **outside the repo**, `cat`s
it for the log, then returns the captured original rc — no pipe anywhere, so
`pipefail` cannot be asked to rescue a swallowed status. The `::warning::` is
emitted by `grep` on that file and never changes the exit code.

`dirty_check`, `--untracked-files=all` and `.kilo/`: plain
`git status --porcelain` **collapses** untracked directories into a single line
(`?? infra/ci/`), which silently tolerates junk dropped *inside* that directory.
QA demonstrated exactly this false green with `qa-garbage.bin`. The step therefore
uses `git status --porcelain --untracked-files=all`, where every untracked file
gets its own line, and applies an **exact filename whitelist**:

* the five E0-T3 deliverables (`?? .github/workflows/ci.yml`,
  `?? infra/ci/{steps.sh,check_contracts.py,local_runner.py,README.md}`) —
  untracked only until the lead commits them, then they vanish from status;
* the `?? .kilo/` **prefix** — a long-lived untracked scratch directory the lead
  owns. Under `-uall` git expands it to per-file lines
  (`?? .kilo/plans/<file>.md`), so it is allowed as a prefix, not as one exact
  line. Asserting empty output locally would be a 100% false red.

A fresh GitHub checkout (files committed, no `.kilo/`) is **empty** → pass.
Every other line fails: a stray junk file anywhere in `.github/` or `infra/ci/`,
a modified tracked file (` M ...`), or a build artefact `.gitignore` forgot.
`local_runner.py` now includes a repeatable **junk probe** proving 3 planted
files + 1 tracked edit all turn the step red and that cleanup returns it green.
`.kilo/` is never deleted or touched.

Triggers: `pull_request` (master), `push` (master), `workflow_dispatch`.
`workflow_dispatch` is the shortest path to a real GH job URL without opening a
PR first. `pull_request_target` is deliberately **not** used (it would hand
secrets access to PR code). One job, id `ci` (PLAN.md:226 selects it via
`act -j ci`), `permissions: contents: read`, per-branch `concurrency` with
`cancel-in-progress: true`.

## Action pins

Every `uses:` is pinned to a 40-character commit SHA, resolved by the lead on
2026-11-05 with `git ls-remote` (dereferenced `^{}` commit, not the tag object —
this matters for `pnpm/action-setup` v4.1.0). Human-readable versions are kept
in comments directly above each `uses:`.

| action | human version | pinned commit SHA |
|---|---|---|
| `actions/checkout` | v4.2.2 | `11bd71901bbe5b1630ceea73d27597364c9af683` |
| `actions/setup-python` | v5.6.0 | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| `astral-sh/setup-uv` | v6.4.3 | `e92bafb6253dcd438e0484186d7669ea7a8ca1cc` |
| `pnpm/action-setup` | v4.1.0 | `a7487c7e89a18df4991f7f222e4898a00d66ddda` |
| `actions/setup-node` | v4.4.0 | `49933ea5288caeca8642d1e84afbd3f7d6820020` |

Order: checkout (`fetch-depth: 1`) → setup-python `3.11` → setup-uv
(`version: "0.8.2"`, `enable-cache: true`) → **pnpm/action-setup before
setup-node** (otherwise `cache: pnpm` cannot find pnpm) → setup-node `22`.
`package.json` has no `packageManager` field, hence the explicit pnpm version.

**Trust boundary.** `actions/*` are GitHub-official first-party actions.
`astral-sh/setup-uv` is maintained by Astral (the upstream `uv` vendor) but is
**not** a GitHub-official action, and `pnpm/action-setup` is community-owned.
All five are immutable-SHA pinned, which limits (but does not eliminate) the
trust placed in third-party release processes. `permissions: contents: read`
keeps the `GITHUB_TOKEN` read-only. `pull_request_target` is not used. Private-
repo fork-PR permission behaviour is UNVERIFIED (below).

**Why no `docs/DEPS.md` row.** Global DoD #5 applies to a *new dependency* of
the project. GitHub Actions `uses:` entries are workflow tooling executed by the
CI runner, not Python/JS runtime dependencies; no `pyproject.toml`,
`package.json` or lockfile changed, and no new Python/JS package was introduced.
`docs/DEPS.md` is also outside this ticket's allowed paths. On the two pin
conflicts the lead resolved by measurement: `pnpm --version` = `12.16.1` is npm's
current `latest`, and `pnpm install --frozen-lockfile --offline` succeeds against
`lockfileVersion: '9.0'` (`Lockfile is up to date, resolution step is skipped`),
so pinning `12.16.1` is correct despite the visually older lockfile. `uv` exists
twice locally (`~/.local/bin/uv` = 0.8.2, `/usr/local/bin/uv` = 0.7.13); both
report `Would make no changes` for `uv sync --frozen --dry-run`. CI pins 0.8.2;
0.7.13 is also verified compatible. `uv.lock` requires `==3.11.*`, so CI pins
Python 3.11; local `node` varies by PATH (22.22.2 / 23.11.0 / 24.21.0), CI pins
22.

## Changing a frozen contract

The gate is meant to make the legitimate path easy and the quiet edit loud. To
change anything in `docs/CONTRACTS.md`:

1. Write `docs/ADR-009-<slug>.md` (next free ADR number) in `docs/`: what
   changes, why, what breaks, migration for existing rows and mobile clients.
2. In the **same PR**, change the constants on **both** code sides
   (`apps/api/schemas.py`, `packages/shared/src/schemas.ts`, and/or
   `packages/shared/src/analytics.ts`) with the `CONTRACT_VERSION` /
   `EVENTS_VERSION` bump the ADR calls for.
3. Update the corresponding CONTRACTS.md sections — §7 title **and** the
   `Current: EVENTS_VERSION = …` line, the §7 event table, the §7 prose count
   (`**Seven events.**` → `**Eight events.**`), §9 version history's last row
   (which must carry an `ADR-00N` reference), the §1 Enumerations fenced block,
   the §4 and §6 `reason_code` tables. A3/A4 exist precisely so "constant
   changed, prose did not" cannot slip through.
4. `make test` must pass: the parity test fails on any name, nullability or
   enum-order drift, and `contract_gate` fails on any document ↔ code drift.
5. **The gate script normally needs no change.** Anchors read real values from
   both sides, so adding an eighth event with all document sections updated is
   accepted without editing `check_contracts.py`. The one likely exception is
   the §1 Enumerations block's structural six-line count and the
   CamelCase → `UPPER_SNAKE` array-name derivation in A6 if a new enum is added.
6. **Any change to an anchor, to `WORD_TO_NUM`, or to the §9 sentinel made for
   the purpose of "making CI green" is itself a gate change and needs an ADR.**
   Weakening the gate to accommodate a code change is not a fix.

`infra/ci/gates/**` is deliberately **not** created here — PLAN.md:205 reserves
it for E0-T4 (regression gate + cost dashboard).

## Acceptance evidence

All tails below are verbatim from self-checks run on the clean main worktree at
HEAD `3cc029e09894db401ca5c1282ddceca174b7c0a4`. Environment **on the PATH used
for these runs**: `python3` = `Python 3.13.12` (system; the gate uses stdlib
only), `.venv/bin/python` = 3.11, `node` = `v22.22.2`, `pnpm` = `12.16.1`,
`uv` = `0.8.2 (21fadbcc1 2025-07-22)`. **These values vary by PATH on this same
machine** — QA independently observed `python3` = 3.9.6 (`/usr/bin/python3`),
`node` = v23.11.0 and `uv` = 0.7.13 under a different PATH prefix. QA also ran
`check_contracts.py` under Python 3.9.6 / 3.11.15 / 3.12.8 / 3.13.12 (all
`OK (9 anchors checked)`, rc=0) and `local_runner.py` under 3.9.6 (rc=0), so the
stdlib-only claim holds and **no number in this table depends on a fixed
interpreter version**.

**1) `bash -n infra/ci/steps.sh`** → `bash_n_rc=0` (no output).

**2) `.venv/bin/python -m py_compile infra/ci/check_contracts.py infra/ci/local_runner.py`**
→ `py_compile_rc=0` (no output).

**3) `.venv/bin/python -m ruff check .`**
```
All checks passed!
```
`ruff_rc=0`. Note ruff scans `infra/ci/*.py` (`line-length=100`, `select =
["E","F","I","UP","B"]`, `target-version=py311`); `testpaths` does not include
`infra/ci`, so these scripts are not collected by pytest.

**4) `python3 infra/ci/check_contracts.py`** (clean checkout, out of the box):
```
CONTRACT_GATE: OK (9 anchors checked)
```
`gate_rc=0`. **Real N = 9** (A1–A8 + STRUCT). No anchor was relaxed to get here.

**5) `bash infra/ci/steps.sh all`** — full GREEN baseline, tail from the round-2
run (elisions marked `...`; lines shown are verbatim):
```
687 passed, 4 skipped, 1 xfailed, 1 warning in 64.74s (0:01:04)
STEP test_py rc=0
STEP evals: .venv/bin/python -m evals.telemetry --since 7d --gate --allow-inconclusive
...
GATE NOT EVALUATED — n=0. This is NOT a pass.
...
VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 DATA_UNAVAILABLE=0 REFUND_ATTRIBUTION=INTACT
::warning:: CI green != gate passed — evals.telemetry printed "GATE NOT EVALUATED" (n=0, --allow-inconclusive downgrade). This is NOT a pass.
STEP evals rc=0
STEP evals_report: .venv/bin/python -m evals.telemetry --since 7d
...
STEP evals_report rc=0
STEP dirty_check: git status --porcelain --untracked-files=all
STEP dirty_check rc=0
STEPS_ALL_RC=0
```
Earlier steps in the same run: `contract_gate` → `CONTRACT_GATE: OK (9 anchors
checked)` / `STEP contract_gate rc=0`; `install` → `Lockfile is up to date,
resolution step is skipped` / `STEP install rc=0`; `lint_py` →
`All checks passed!`; `test_js` → `ℹ pass 76 / ℹ fail 0`. The pytest wall time
is a local number that drifts between runs (53.82 s in round 1, 64.74 s here,
65.21 s inside the runner) — it is not a GH timing.

**6) `python3 infra/ci/local_runner.py`** — RED/GREEN evidence (verbatim tail):
```
## RED — five semantic mutations, each in the worktree
  M1 EVENTS_VERSION = 1.4.0 -> 9.9.9
    all rc=1  contract_gate rc=1  short-circuit(before install)=True
    FAIL anchors = ['A1']  (expected A1)
      CONTRACT_GATE: FAIL A1 §7 version(s) ['9.9.9', '1.4.0'] != EVENTS_VERSION '1.4.0' in analytics.ts
  M2 delete §7 table row 7 (export_delivered)
    all rc=1  contract_gate rc=1  short-circuit(before install)=True
    FAIL anchors = ['A2']  (expected A2)
      CONTRACT_GATE: FAIL A2 §7 table numbering [1, 2, 3, 4, 5, 6] != [1, 2, 3, 4, 5, 6, 7]
  M3 drop `other` from §1 GarmentCategory line
    all rc=1  contract_gate rc=1  short-circuit(before install)=True
    FAIL anchors = ['A6']  (expected A6)
      CONTRACT_GATE: FAIL A6 §1 GarmentCategory ['top', 'bottom', 'dress', 'outerwear', 'shoe', 'accessory'] != schemas.py ['top', 'bottom', 'dress', 'outerwear', 'shoe', 'accessory', 'other']
  M4 **Seven events.** -> **Six events.**
    all rc=1  contract_gate rc=1  short-circuit(before install)=True
    FAIL anchors = ['A3']  (expected A3)
      CONTRACT_GATE: FAIL A3 '**Six events.**' says 6 but ANALYTICS_EVENTS has 7
  M5 header `frozen` -> `draft`
    all rc=1  contract_gate rc=1  short-circuit(before install)=True
    FAIL anchors = ['A5']  (expected A5)
      CONTRACT_GATE: FAIL A5 'frozen' sentinel missing from CONTRACTS.md Status header

## RESTORE — original bytes written back, contract_gate re-run
  contract_gate rc = 0
      CONTRACT_GATE: OK (9 anchors checked)
  sha256(pristine) = d54b8252d20f4b63912d26954e8c567a17fd93599cc7005d6b87e0befab059ea
  sha256(restored) = d54b8252d20f4b63912d26954e8c567a17fd93599cc7005d6b87e0befab059ea
  pristine == restored : True

## JUNK PROBE — planted files must turn dirty_check RED
  baseline (clean worktree, 2 copied CI files) rc = 0
  planted infra/ci/qa-garbage.bin -> rc = 1
      dirty_check: unexpected status line: ?? infra/ci/qa-garbage.bin
  planted .github/workflows/qa-garbage.yml -> rc = 1
      dirty_check: unexpected status line: ?? .github/workflows/qa-garbage.yml
  planted qa-stray.txt -> rc = 1
      dirty_check: unexpected status line: ?? qa-stray.txt
  modified tracked pyproject.toml -> rc = 1
      dirty_check: unexpected status line:  M pyproject.toml
  after all junk removed rc = 0
  VERDICT: 3/3 planted junk + 1 tracked edit RED; baseline/post GREEN

## CLEANUP / ZERO-POLLUTION PROOF
  git worktree remove --force rc = 0; prune done
  worktree still in `git worktree list` = False
  PRE  git status --porcelain = '?? .github/\n?? .kilo/\n?? infra/ci/\n'
  POST git status --porcelain = '?? .github/\n?? .kilo/\n?? infra/ci/\n'
  verbatim equal = True
  tracked-file modifications in snapshot = 0
  VERDICT: worktree removed; main worktree unchanged (zero pollution)

## EVIDENCE TABLE
  mutation | FAIL anchor(s) | all rc | gate rc | sha256(mut)[:12] | verdict
  M1       | A1            |      1 |       1 | e1d43a78dadb        | RED
  M2       | A2            |      1 |       1 | fa26bbd7d147        | RED
  M3       | A6            |      1 |       1 | b3ac422984c7        | RED
  M4       | A3            |      1 |       1 | 875f94a69abf        | RED
  M5       | A5            |      1 |       1 | c3d5e9400efd        | RED
  RED count = 5/5   RESTORE green = 1/1
  sha256(pristine) = d54b8252d20f4b63912d26954e8c567a17fd93599cc7005d6b87e0befab059ea
  sha256(mutated)  = e1d43a78dadb7e3cb81e899b6f17ebbcb75a789526b009c6e5d95ce3aa32d01e   (M1, representative)
  sha256(restored) = d54b8252d20f4b63912d26954e8c567a17fd93599cc7005d6b87e0befab059ea
  pristine == restored = True

RESULT: PASS (LOCAL EQUIVALENCE ONLY; GitHub Actions side UNVERIFIED)
```
`LOCAL_RUNNER_RC=0`. **Two different status views, deliberately.** The
`dirty_check` step and the JUNK PROBE use `-uall` + an exact whitelist (that is
the gate that was tightened). The runner's PRE/POST zero-pollution snapshot
still uses plain `git status --porcelain` — it is a *verbatim before/after
equality* check on the MAIN worktree, not a whitelist gate, and its purpose is
to prove the runner changed nothing. Neither view is a way back to the old
false green: junk planted inside a deliverable directory is now proven red by
the JUNK PROBE above. Three-state sha256, verbatim:
pristine `d54b8252d20f4b63912d26954e8c567a17fd93599cc7005d6b87e0befab059ea`,
mutated (M1) `e1d43a78dadb7e3cb81e899b6f17ebbcb75a789526b009c6e5d95ce3aa32d01e`,
restored `d54b8252d20f4b63912d26954e8c567a17fd93599cc7005d6b87e0befab059ea`
(pristine == restored). The runner also verified WIRING: `steps.sh STEP_ORDER
(10)` equals `ci.yml run lines (10)`, `contract_gate` before `install`, and no
`continue-on-error` / `|| true` / `|| echo` / `if: always()` in the YAML.

**RESTORE / RED honesty.** Each RED run executes `bash infra/ci/steps.sh all`
in a detached-HEAD worktree and short-circuits at `contract_gate` (step 2), so
`install` and every later step **do not execute** by construction — the runner
asserts `short-circuit(before install)=True` for all five. This is the default
GitHub Actions "fail the job, skip remaining steps" semantics. It is **not** a
full pipeline run: no dependency install, no lint, no tests, no evals. The
worktree needs no `.venv`/`node_modules` and no symlinks. Because HEAD
(`3cc029e`) predates E0-T3, `steps.sh` and `check_contracts.py` are copied into
the worktree as **real files, not symlinks**, to emulate the post-commit checkout
— `check_contracts.py` resolves its repo root from `__file__`, so a symlink back
into the main worktree would read the pristine document and produce a false
green.

**Named residual on DoD #2.** The global DoD requires pasting the `make test`
output tail into `docs/HANDOFF-<role>.md`. `docs/**` is **outside** E0-T3's
allowed paths, so `docs/HANDOFF-qa.md` was **not** updated by this ticket. The
real output tail is recorded above in this README instead (allowed path). We do
not claim the original wording of DoD #2 was satisfied; this is a named residual
for the lead/QA to close in a ticket that owns `docs/**`. Note also that we
invoke the underlying commands directly rather than `make test`, for the Make
3.81 exit-code reason above; the tail shown is from `.venv/bin/python -m
pytest` (the same command `make test` runs second).

**7) `git status --porcelain`** — see the "zero pollution" block above; the
final state contains only this ticket's five new files (collapsed by git into
`?? .github/` and `?? infra/ci/`) plus `?? .kilo/`, and **no tracked-file
change**. With `--untracked-files=all` the five files are listed individually:
`.github/workflows/ci.yml`, `infra/ci/steps.sh`,
`infra/ci/check_contracts.py`, `infra/ci/local_runner.py`,
`infra/ci/README.md`.

## Honesty / UNVERIFIED

* **`act -j ci` was NOT run.** `act` is not installed and the Docker daemon is
  not running on this machine. A real PR / `workflow_dispatch` GitHub job run,
  its job URL and its log tail are **UNVERIFIED** — no URL, SHA, duration or
  test count for a GH run is claimed here. `workflow_dispatch` is the shortest
  path to obtaining one.
* **GitHub Actions execution is UNVERIFIED.** The GH-side `checkout` /
  `setup-python` / `setup-uv` / `pnpm/action-setup` / `setup-node` actions,
  their cache behaviour and their interaction with `ubuntu-24.04` were never
  executed by this runner. `local_runner.py` proves only local equivalence.
* **Required status check NOT configured.** That is a repository-settings
  action outside this ticket's whitelist and needs Bert. Until it is set,
  **a red CI does not actually block a merge** — the gate has teeth locally but
  no enforcement authority on GitHub yet. UNVERIFIED.
* **`runs-on: ubuntu-24.04` and `timeout-minutes: 20` are unverified initial
  values.** Real GH job duration, cache hit rate and step timings are
  **UNMEASURED**. The pytest suite took 53.82 s (round 1) / 64.74 s (round 2)
  on this machine; those are local numbers that drift between runs, not GH
  timings.
* **Private repo / fork-PR permission boundary UNVERIFIED.** `bujinwang/snaplist`
  is private; `permissions: contents: read` and the absence of
  `pull_request_target` are deliberate, but fork-PR behaviour for a private repo
  has not been tested.
* **The gate protects frozen semantic anchors, not arbitrary prose.** Editing a
  paragraph that no anchor reads will not turn CI red. This is a named residual,
  not a promise of whole-document integrity; a hash seal was rejected (above).
* **`dirty_check`'s QA-found false green is FIXED (round 2), by lead ruling.**
  The original implementation used plain `git status --porcelain` with
  directory-level allowances (`?? .github/`, `?? infra/ci/`); QA proved a junk
  file dropped inside those directories was silently tolerated. It is now
  `--untracked-files=all` + an **exact filename whitelist** for the five
  deliverables, so a planted `?? infra/ci/qa-garbage.bin` → rc=1. The one
  remaining tolerance is the `?? .kilo/` **prefix**: that scratch directory is
  the lead's, is not ours to enumerate, and `-uall` expands it into per-file
  lines, so it cannot be pinned to one exact line. **Named residual:** junk
  planted inside `.kilo/` is still tolerated (unchanged from the original
  ruling); junk anywhere else in the deliverable directories is not. The
  repeatable junk probe in `local_runner.py` locks in the fixed behaviour.
* **`evals` rc=0 is a dev/CI downgrade, NOT a pass.** `make evals` /
  `--gate --allow-inconclusive` returns 0 on an empty database, printing
  `GATE NOT EVALUATED — n=0. This is NOT a pass.` and
  `VERDICTS: PASS=0 FAIL=0 INCONCLUSIVE=8 ...`. The pipeline emits a
  `::warning::` for exactly this. It is never counted as evidence that a
  telemetry gate passed, and GREEN is judged only by all step rc=0 plus
  `CONTRACT_GATE: OK`.
* **Local toolchain drift (values vary by PATH on the same machine).** The
  engineering runs resolved `python3` = 3.13.12, `node` = 22.22.2,
  `uv` = 0.8.2; QA's independent runs under a different PATH prefix resolved
  `python3` = 3.9.6 (`/usr/bin/python3`), `node` = v23.11.0, `uv` = 0.7.13.
  22.22.2 / 23.11.0 / 24.21.0 node builds and both `uv` builds are installed;
  `.venv` is Python 3.11. QA verified `check_contracts.py` returns
  `OK (9 anchors checked)` rc=0 under Python 3.9.6 / 3.11.15 / 3.12.8 / 3.13.12
  and `local_runner.py` rc=0 under 3.9.6, so stdlib-only holds. CI pins
  3.11 / 22 / 0.8.2 / 12.16.1 to remove that drift.
* **No commit, no PR, no `git add`.** The lead owns submission. All five files
  remain untracked; `.kilo/` remains untouched and untracked.
* **A pre-existing `.kilo/` worktree is unrelated.** `git worktree list` also
  shows `/Users/bujin/.local/share/kilo/worktree-pool/snaplist-.../...` (detached
  at `864b849`). It belongs to the Kilo tool's own worktree pool, existed before
  this ticket, and was never created, used or modified by `local_runner.py`.
  This runner's worktrees use a `tempfile.mkdtemp(prefix="snaplist-e0t3-")` path
  under the OS temp dir, and the cleanup proof checks for that exact path only.
* **No `infra/ci/gates/**`, no evidence directory, no sixth file.** A generated
  `__pycache__/` from the mandated `py_compile` self-check is removed before
  handoff; it is gitignored and was never a deliverable.

## Why these commands are not in CI

These are all deliberately **outside** the blocking chain:

| command | why it is not in CI |
|---|---|
| `make golden` | exits 2 by design until the 40 real garment photos + manifest + 20 hand masks land (E0-T1). An empty set must not look like a passing one. |
| `make fetch-models` | downloads 176 MB over the network on every fresh runner. Worse: with torch present, `runtime.probe()` used to select BiRefNet (unimplemented), so the default vision path started refusing on a machine that had not changed — the exact "installing a dependency changes default behaviour" incident recorded in `docs/DEPS.md`. CI must never download model weights. |
| `make evals-strict` | the release gate omits `--allow-inconclusive`, so an unmeasured/empty database exits **3**. On today's empty DB it is guaranteed non-zero. A red-by-default gate is disabled within a week; `evals` (dev/CI gate) is the correct blocking form. |
| `python -m evals.run --suite vision --fail-under-threshold` | E0-T2's acceptance command is **designed to exit 1 today** ("exit 1 today, by design", PLAN.md:220). The E0-T2 vision harness is deliberately not wired into CI; the ticket name's "evals" here refers to the R4-wired `evals.telemetry` dev/CI gate. |
| `make golden-provisional`, `make masks-provisional`, `make seg-iou` | structure checks / regenerated provisional masks / an IoU smoke report whose denominator is algorithmic masks — a signal, not a gate. `seg-iou` reports, it does not gate. |
| `make fetch-sdxl` | 6.9 GB of fp16 weights for `mode=SDXL_INPAINT`, a mode known not to be shippable; must stay an explicit act. |
| `make test` / `make lint` / `make evals` as make targets | GNU Make 3.81 collapses any non-zero recipe exit to 2. CI calls the underlying commands directly to preserve real exit codes (and to keep `pnpm -w test` before pytest). |

`evals_report` is the single non-blocking step, and only because its command
itself always exits 0 — no `continue-on-error`, no `|| true`, no `if: always()`.
