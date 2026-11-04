"""Runtime configuration. Everything is environment-driven; no secret has a default.

`make install` and `make test` must work with **zero** credentials set — that is why every
value below defaults to empty and nothing here validates on import. Validation happens in
`Settings.require()` and surfaces as a 503 at the call site, never as an import-time crash.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env(name: str, default: Any) -> Any:
    """A field that reads `SNAPLIST_STORAGE_<name>` *or* `STORAGE_<name>`, and a keyword.

    The third choice is the field's own name, so `Settings(storage_dir="/tmp/x")` in a test
    keeps working — a `validation_alias` would otherwise make the keyword silently ignored,
    which is the kind of bug that shows up as "the fixture wrote to my home directory".
    """
    field_name = f"storage_{name.lower()}"
    return Field(
        default=default,
        validation_alias=AliasChoices(f"SNAPLIST_STORAGE_{name}", f"STORAGE_{name}", field_name),
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "local"
    log_level: str = "info"

    # Supabase (E5). Empty in local scaffolding.
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""

    # Object storage (E5-T4). `storage_backend` is "auto" unless an operator forces a backend:
    # auto = Supabase when both Supabase values exist, else the local filesystem, which is what
    # makes `make test` / `make dev` work with an empty .env.
    #
    # Two env spellings are accepted for each storage value — `SNAPLIST_STORAGE_DIR` and
    # `STORAGE_DIR` — because the repo already uses both conventions (`SNAPLIST_DB`,
    # `SNAPLIST_MODEL_DIR` in the Makefile; bare `SUPABASE_URL` here). Accepting both costs one
    # `AliasChoices` and removes a class of "I set the variable and nothing happened" bugs.
    storage_backend: str = _env("BACKEND", "auto")
    storage_bucket: str = _env("BUCKET", "snaplist")
    storage_dir: str = _env("DIR", "")
    # Only a *secret* has no default. Empty means LocalStorage mints a random per-process key,
    # which is honest for a laptop and useless in production — `docs/RUNBOOK.md` §6 says so.
    storage_signing_secret: str = _env("SIGNING_SECRET", "")
    # TTLs are product knobs, not secrets: a 15-minute upload window for a phone on 3G, and an
    # hour for a pack the client caches on disk.
    storage_upload_ttl_seconds: int = _env("UPLOAD_TTL_SECONDS", 900)
    storage_asset_ttl_seconds: int = _env("ASSET_TTL_SECONDS", 3600)
    storage_max_upload_bytes: int = _env("MAX_UPLOAD_BYTES", 20_000_000)

    # Stripe (E8)
    stripe_secret: str = ""
    stripe_webhook_secret: str = ""

    # Model providers (E2/E3/E4) — used by packages/vision, read here only for /readyz reporting
    fashn_api_key: str = ""
    modal_token_id: str = ""
    modal_token_secret: str = ""

    # Product limits, straight from BRIEF.md cost ceilings / plan table.
    free_items_per_month: int = 3
    starter_items_per_month: int = 100
    pro_items_per_month: int = 400

    def missing(self) -> list[str]:
        """Names of unset credentials, for /readyz. Never returns values."""
        keys = (
            "supabase_url",
            "supabase_service_key",
            "stripe_secret",
            "fashn_api_key",
            "modal_token_id",
            "modal_token_secret",
        )
        return [k for k in keys if not getattr(self, k)]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
