"""One error envelope for every non-2xx response.

`docs/CONTRACTS.md` §4 fixes the shape:

    {"error": {"code": "quota_exceeded", "message": "...", "retry_after_seconds": null,
               "details": {"plan": "free"}}}

FastAPI's default `{"detail": ...}` body would be a second contract, and a mobile client that
has to parse two error shapes is a client that will parse the wrong one at 2am. So every
exception — ours, FastAPI's validation error, a bare `HTTPException`, an unexpected crash —
is funnelled through this module.

No new `ErrorCode` members are added here: the enum in `apps/api/schemas.py` is frozen at
seven values. A 409 (illegal job transition) therefore reports `invalid_request` with the
status code carrying the nuance, exactly like the contract says.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.schemas import ErrorBody, ErrorCode, ErrorResponse

# Status codes used by the API, named so a reader does not have to remember which integer
# a quota rejection is.
STATUS_BAD_REQUEST = 400
STATUS_UNAUTHORIZED = 401
STATUS_PAYMENT_REQUIRED = 402
STATUS_FORBIDDEN = 403
STATUS_NOT_FOUND = 404
STATUS_CONFLICT = 409
STATUS_UNPROCESSABLE = 422
STATUS_NOT_IMPLEMENTED = 501
STATUS_UNAVAILABLE = 503


class ApiError(Exception):
    """A deliberate refusal. Carries everything the envelope needs and nothing more."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        *,
        retry_after_seconds: int | None = None,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.details = dict(details) if details else None

    def body(self) -> dict[str, Any]:
        """The envelope, as a plain dict ready for `JSONResponse`."""
        return ErrorResponse(
            error=ErrorBody(
                code=self.code,
                message=self.message,
                retry_after_seconds=self.retry_after_seconds,
                details=self.details,
            )
        ).model_dump(mode="json")


def bad_request(message: str, *, details: Mapping[str, str] | None = None) -> ApiError:
    return ApiError(STATUS_BAD_REQUEST, ErrorCode.INVALID_REQUEST, message, details=details)


def unauthorized(message: str, *, details: Mapping[str, str] | None = None) -> ApiError:
    return ApiError(STATUS_UNAUTHORIZED, ErrorCode.UNAUTHORIZED, message, details=details)


def forbidden(message: str, *, details: Mapping[str, str] | None = None) -> ApiError:
    return ApiError(STATUS_FORBIDDEN, ErrorCode.FORBIDDEN, message, details=details)


def not_found(message: str = "Not found.", *, details: Mapping[str, str] | None = None) -> ApiError:
    return ApiError(STATUS_NOT_FOUND, ErrorCode.NOT_FOUND, message, details=details)


def conflict(message: str, *, details: Mapping[str, str] | None = None) -> ApiError:
    """409 body. The contract has no `conflict` code, so this reports `invalid_request`."""
    return ApiError(STATUS_CONFLICT, ErrorCode.INVALID_REQUEST, message, details=details)


def unprocessable(message: str, *, details: Mapping[str, str] | None = None) -> ApiError:
    return ApiError(STATUS_UNPROCESSABLE, ErrorCode.INVALID_REQUEST, message, details=details)


def not_implemented(message: str) -> ApiError:
    return ApiError(STATUS_NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, message)


def unavailable(message: str, *, details: Mapping[str, str] | None = None) -> ApiError:
    """503. Two different situations land here, and the client must be able to tell them apart:

    * a **credential or configuration** is missing → pass
      `details={"missing_credential": "<config name>"}`, the same shape the 501 refusals use, so
      "not configured" has exactly one expression in the API;
    * **anything else** (a dependency missing from the image, a crash) → no details, because
      there is no name for the user to act on.

    The discriminator is the presence of `details.missing_credential`, not the status code: both
    are 503 because both mean "this deployment cannot serve that request right now".
    """
    return ApiError(STATUS_UNAVAILABLE, ErrorCode.INTERNAL, message, details=details)


#: Detail key shared by the 501 credential refusals and 503 configuration refusals. One name for
#: one concept — a client that keys its "ask the admin to configure X" UI off this string must
#: not have to learn a second spelling for the same situation.
DETAIL_MISSING_CREDENTIAL = "missing_credential"

#: Detail key for "this deployment is missing something that is not a credential" — e.g. the
#: `node` binary or the built `packages/shared/dist` that the marketplace mappers need. Kept
#: distinct on purpose: prompting a seller or an admin for a *key* that does not exist would be
#: worse than saying nothing.
DETAIL_MISSING_DEPENDENCY = "missing_dependency"


def _safe_str(value: Any, limit: int = 200) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _validation_details(exc: RequestValidationError) -> dict[str, str]:
    """Flatten pydantic errors into `{"body.category": "Input should be ..."}`.

    Truncated on purpose: a 200-item item_ids array with 200 bad uuids must not turn the
    error body into a payload bigger than the request.
    """
    details: dict[str, str] = {}
    for err in exc.errors()[:10]:
        location = ".".join(str(part) for part in err.get("loc", ())) or "body"
        details[location] = _safe_str(err.get("msg", "invalid value"))
    return details


def install_error_handlers(app: FastAPI) -> None:
    """Register the four handlers that make the envelope universal."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers: dict[str, str] = {}
        if exc.retry_after_seconds is not None:
            headers["retry-after"] = str(exc.retry_after_seconds)
        return JSONResponse(status_code=exc.status_code, content=exc.body(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=STATUS_UNPROCESSABLE,
            content=ApiError(
                STATUS_UNPROCESSABLE,
                ErrorCode.INVALID_REQUEST,
                "Request did not validate against the contract. See details for the fields.",
                details=_validation_details(exc),
            ).body(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {
            401: ErrorCode.UNAUTHORIZED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.INVALID_REQUEST,
        }
        code = mapping.get(exc.status_code, ErrorCode.INTERNAL)
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error."
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiError(exc.status_code, code, message).body(),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Never echo `str(exc)` to the client: an internal message can contain a DSN, a
        # storage path or a provider token. The traceback belongs in the log.
        return JSONResponse(
            status_code=STATUS_UNAVAILABLE,
            content=ApiError(
                STATUS_UNAVAILABLE,
                ErrorCode.INTERNAL,
                f"Unhandled server error ({type(exc).__name__}). The request id is in the logs.",
            ).body(),
        )
