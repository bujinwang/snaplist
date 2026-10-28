# DEPS.md — every third-party dependency and why it is here

Populated by whoever adds a dependency, in the same commit as the manifest change. A row
without a reason is a row nobody can defend in a review. Versions below are what is actually
installed in the lockfiles right now, not the range in the manifest.

Python resolves through `uv` (`pyproject.toml` + `uv.lock`). JS resolves through `pnpm`
(`package.json` + `pnpm-lock.yaml`).

## Python

| name | version | licence | reason |
|---|---|---|---|
| fastapi | 0.115.14 | MIT | the API framework fixed by BRIEF.md; gives us OpenAPI for free so mobile/growth can code against `/v1` before it exists |
| pydantic | 2.14.1 | MIT | the contract. VLM output is validated against `ItemAttributes`; `extra="forbid"` is what makes a schema change loud |
| pydantic-settings | 2.16.2 | MIT | env-driven config with no secret defaults; `make test` must pass with zero credentials |
| uvicorn | 0.30.6 | BSD-3-Clause | ASGI server for `make dev`; nothing exotic, ships with FastAPI docs |
| python-dotenv | 1.2.4 | BSD-3-Clause | transitive via pydantic-settings (`env_file=".env"`); listed because a secret-handling dep deserves a row |
| pytest | 8.4.2 | MIT | test runner for `make test`; fixture-based, no plugin sprawl |
| httpx | 0.27.2 | BSD-3-Clause | required by `fastapi.testclient`; also the client the eval harness will use for real provider calls |
| ruff | 0.6.9 | MIT | `make lint` for python. One binary for lint+format, no black/isort/flake8 stack |

## JavaScript / TypeScript

| name | version | licence | reason |
|---|---|---|---|
| zod | 3.23.8 | MIT | the zod mirror of `ItemAttributes` in `packages/shared`, and the only runtime validation mobile trusts |
| typescript | 5.5.4 (`packages/shared`) | Apache-2.0 | compiles `@snaplist/shared` and doubles as its linter (`tsc --noEmit`) |
| typescript | 5.3.3 (`apps/mobile`) | Apache-2.0 | pinned by Expo SDK 51's own template; do not bump independently of Expo |
| @types/node | 20.19.43 | MIT | types for `node:test`, `node:assert` and `process` in the shared-package tests |
| expo | 51.0.39 | MIT | the mobile framework fixed by BRIEF.md (SDK 51 = RN 0.74, the last LTS-ish line we can pin confidently) |
| expo-status-bar | 1.12.1 | MIT | status bar styling on the scaffold screen; comes from the Expo template |
| react | 18.2.0 | MIT | peer of React Native 0.74 |
| react-native | 0.74.5 | MIT | the renderer; version dictated by Expo SDK 51 |
| @types/react | 18.2.79 | MIT | type checking for the sole `.tsx` file |

## Not dependencies, on purpose

| thing | why not |
|---|---|
| eslint / prettier | `tsc --noEmit` plus `ruff` already fail the build on real defects. A formatter war is not a v1 problem. Revisit if the diff noise gets in the way. |
| jest / vitest | `node --test` is in the runtime and needs no config or transform for our compiled output |
| celery / redis | explicitly excluded by BRIEF.md; the `jobs` table does this |
| modal | installed in the Modal image, not in the API image. `infra/modal_app.py` is never imported by the test suite |
| torch / diffusers / SAM weights | belong to E1–E4. When they land they must be added here **with** their measured `$/item` impact |

## How to add a row

1. Add the dependency to the correct manifest (`pyproject.toml` or the package's `package.json`).
2. Install so the lockfile updates (`uv sync` / `pnpm install`).
3. Add the row here with the version that actually resolved, and a reason a stranger can audit.
4. If the licence is not MIT/BSD/Apache-2.0/ISC, flag it to the legal role before merging.
