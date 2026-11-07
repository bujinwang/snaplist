#!/usr/bin/env bash
# E0-T3 + E0-T4 CI step definitions — the SINGLE source of truth for this pipeline.
#
# Both .github/workflows/ci.yml and infra/ci/local_runner.py call
# `bash infra/ci/steps.sh <name>`; command text lives here only, never in the
# workflow YAML or in the runner. ci.yml drives the steps one-by-one; the local
# runner drives `all`. Both therefore execute the same functions in the same
# order, which is what "the wiring exists" actually means.
#
# Command <-> Makefile target map (commands are invoked DIRECTLY, never via make):
#   toolchain_probe  (no target)    python3/node/pnpm/uv --version + git rev-parse HEAD
#   contract_gate    (no target)    python3 infra/ci/check_contracts.py
#   install          make install   uv sync --frozen ; pnpm install --frozen-lockfile
#   lint_py          make lint 1/2  .venv/bin/python -m ruff check .
#   lint_ts          make lint 2/2  pnpm -w lint
#   test_js          make test 1/2  pnpm -w test
#   test_py          make test 2/2  .venv/bin/python -m pytest
#   evals            make evals      .venv/bin/python -m evals.telemetry --since 7d --gate --allow-inconclusive
#   evals_report     make evals-report  .venv/bin/python -m evals.telemetry --since 7d
#   gates_report    (no target)    strict rc from both E0-T4 tools; rc=2 -> ::warning:: only
#   dirty_check      (no target)    git status --porcelain --untracked-files=all
#
# WHY NO `make`: GNU Make 3.81 (macOS stock) collapses ANY non-zero recipe exit
# code to 2. Routing through make would destroy evals.telemetry's real 0/1/2/3
# exit codes and hide which gate actually fired. Every blocking step therefore
# calls the underlying command directly so the true return code survives.
#
# ORDER IS LOAD-BEARING: `pnpm -w test` must precede pytest (Makefile:60-63)
# because it builds packages/shared/dist, which the fail-closed python parity
# test reads. `contract_gate` is deliberately step 2, BEFORE install: it is pure
# stdlib, needs no venv/node_modules and no network, so a broken frozen contract
# fails the job in seconds and cannot be affected by anything an install might
# change (see "Why these commands are not in CI" in README.md).
set -euo pipefail

readonly STEP_ORDER=(
  toolchain_probe
  contract_gate
  install
  lint_py
  lint_ts
  test_js
  test_py
  evals
  evals_report
  gates_report
  dirty_check
)

_emit() {
  printf 'STEP %s: %s\n' "$1" "$2"
}

step_toolchain_probe() {
  _emit toolchain_probe 'python3 --version && node --version && pnpm --version && uv --version && git rev-parse HEAD'
  python3 --version \
    && node --version \
    && pnpm --version \
    && uv --version \
    && git rev-parse HEAD
}

step_contract_gate() {
  _emit contract_gate 'python3 infra/ci/check_contracts.py'
  python3 infra/ci/check_contracts.py
}

step_install() {
  _emit install 'uv sync --frozen && pnpm install --frozen-lockfile'
  uv sync --frozen && pnpm install --frozen-lockfile
}

step_lint_py() {
  _emit lint_py '.venv/bin/python -m ruff check .'
  .venv/bin/python -m ruff check .
}

step_lint_ts() {
  _emit lint_ts 'pnpm -w lint'
  pnpm -w lint
}

step_test_js() {
  _emit test_js 'pnpm -w test'
  pnpm -w test
}

step_test_py() {
  _emit test_py '.venv/bin/python -m pytest'
  .venv/bin/python -m pytest
}

step_evals() {
  local tmp rc=0
  _emit evals '.venv/bin/python -m evals.telemetry --since 7d --gate --allow-inconclusive'
  # No pipe anywhere: the gate writes to a temp file outside the repo, we cat it
  # for the log, then `return` the ORIGINAL rc. `set -o pipefail` cannot rescue a
  # consumer that swallows the producer's status, so we simply avoid pipes.
  tmp="$(mktemp "${TMPDIR:-/tmp}/snaplist-evals.XXXXXX")" || return 1
  .venv/bin/python -m evals.telemetry --since 7d --gate --allow-inconclusive >"$tmp" 2>&1 || rc=$?
  cat "$tmp"
  if grep -q 'GATE NOT EVALUATED' "$tmp"; then
    printf '%s\n' '::warning:: CI green != gate passed — evals.telemetry printed "GATE NOT EVALUATED" (n=0, --allow-inconclusive downgrade). This is NOT a pass.'
  fi
  rm -f "$tmp"
  return "$rc"
}

step_evals_report() {
  _emit evals_report '.venv/bin/python -m evals.telemetry --since 7d'
  .venv/bin/python -m evals.telemetry --since 7d
}

# E0-T4 wiring. NON-BLOCKING REPORT step: its rc means "both reports were rendered",
# NEVER "both gates passed". Both tools run in their strictest form; only rc=2 together
# with the tool's own NOT-A-PASS marker is downgraded to a visible annotation.
# rc=1 (regression / over budget) and rc>=3 (unexpected) still fail the job.
# Upgrade to a blocking gate: docs/QA.md §13.6.
step_gates_report() {
  local rg_out cost_out rg_rc=0 cost_rc=0 bad=0
  _emit gates_report '.venv/bin/python infra/ci/gates/regression_gate.py ; .venv/bin/python infra/ci/gates/cost_dashboard.py --fail-on-unmeasured'
  rg_out="$(mktemp "${TMPDIR:-/tmp}/snaplist-e0t4-rg.XXXXXX")" || return 1
  cost_out="$(mktemp "${TMPDIR:-/tmp}/snaplist-e0t4-cost.XXXXXX")" || { rm -f "$rg_out"; return 1; }

  # No pipe, no swallow construct: capture the REAL rc of each tool. Temp files live
  # outside the repo so dirty_check stays clean (same pattern as step_evals).
  .venv/bin/python infra/ci/gates/regression_gate.py >"$rg_out" 2>&1 || rg_rc=$?
  .venv/bin/python infra/ci/gates/cost_dashboard.py --fail-on-unmeasured >"$cost_out" 2>&1 || cost_rc=$?
  cat "$rg_out"
  cat "$cost_out"

  # Fail-closed triage: exactly ONE code is downgradable, and only with the tool's own
  # marker. This is NOT "accept any non-1 code".
  if [ "$rg_rc" -eq 2 ] && grep -q 'GATE INCONCLUSIVE' "$rg_out"; then
    printf '%s\n' '::warning:: gates_report: regression_gate.py rc=2 — GATE INCONCLUSIVE, this is NOT a pass. CI green means "report rendered", not "gate passed". See docs/QA.md §13.6.'
  elif [ "$rg_rc" -ne 0 ]; then
    printf 'gates_report: regression_gate.py rc=%s is not downgradable — failing the job\n' "$rg_rc" >&2
    bad=1
  fi
  if [ "$cost_rc" -eq 2 ] && grep -q 'COST BUDGET NOT EVALUATED' "$cost_out"; then
    printf '%s\n' '::warning:: gates_report: cost_dashboard.py --fail-on-unmeasured rc=2 — COST BUDGET NOT EVALUATED, this is NOT a pass. The <= $0.03/item verdict was never computed. See docs/QA.md §13.6.'
  elif [ "$cost_rc" -ne 0 ]; then
    printf 'gates_report: cost_dashboard.py rc=%s is not downgradable — failing the job\n' "$cost_rc" >&2
    bad=1
  fi
  rm -f "$rg_out" "$cost_out"
  return "$bad"
}

step_dirty_check() {
  _emit dirty_check 'git status --porcelain --untracked-files=all'
  local status_out line bad=0
  # `-uall` is load-bearing. Plain `git status --porcelain` COLLAPSES untracked
  # directories into `?? infra/ci/`, which silently tolerates junk dropped
  # inside that directory (QA proved this with qa-garbage.bin). With `-uall`
  # every untracked file gets its own line, so an exact filename whitelist can
  # reject anything that is not one of this ticket's five deliverables.
  status_out="$(git status --porcelain --untracked-files=all)"
  # A fresh GitHub Actions checkout is clean (the five files are tracked once
  # committed), so this prints NOTHING and the step passes on the empty set.
  #
  # `?? .kilo/` is a long-lived, untracked scratch dir the lead owns: it is
  # never committed and never present in a GH checkout. Under `-uall` git
  # expands it to per-file lines (`?? .kilo/plans/<file>.md`), so it is allowed
  # as a PREFIX, not as one exact line. Asserting empty output locally would be
  # a 100% false red. Everything under `.kilo/` stays tolerated — that is the
  # lead's explicit carve-out, recorded as a named residual in README.md.
  #
  # The five deliverables are allowlisted by EXACT filename. While uncommitted
  # they appear as `??` lines; once committed they disappear from status. Any
  # other line fails: a stray junk file, a modified tracked file, or a build
  # artefact .gitignore forgot (e.g. a stray dist/).
  while IFS= read -r line; do
    if [ -z "$line" ]; then
      continue
    fi
    case "$line" in
      '?? .kilo/'*)
        ;;
      '?? .github/workflows/ci.yml'| \
      '?? infra/ci/steps.sh'| \
      '?? infra/ci/check_contracts.py'| \
      '?? infra/ci/local_runner.py'| \
      '?? infra/ci/README.md')
        ;;
      *)
        printf 'dirty_check: unexpected status line: %s\n' "$line" >&2
        bad=1
        ;;
    esac
  done <<< "$status_out"
  return "$bad"
}

run_all() {
  local step rc
  for step in "${STEP_ORDER[@]}"; do
    rc=0
    "step_${step}" || rc=$?
    printf 'STEP %s rc=%s\n' "$step" "$rc"
    if [ "$rc" -ne 0 ]; then
      return "$rc"
    fi
  done
  return 0
}

main() {
  local action="${1:-}"
  if [ -z "$action" ]; then
    printf 'usage: bash infra/ci/steps.sh <step|all>\n' >&2
    return 2
  fi
  case "$action" in
    all) run_all ;;
    toolchain_probe|contract_gate|install|lint_py|lint_ts|test_js|test_py|evals|evals_report|gates_report|dirty_check)
      "step_${action}"
      ;;
    *)
      printf 'unknown step: %s\n' "$action" >&2
      return 2
      ;;
  esac
}

main "$@"
