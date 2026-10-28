"""SnapList API — application factory.

Scope of this build (see docs/HANDOFF-engineer.md for the honest list):

* `/health`, `/readyz` — real, dependency-free, no credentials required
* `/v1/items`, `/v1/jobs`, `/v1/exports`, `/v1/usage` — real, backed by a `Repository`
  (SQLite in this build, Supabase/Postgres when E5-T4/T5 land) and proof-tested for
  cross-user isolation and quota behaviour
* `/v1/checkout`, `/v1/webhooks/stripe`, `/v1/items/{id}/rerender`, `/v1/items/{id}/on-model`
  — **501**, because they need Stripe / a worker / FASHN. No fake success anywhere.

    make dev            # .venv/bin/python -m uvicorn apps.api.main:app --reload --port 8000

`/health` stays dependency-free so a container probe can pass before any credential exists.
`create_app(repository=..., settings=...)` exists so the test suite can build an app around a
fresh in-memory repository and a fixed `Settings` without touching process env or module state.
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from apps.api import errors
from apps.api.auth import AUTH_MODE_HEADER, resolve_auth_mode
from apps.api.repository import Repository, build_repository
from apps.api.routers import billing as billing_router
from apps.api.routers import exports as exports_router
from apps.api.routers import items as items_router
from apps.api.routers import jobs as jobs_router
from apps.api.routers import usage as usage_router
from apps.api.schemas import CONTRACT_VERSION
from apps.api.settings import Settings, get_settings

API_VERSION = "0.2.0"


def create_app(
    *,
    repository: Repository | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    resolved_settings = settings if settings is not None else get_settings()
    app = FastAPI(
        title="SnapList API",
        version=API_VERSION,
        description=(
            "photo -> listing-ready asset pack. Contract version "
            f"{CONTRACT_VERSION}. Errors always use {{\"error\":{{\"code\",\"message\"}}}}."
        ),
    )
    app.state.settings = resolved_settings
    app.state.repository = repository if repository is not None else build_repository()

    errors.install_error_handlers(app)

    @app.middleware("http")
    async def _stamp_auth_mode(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Tell every `/v1` client which identity mode the process is running.

        `local_dev` means "the caller self-declared its uuid and nothing was verified". A
        client, a QA person or an incident reviewer can see that from a single header instead of
        having to read this file. Configuration decides the value, so it cannot lie per request.
        """
        response = await call_next(request)
        if request.url.path.startswith("/v1"):
            response.headers[AUTH_MODE_HEADER] = resolve_auth_mode(app.state.settings).value
        return response

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "api_version": API_VERSION,
            "contract_version": CONTRACT_VERSION,
        }

    @app.get("/readyz", tags=["ops"])
    def readyz() -> dict[str, object]:
        """Reports which credentials are missing and which auth mode is active.

        Returns credential *names* only, never values. `ready=false` means "a provider-backed
        feature will 501", not "the process is down" — `/health` answers that.
        """
        missing = app.state.settings.missing()
        mode = resolve_auth_mode(app.state.settings)
        return {
            "ready": not missing,
            "missing_credentials": missing,
            "auth_mode": mode.value,
            "unverified_identity": mode.value == "local_dev",
            "storage": type(app.state.repository).__name__,
            "migrations_applied": False,
        }

    app.include_router(items_router.router)
    app.include_router(jobs_router.router)
    app.include_router(exports_router.router)
    app.include_router(usage_router.router)
    app.include_router(billing_router.router)
    return app


app = create_app()
