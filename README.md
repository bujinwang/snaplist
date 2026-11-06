# SnapList

[![CI](https://github.com/bujinwang/snaplist/actions/workflows/ci.yml/badge.svg)](https://github.com/bujinwang/snaplist/actions/workflows/ci.yml)

SnapList helps sellers turn a garment photo into a marketplace-ready listing: shoot one
flat-lay, get it prettified and auto-attributed, optionally virtual-try-on, and export to
marketplaces (eBay / Poshmark / Vinted) with Stripe-billed, quota-tracked usage. The
authoritative product definition lives in [`docs/PRD.md`](docs/PRD.md); the 90-second
first-run experience is specified in [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

> This is a public repository. SnapList carries a deliberate **honesty discipline**: every
> metric in the docs is labelled `MEASURED` / `ASSUMPTION` / `UNMEASURED`, and the telemetry
> gate prints `GATE NOT EVALUATED — n=0. This is NOT a pass.` on an empty database. Do not quote
> unmeasured numbers. See [`docs/QA.md`](docs/QA.md) for the full honesty boundary.

## Repository layout

Monorepo managed with [`uv`](https://docs.astral.sh/uv/) (Python) and `pnpm` (TypeScript).

| Path | What it is |
|---|---|
| `apps/api` | Backend service — event ingestion (`POST /v1/events`), the usage ledger, and image-processing orchestration. |
| `apps/mobile` | Expo / React Native client (early-stage scaffold). See [`apps/mobile/README.md`](apps/mobile/README.md). |
| `apps/web` | Web client. |
| `packages/shared` | Shared TypeScript types and the **frozen-contract** schema source (`schemas.ts`, `analytics.ts`). |
| `evals` | Evaluation harness and the telemetry gate (`evals.telemetry`). See [`evals/README.md`](evals/README.md). |
| `infra/migrations` | SQL migrations for Postgres / Supabase, plus the local shim and RLS/constraint verify scripts. |
| `infra/ci` | CI step definitions shared by GitHub Actions and a local equivalence runner. See [`infra/ci/README.md`](infra/ci/README.md). |
| `docs` | PRD, plan, frozen contracts, ADRs, QA, runbook, metrics. |

## Prerequisites

- **Python 3.11** with [`uv`](https://docs.astral.sh/uv/) (the project pins `>=3.11,<3.12`).
- **Node 22** with `pnpm` (CI pins Node 22 / pnpm 12).
- A Postgres instance is **optional** for local development; the telemetry gate defaults to the
  local SQLite database. The read-only Postgres reader is an optional dependency group (`pg`).

## Quick start

```bash
# Python dependencies
uv sync
# JavaScript dependencies
pnpm install

# Frozen-contract gate: docs/CONTRACTS.md <-> code constants
make contract
# Lint (ruff + tsc) and tests (JS + Python, incl. contract parity + telemetry suites)
make lint
make test
# Telemetry gate — the dev/CI form. On an empty database it prints
# "GATE NOT EVALUATED — n=0. This is NOT a pass." and exits 0 by design.
make evals
```

`make evals-strict` is the release form: it omits `--allow-inconclusive`, so an unmeasured
/ empty database exits non-zero and is **not** a merge gate. `make golden` (vision golden set)
is intentionally not wired into CI until the 40 real garment photos land (E0-T1).

## Engineering practices

- **Frozen contracts.** [`docs/CONTRACTS.md`](docs/CONTRACTS.md) is the contract; the CI step
  `make contract` (`infra/ci/check_contracts.py`) verifies code↔document parity with zero
  hardcoded business constants. Changing a contract requires an ADR **and** synchronized edits on
  both code sides (`apps/api/schemas.py`, `packages/shared/src/schemas.ts`,
  `packages/shared/src/analytics.ts`). See the "Changing a frozen contract" section in
  [`infra/ci/README.md`](infra/ci/README.md).
- **Telemetry gate.** Latency and failure-mix gates live in `evals.telemetry`; their honesty
  rules (append-only source, no DDL from the report path, INTACT/DEGRADED/UNVERIFIED
  attribution) are documented in [`docs/QA.md`](docs/QA.md) and `docs/ADR-009-*`.
- **CI.** `.github/workflows/ci.yml` (one job, `ci`) runs on every PR and push and is the merge
  gate. Action `uses:` entries are immutable-SHA pinned.

## Documentation index

- Product & plan: [`docs/PRD.md`](docs/PRD.md), [`docs/PLAN.md`](docs/PLAN.md), [`docs/BRIEF.md`](BRIEF.md)
- Contracts & architecture: [`docs/CONTRACTS.md`](docs/CONTRACTS.md), [`docs/ADR-001-stack.md`](docs/ADR-001-stack.md) and the numbered ADRs
- Quality & operations: [`docs/QA.md`](docs/QA.md), [`docs/RUNBOOK.md`](docs/RUNBOOK.md), [`docs/METRICS.md`](docs/METRICS.md)
- Onboarding: [`docs/ONBOARDING.md`](docs/ONBOARDING.md)

## Contributing

Open an issue or PR. Keep contracts frozen (ADR for any change), run `make contract lint test`
before pushing, and never commit secrets — `.env` is gitignored; copy `.env.example` to `.env`
and fill values locally.
