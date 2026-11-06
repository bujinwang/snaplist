#!/usr/bin/env bash
# E0-T3 CI step definitions — the SINGLE source of truth for this pipeline.
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
    toolchain_probe|contract_gate|install|lint_py|lint_ts|test_js|test_py|evals|evals_report|dirty_check)
      "step_${action}"
      ;;
    *)
      printf 'unknown step: %s\n' "$action" >&2
      return 2
      ;;
  esac
}

main "$@"
