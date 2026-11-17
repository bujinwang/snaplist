#!/usr/bin/env bash
# Build the scratch Postgres database the api suite runs against.
#
# One script, two callers — `infra/ci/steps.sh setup_pg` (CI) and `make test-pg` (local) — because
# the recipe is the kind of fact that rots when it is written twice. It is destructive on purpose:
# the database named by PGDATABASE is DROPPED and rebuilt from the migrations, so a half-applied
# schema from an earlier run can never be mistaken for a passing one.
#
# Everything is overridable for a non-default local install:
#   PGHOST (default 127.0.0.1)  PGPORT (5432)  PGUSER (postgres)  PGDATABASE (snaplist_pgtest)
#   PGPASSWORD (unset — the CI service uses trust auth)
#
# `ON_ERROR_STOP=1` on every psql invocation: a migration that half-applies and returns 0 is worse
# than one that fails, because the suite would then run against a schema nobody defined.
set -euo pipefail

PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
PGDATABASE="${PGDATABASE:-snaplist_pgtest}"
export PGHOST PGPORT PGUSER PGPASSWORD="${PGPASSWORD:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if ! command -v psql >/dev/null 2>&1; then
  printf 'psql not found on PATH: install the postgresql client\n' >&2
  exit 2
fi

_psql() {
  psql -v ON_ERROR_STOP=1 --no-psqlrc "$@"
}

printf 'pg_setup: rebuilding %s on %s:%s as %s\n' "$PGDATABASE" "$PGHOST" "$PGPORT" "$PGUSER"

# `-d postgres` for the create/drop pair: you cannot drop the database you are connected to.
_psql -d postgres -c "drop database if exists ${PGDATABASE};"
_psql -d postgres -c "create database ${PGDATABASE};"

# The shim stands in for the parts of Supabase our migrations assume (roles, `auth.uid()`, …).
# It must come first: 0001 fails without it, and failing there would look like a migration bug.
_psql -d "$PGDATABASE" -f infra/migrations/verify/local_supabase_shim.sql

for migration in \
  0001_init.sql \
  0002_events.sql \
  0003_events_item_id.sql \
  0004_repository_parity.sql
do
  printf 'pg_setup: applying %s\n' "$migration"
  _psql -d "$PGDATABASE" -f "infra/migrations/$migration"
done

printf 'pg_setup: ready — SNAPLIST_TEST_PG=postgresql://%s@%s:%s/%s\n' \
  "$PGUSER" "$PGHOST" "$PGPORT" "$PGDATABASE"
