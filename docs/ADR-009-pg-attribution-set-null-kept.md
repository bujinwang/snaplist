# ADR-009: Retain `usage_events` SET NULL FKs; ship a read-only Postgres reader

- **Status:** Accepted
- **Date:** 2026-11-05
- **Supersedes / relates:** ADR-008 (no-FK design on `events.item_id` / `events`↔`jobs`), QA.md §5.2 and §10 (residuals R1, R2, R3)

## 1. Context

E0-T5 is the telemetry gate that reads real analytics events from the local dev SQLite database
and classifies refund attribution via the `usage_events` ledger self-join. Three residuals were
carried in QA.md §10:

- **R1** — `failure_mix_unattributable`: items that carry events but have no surviving `jobs`
  row (ADR-008 no-FK design + `jobs` `ON DELETE CASCADE`). This is an honest, permanent residual
  and is accepted as such.
- **R2** — `0001_init.sql:231-232` declares `on delete set null` FK actions on
  `usage_events.item_id` / `usage_events.job_id`. These two FKs sit outside the E0-T5
  allowed-paths whitelist, and they are not merely a fixture artefact: they are a *real producer*
  of NULL ledger join keys in production (the SET NULL action fires when the referenced row is
  deleted). Those NULLs are exactly what the gate classifies as DEGRADED (evidence absence).
- **R3** — the gate shipped only a SQLite reader, so `excluded_refunded` was UNMEASURED on
  Postgres/Supabase by construction (per the SET NULL FKs above).

Two decisions had to be made: (a) whether to keep the SET NULL FKs, and (b) whether/how to add a
Postgres reader without breaking the default `uv sync` (which must work without `psycopg`) and
without changing any schema or write path.

## 2. Decision

1. **Keep the `on delete set null` FK actions on `usage_events.item_id` / `job_id` unchanged.**
   They are outside the E0-T5 whitelist, they drive honest DEGRADED attribution (not corruption),
   and changing them is a schema/write-path change this ticket is explicitly forbidden from
   making. `infra/migrations/0001_init.sql:231-232` is left as-is.

2. **Ship `PostgresEventSource` as the OPTIONAL `pg` PEP 735 dependency group** (currently
   `psycopg[binary]>=3.2,<4`), selected only when `SNAPLIST_PG_DSN` is set. The module
   (`evals/telemetry/pg_source.py`) imports `psycopg` lazily inside `_connect`, so a default
   `uv sync` never imports it and the gate keeps running off SQLite with no `psycopg` installed.
   `select_source()` (in `evals/telemetry/source.py`) is the single env-driven factory.

3. **Reuse the SAME attribution logic as SQLite.** `PostgresEventSource` calls the shared
   `_materialize()` / `_classify_attribution()`, so a SET NULL NULL join key is DEGRADED on
   Postgres exactly as on SQLite, and the `excluded_refunded` count is now MEASURED on Postgres
   too. `UNVERIFIED` remains reserved for the not-yet-measured-under-real-RLS case.

## 3. Consequences

- `excluded_refunded` is now measurable on Postgres (R3 closed). The read path is strictly
  read-only (`default_transaction_read_only=on`, no writes, no DDL, no id coercion).
- NULL ledger join keys remain classified as **DEGRADED** (evidence absence), never as 3b data
  corruption — on both backends.
- The SET NULL FKs are retained by design (R2 closed); no migration is proposed here.
- **Follow-up ADR required:** analytics needs a decision on whether a deletion-proof attribution
  key is required before quoting `excluded_refunded` on Postgres under the *real* RLS role. Until
  that is measured under the production role, `UNVERIFIED` is the honest status for that path.
  R1 is accepted as a permanent, intentional honesty residual (no schema change, no write path).

See QA.md §5.2 (SET NULL attribution measurement) and §10 (R1 / R2 / R3 CLOSED).
