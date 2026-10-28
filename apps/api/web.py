"""Small FastAPI helpers shared by the routers.

Two things live here because both are easy to get subtly wrong in five places:

1. **Reading the repository / settings off `app.state`.** The tests build an app with a fresh
   in-memory repository per test; the process builds one per worker. A router that imported a
   module-level singleton would leak state between tests and, worse, between requests.
2. **Parsing a JSON body by hand.** `POST /v1/items` must answer 501 for a multipart upload
   (binary upload is E5-T4) *before* a JSON validator gets a chance to answer 422, and
   `python-multipart` is deliberately not a dependency of this repo. Doing the parse here keeps
   that reasoning in one place.
"""

from __future__ import annotations

import json
from typing import TypeVar

from fastapi import Request
from pydantic import BaseModel, ValidationError

from apps.api import errors
from apps.api.repository import Repository
from apps.api.settings import Settings, get_settings

ModelT = TypeVar("ModelT", bound=BaseModel)

JSON_MEDIA_TYPES = frozenset({"application/json", "text/json", ""})


def repository_of(request: Request) -> Repository:
    repository = getattr(request.app.state, "repository", None)
    if repository is None:  # pragma: no cover - create_app always sets it
        raise errors.unavailable("Storage is not configured on this process.")
    return repository


def settings_of(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


def media_type_of(request: Request) -> str:
    return (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()


def parse_json_body(request: Request, raw: bytes, model: type[ModelT]) -> ModelT:
    """Validate `raw` as `model`, mapping every failure onto the error envelope."""
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise errors.unprocessable(
            "Request body is not valid JSON.",
            details={"body": f"{exc.msg} at line {exc.lineno} column {exc.colno}"},
        ) from exc
    if not isinstance(payload, dict):
        raise errors.unprocessable("Request body must be a JSON object.")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        details = {
            ".".join(str(part) for part in error["loc"]) or "body": str(error["msg"])[:200]
            for error in exc.errors()[:10]
        }
        raise errors.unprocessable(
            "Request body did not validate. See details for the fields.", details=details
        ) from exc
