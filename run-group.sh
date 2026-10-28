#!/usr/bin/env bash
# run-group.sh — dispatch the SnapList expert group sequentially through the WorkBuddy CLI.
#
#   ./run-group.sh                 # all 10 roles, in order, stop on first failure
#   ./run-group.sh vision backend  # only these roles
#   AGENT=whale ./run-group.sh     # use a different installed coding agent
#
# Prereqs: `coder -y wb "hi"` works (WorkBuddy CLI must be logged in: run codebuddy, type /login).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="${AGENT:-wb}"
LOGS="$ROOT/logs"; mkdir -p "$LOGS"
ORDER=(lead product vision backend vton growth mobile legal qa integrator)

role_file() {
  case "$1" in
    lead) echo 00-lead.md ;;      product) echo 01-product.md ;;
    vision) echo 02-vision.md ;;  vton) echo 03-vton.md ;;
    backend) echo 04-backend.md ;;mobile) echo 05-mobile.md ;;
    growth) echo 06-growth.md ;;  qa) echo 07-qa.md ;;
    legal) echo 08-legal.md ;;    integrator) echo 09-integrator.md ;;
  esac
}

TARGETS=("$@")
[[ ${#TARGETS[@]} -eq 0 ]] && TARGETS=("${ORDER[@]}")

# Fail fast on a dead CLI instead of burning ten runs on "Authentication required".
probe="$(cd "$ROOT" && coder -y "$AGENT" "Reply with exactly: READY" 2>&1)"
if grep -qiE 'authentication required|not logged in|invalid api key' <<<"$probe"; then
  echo "ABORT: $AGENT is not authenticated."
  echo "Fix: open Terminal, run: coder $AGENT \"hi\"   -> then type /login   (or export CODEBUDDY_API_KEY)"
  exit 2
fi
grep -qi 'READY' <<<"$probe" || echo "WARN: probe returned no READY marker; continuing anyway."

for r in "${TARGETS[@]}"; do
  f="$(role_file "$r")"
  [[ -n "$f" && -f "$ROOT/prompts/$f" ]] || { echo "unknown role: $r"; exit 2; }
  log="$LOGS/$(date +%H%M%S)-$r.log"
  echo "=== $r  ($f)  -> $log"
  ( cd "$ROOT" && coder -y "$AGENT" "$(cat "prompts/$f")" ) 2>&1 | tee "$log"
  rc=${PIPESTATUS[0]}
  if [[ $rc -ne 0 ]]; then echo "STOP: $r exited $rc. See $log"; exit $rc; fi
  if grep -qiE 'authentication required|not logged in' "$log"; then echo "STOP: auth lost during $r"; exit 2; fi
  if [[ -f "$ROOT/BLOCKED.md" ]]; then
    echo "STOP: $r wrote BLOCKED.md"; sed -n '1,12p' "$ROOT/BLOCKED.md"; exit 3
  fi
done
echo "Group run complete. Review docs/STATUS.md and docs/DEFECTS.md."
