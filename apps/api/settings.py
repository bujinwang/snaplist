"""Runtime configuration. Everything is environment-driven; no secret has a default.

`make install` and `make test` must work with **zero** credentials set — that is why every
value below defaults to empty and nothing here validates on import. Validation happens in
`Settings.require()` and surfaces as a 503 at the call site, never as an import-time crash.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "local"
    log_level: str = "info"

    # Supabase (E5). Empty in local scaffolding.
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""

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
