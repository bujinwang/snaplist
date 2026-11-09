# `infra/ci/gates` — E0-T4: regression gate + cost dashboard

Two stdlib-only scripts, one baseline file, one test suite. No dependencies, no network, no
subprocess, no provider SDK. Both tools exist to answer one question honestly: **is that a
number, or is it just green?**

| file | what it does |
|---|---|
| `regression_gate.py` | compares the metrics `evals.run.measurements()` produces against `baseline.json`; exit `0` compared-and-held, `1` regression, `2` inconclusive |
| `cost_dashboard.py` | parses per-stage `$/item` and sample counts out of `evals/reports/cost.md`; renders `UNMEASURED` when there is nothing to parse; exit `0` rendered, `1` over budget, `2` not evaluable |
| `baseline.json` | the bar. **Shipped explicitly empty** — see "Why the baseline is empty" below |
| `tests/` | 65 `unittest` cases, stdlib only |

`docs/QA.md` §13 is the human-readable dashboard; `cost_dashboard.py --update-doc` writes it.

## Commands

```sh
# the gate, as shipped: exits 2 with the reason and the command that would create a baseline
.venv/bin/python infra/ci/gates/regression_gate.py

# create a baseline on purpose (stamps git sha + timestamp + sets + mask sources)
.venv/bin/python infra/ci/gates/regression_gate.py --update-baseline

# a pipeline-regression baseline over machine-made references is a deliberate act
.venv/bin/python infra/ci/gates/regression_gate.py --update-baseline --allow-machine-references

# the dashboard: print only
.venv/bin/python infra/ci/gates/cost_dashboard.py
.venv/bin/python infra/ci/gates/cost_dashboard.py --fail-on-unmeasured

# regenerate the table in docs/QA.md §13 (between the two E0-T4 markers)
.venv/bin/python infra/ci/gates/cost_dashboard.py --update-doc

# tests
.venv/bin/python -m unittest discover -s infra/ci/gates/tests -t infra/ci/gates/tests -v
```

## Exit codes

| code | `regression_gate.py` | `cost_dashboard.py` |
|---|---|---|
| `0` | every baselined metric was compared and none fell below `baseline - tolerance` | the table was rendered (report mode always exits 0 when `cost.md` is absent — absence is a fact to report) |
| `1` | at least one metric fell below `baseline - tolerance` | `--fail-on-unmeasured`, every stage measured, and `E1+E2+E3` is **over** `$0.03/item` |
| `2` | nothing was compared (no/empty/malformed baseline) **or** a baselined metric is `UNMEASURED`; also every `--update-baseline` refusal and every `--update-doc` refusal | `--fail-on-unmeasured` and something is `UNMEASURED`; also any `--update-doc` refusal (missing file or missing markers) |

Two rules that are not negotiable, and are covered by tests:

* **`0` requires at least one comparison.** `compared == 0` is hard-wired to `2`. A gate that
  goes green because it read nothing is the failure mode this whole directory exists to remove.
* **`2` is never "fine, it's just dev".** If CI treats `2` as a pass, the gate is decorative.
  Wire it with the real code visible (`python …/regression_gate.py`), not through a `|| true`.

## `baseline.json` schema (version 1)

```json
{
  "schema_version": 1,
  "generated_at": "2026-11-06T23:32:21+00:00",
  "git_sha": "1beee75e5df0",
  "tool": "infra/ci/gates/regression_gate.py --update-baseline",
  "default_tolerance": 0.01,
  "sets": {"<metric>": "<set name the number came from>"},
  "mask_sources": {"<metric>": ["ai:…", "algorithmic:…", "ai-assisted:sam2+point-reviewed"]},
  "metrics": {
    "<metric name>": {
      "value": 0.9299,
      "n": 20,
      "set": "golden",
      "provenance": "the harness's own note for this number, verbatim",
      "tolerance": 0.01
    }
  }
}
```

* `metric name` must equal the key `evals.run.measurements()` produces. That key is the join,
  there is no mapping table.
* `n`, `set` and `provenance` are **required** — a baseline that does not say how many fixtures
  produced a number cannot be audited, and an unauditable bar is a bar nobody should trust.
* `tolerance` is the maximum drop that is not a regression. Per-metric, because a 0.01 dip in a
  mean over 20 masks and a 0.01 dip in a 0.99 SSIM are not the same event. Missing ⇒
  `default_tolerance` (default `0.01`, overridable with `--tolerance`).
* Any malformed record invalidates the whole file (`2`). Skipping a bad row would silently
  compare fewer metrics than the author believed, and fewer compared rows reads as PASS.
* `sets` and `mask_sources` are **required** and must enumerate **exactly** the same metric names
  as `metrics`. A hand-edit that deletes a row from `metrics` while leaving the other two maps
  behind is refused (`2`) rather than gating one metric fewer — the same false green as
  "compared nothing", arriving from the other direction. Ungating a metric has to be an explicit
  act (`--update-baseline --allow-dropped-metrics`), never a quiet edit. **Residual:** an edit
  that removes a metric from all three maps consistently is indistinguishable from an
  intentionally smaller baseline; no file-level check can see intent, so git history is the audit
  trail that remains.
* Metrics that are measured but absent from the baseline print `NOT IN BASELINE` and do not fail
  the run: adding a metric must not require a baseline edit in the same commit, or people stop
  adding metrics.

## `--update-baseline` refusals

1. **Nothing measured** → `2`, no file written.
2. **Non-finite value** (`nan`, `inf`) → `2`. `nan` compares false against everything and would
   pass forever.
3. **Machine-made references**, unless `--allow-machine-references` → `2`, no file written. The
   markers are `not hand-drawn`, `algorithmic`, `ai:`, `ai-assisted:`, `provisional` — the same
   vocabulary `evals/mask_provenance.py` exports for `evals/run.py`, `evals/tools/seg_iou.py`
   and the validator, so the guard cannot disagree with the report. **`ai:` and `ai-assisted:`
   do not conflict:** the frozen manifest still carries old `ai:*` values, while
   `ai-assisted:sam2+point-reviewed` does not contain `ai:` (the next character is `-`), so
   dropping the new marker would let that ADR-010 tier slip past the guard. ADR-010 keeps three
   tiers — hand-drawn / AI-assisted point-reviewed / machine-made — and U1 stays UNMET while
   the point-review register is `0/20` and its threshold is `UNSET`.
4. **Dropping a metric the previous baseline had**, unless `--allow-dropped-metrics` → `2`. A
   transient measurement failure would otherwise delete coverage permanently, and every later
   run would look green.
5. **An existing baseline that failed validation**, unless `--allow-dropped-metrics` → `2`. A file
   that cannot be parsed is not a baseline, so rebuilding over it would replace however many rows
   it claimed to have with this run's rows. The shipped empty placeholder is *not* this case — it
   validates, and `--update-baseline` fills it as the first command in this README says.

## Why the baseline is empty

`baseline.json` ships with `"metrics": {}`. That is the delivered decision, not an oversight.

The only two metrics measurable in this checkout are:

| metric | value | n | why it is not a bar |
|---|---|---|---|
| segmentation mean IoU | 0.9299 | 20 | references are `ai:google-ai-mode-nano-banana` + `algorithmic:matting`, not hand masks — a pipeline check, not the E1-T4 DoD number (worst case 0.6053) |
| prettify SSIM (inside mask) | 0.9936 | 9 | SSIM of the output against its own input on fixtures with **synthetic** labels; 1.0 means nothing changed, so this is a destruction budget, not a cleaning score |

Enshrining either would convert a self-referential measurement into release evidence, and the
next person would read `PASS` without ever seeing the caveat. The gate therefore exits `2` and
says so, in the same output, with the command that would create a real baseline.

## `evals/reports/cost.md` format contract

The dashboard's only input. A Markdown table whose header names a cost column:

```markdown
| stage | $/item | n | notes |
|---|---|---|---|
| E1 segmentation | $0.0012 | 20 | provider rate × measured tokens |
| E2 rectify + prettify | $0.0008 | 20 | |
| E3 attributes + dedupe | `0.0004` | 20 | |
```

* Stage cells: `E1`/`E2`/`E3`/`E4`, or a name (`segmentation`, `prettify`, `attributes`,
  `dedupe`, `on-model`, `vton`). Anything else is **ignored with a printed note** — a wrong
  bucket is a wrong number with a confident label on it.
* Cost cells: `$0.0042`, `` `0.0042` ``, `0.0042 (± …)`. `UNMEASURED`, `n/a`, `—`, `-` and an
  empty cell all parse as *not a number*, which is a first-class value here.
* `E1+E2+E3` is summed **only** when all three are measured. A partial sum is not a per-item
  cost, so it is `UNMEASURED` and the `<= $0.03/item` verdict is not computed.
* `E4` is listed for context and excluded from the total: it is priced per *render*, its own DoD
  target is different, and it is BLOCKED in this build (`packages/vision/BLOCKED.md`).

## One thing CI must get right

`cost_dashboard.py` **exits 0 in report mode even when the verdict is `FAIL`** — a report is not a
gate. Any CI wiring must pass `--fail-on-unmeasured`, or a breached `$0.03/item` budget prints
`FAIL` and returns green. The same grade of claim as the regression gate's `2`: treat it as
*not a pass*.

E0-T4 wiring: `infra/ci/steps.sh`'s `gates_report` step invokes `cost_dashboard.py`
**always with `--fail-on-unmeasured`**, so the report-mode 0-on-`FAIL` path cannot make CI
green by itself. In this checkout that step is a **non-blocking report step**, not a gate: both
E0-T4 tools run in their strictest form, their real rcs are preserved and both full reports are
printed, and only `rc=2` together with the tool's own `GATE INCONCLUSIVE` /
`COST BUDGET NOT EVALUATED` marker is downgraded to a `::warning::` that literally says
"this is NOT a pass". `rc=1`, `rc>=3` and `rc=2` without that marker still fail the job. Green
means "both reports were rendered", not "either gate passed" — see `docs/QA.md §13.6`.

## Honest status

* **`evals/reports/cost.md` does not exist.** No stage has ever run against a paid provider
  (E4 on-model is a stub; `FASHN_API_KEY` / `MODAL_TOKEN` absent) and there are no production
  `usage_events`, so no `$/item` has ever been billed. Every cost cell is `UNMEASURED` and the
  `<= $0.03/item` verdict is **UNREACHABLE, not passing**.
* **The regression gate exits `2` today** for the same reason the baseline is empty. There is no
  passing state to show, and inventing one is out of scope for this ticket.
* **A measured metric can still be `UNMEASURED` next run** — the segmentation weights live
  outside the repo (`make fetch-models`, 176 MB). The gate reports that as `2`, never as "held".
* **These tests are collected by `pytest` now — CLOSED.** `pyproject.toml`'s `testpaths` now
  lists `infra/ci/gates/tests` alongside `apps/api/tests`, `packages/vision/tests`,
  `evals/tests` and `evals/telemetry/tests`, so a plain `pytest` collects all 65 cases
  (793 = 728 + 65). The explicit `unittest discover -s infra/ci/gates/tests -t
  infra/ci/gates/tests` command above still works as a legacy runner. `.github/workflows/ci.yml`
  now calls both tools from the `gates_report` step, with `regression_gate.py`'s `2` treated as
  *not a pass* rather than as green; see `docs/QA.md §13.6` for the new named residual.
* **The gate imports the harness in-process**, so it inherits whatever `measurements()` does:
  with the segmentation weights present that means one local ONNX inference pass over the
  masked set. The gate itself adds no I/O — no subprocess, no network, no provider call, and it
  writes nothing under `evals/`.

## Named residual (E0-T4 wiring)

Both tools are wired into `.github/workflows/ci.yml` via the `gates_report` step, but as a
**non-blocking report step**, not a gate: `regression_gate.py` exits `2` (empty baseline) and
`cost_dashboard.py --fail-on-unmeasured` exits `2` (`evals/reports/cost.md` absent), and only
those two `2`s are downgraded to `::warning::` annotations that literally say
"this is NOT a pass". CI green means "both reports were rendered", not "either gate passed".
The residual is deliberately temporary; the three mechanical upgrade conditions (U1/U2/U3),
the exact lines to change, and the step rename to `regression_cost_gates` all live in
**`docs/QA.md §13.6`**.
