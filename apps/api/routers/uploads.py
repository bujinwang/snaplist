"""E5-T4 — the two signed, unauthenticated object routes.

    PUT /v1/uploads/{key}?exp=<unix seconds>&sig=<hex>   bytes in
    GET /v1/assets/{key}?exp=<unix seconds>&sig=<hex>    bytes out

Why there is no `Authorization` header here
-------------------------------------------
The signature **is** the authorisation. It is a capability URL: only this process can mint
one (it holds the secret), and it can only be minted for a key this process chose (a source
key handed out by `POST /v1/items`, or an asset key from a finished job). Requiring a bearer
token on top would mean a phone on a bad connection has to refresh a token mid-upload to
retry a 20 MB body, and would make the URL alone insufficient — which defeats the point of
handing out a URL. `docs/PLAN.md` E5-T4 asks for exactly this: "refuses an unauthenticated
PUT" is satisfied by a signed PUT succeeding and an unsigned PUT failing, not by adding auth
to a signed URL.

Order of refusals, and why it matters
-------------------------------------
1. **503** — the process has no storage backend at all. This is a deployment problem, and it
   names the missing config key.
2. **400** — the key is not a safe relative path (`..`, absolute, `//`, `\\`, control chars,
   too long). Validation happens *before* signature verification so a hostile key is rejected
   by a check that does not depend on the secret.
3. **403** — signature missing, forged, for a different key, for a different purpose, or
   expired. Every one of those is the same answer on purpose: telling an attacker *which*
   part of the capability they got wrong is free help.
4. **415 / 413** — content-type outside the four accepted formats; body over the ceiling.
5. **404** (GET only) — a *valid* signature with no object behind it. Only after (4), because
   an unsigned request must not be able to probe whether an object exists.

Write-back on upload
---------------------
A successful `PUT` also registers the `assets` row with `kind='source'` (when the key is
item-shaped), which is what makes the item's job claimable:
`SqliteRepository.peek_next_queued_job` requires a source asset, so the worker cannot claim a
job whose bytes have not landed. The row's `sha256`/`byte_size` come from the bytes that were
written, never from the request's `Content-Length` header.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response

from apps.api import errors, web
from apps.api.repository import Repository
from apps.api.schemas import ErrorCode, ErrorResponse
from apps.api.storage import (
    ALLOWED_CONTENT_TYPES,
    PURPOSE_ASSET,
    PURPOSE_UPLOAD,
    InvalidKey,
    MissingObject,
    ObjectTooLarge,
    Storage,
    StorageError,
    StoredObject,
    UnsupportedContentType,
    content_type_for_key,
    parse_item_key,
    sha256_of,
    storage_of,
    validate_key,
)

router = APIRouter(prefix="/v1", tags=["uploads"])

STATUS_PAYLOAD_TOO_LARGE = 413
STATUS_UNSUPPORTED_MEDIA_TYPE = 415

#: `assets.meta` key holding the digest of the bytes this process wrote. E3-T4 (exact dedupe)
#: reads it, and `apps/api/jobs/writeback.py` writes it with the same name.
META_SHA256 = "sha256"
META_CONTENT_TYPE = "content_type"


# ---------------------------------------------------------------------------------------
# Shared parsing / refusal helpers — one meaning per status code across both routes.
# ---------------------------------------------------------------------------------------
def _exp_of(request: Request) -> int | None:
    """`?exp=` as an int, or None. A non-numeric exp is simply an invalid signature."""
    raw = request.query_params.get("exp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _sig_of(request: Request) -> str | None:
    """`?sig=` exactly as sent — **no** trimming.

    Trimming was a real hole: `?sig=<valid>%20` stripped back to a valid signature and the
    upload succeeded, so a value the route never accepted as-is was accepted after a silent
    edit. The signature is 64 hex characters; a value with a space in it is a different value
    and must fail the shape check rather than be repaired.
    """
    raw = request.query_params.get("sig")
    return raw if raw else None


def _require_storage(request: Request) -> Storage:
    storage = storage_of(request)
    if not storage.configured:
        # 503, and the config *name* rather than a fabricated URL. `POST /v1/items` still
        # succeeds in this state with `upload_url: null` — see settings/build_storage.
        raise errors.unavailable(
            "Object storage is not configured on this deployment: signed URLs cannot be "
            "issued and uploaded bytes cannot be stored.",
            details={
                errors.DETAIL_MISSING_CREDENTIAL: getattr(
                    storage, "missing_credential", "supabase_service_key"
                )
            },
        )
    return storage


def _require_key(key: str) -> str:
    try:
        return validate_key(key)
    except InvalidKey as exc:
        raise errors.bad_request(str(exc)) from exc


def _require_signature(storage: Storage, key: str, purpose: str, request: Request) -> None:
    if not storage.verify_signature(
        key, purpose=purpose, exp=_exp_of(request), sig=_sig_of(request)
    ):
        raise errors.forbidden(
            "Signature is missing, expired or not valid for this object.",
            details={"purpose": purpose},
        )


def _require_content_type(request: Request) -> str:
    media_type = web.media_type_of(request)
    if media_type not in ALLOWED_CONTENT_TYPES:
        raise errors.ApiError(
            STATUS_UNSUPPORTED_MEDIA_TYPE,
            ErrorCode.INVALID_REQUEST,
            "Unsupported image type. Accepted: "
            f"{', '.join(sorted(ALLOWED_CONTENT_TYPES))}.",
            details={"content_type": media_type or "unset"},
        )
    return media_type


def _declared_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _too_large(limit: int, size: int) -> errors.ApiError:
    return errors.ApiError(
        STATUS_PAYLOAD_TOO_LARGE,
        ErrorCode.INVALID_REQUEST,
        f"Upload exceeds the {limit} byte limit.",
        details={"byte_size": str(size), "limit": str(limit)},
    )


def _item_for_key(repository: Repository, key: str) -> tuple[UUID, UUID] | None:
    """`(user_id, item_id)` when the key is item-shaped and the item is live, else None.

    Reading the item here (before any bytes are written) means a signature for an item that
    has since been deleted cannot leave an orphan object in the bucket.

    This runs **after** the signature check, deliberately. Moving it earlier would turn an
    unauthenticated `PUT` at a guessed key into an existence oracle: 404 for a real item,
    403 for a fake one. The order costs one `select` on a refused request and buys the
    property that every failure before the signature is the same 403.
    """
    parsed = parse_item_key(key)
    if parsed is None:
        return None
    user_id, item_id = parsed
    record = repository.get_item(item_id, user_id=user_id)
    if record is None:
        raise errors.not_found(f"No item {item_id} for this account.")
    return user_id, item_id


def _require_unprocessed(repository: Repository, parsed: tuple[UUID, UUID] | None) -> None:
    """409 when this item's pipeline already finished — see `Repository.has_finished_job`.

    Overwriting the source photo of a finished item would leave `prettified_url` /
    `cutout_url` pointing at renders of the *previous* photo while `assets.meta.sha256`
    describes the new one: a silent inconsistency with no error to notice. Refusing is cheap
    and reversible; reconciling is neither.
    """
    if parsed is None:
        return
    _, item_id = parsed
    if repository.has_finished_job(item_id):
        raise errors.conflict(
            "This item has already been processed, so its original photo can no longer be "
            "replaced: the assets derived from it would no longer match. Create a new item "
            "(POST /v1/items) to upload a different photo, or POST /v1/items/{item_id}/"
            "rerender to redo the rendering from the photo already stored.",
            details={"item_id": str(item_id)},
        )


# ---------------------------------------------------------------------------------------
# PUT — direct upload.
# ---------------------------------------------------------------------------------------
@router.put(
    "/uploads/{key:path}",
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
    },
    summary="Upload one object to a signed URL",
)
async def upload_object(key: str, request: Request) -> Response:
    """Store the body verbatim at `key`. 201 on first write, 200 on a re-upload, 409 once the
    item has been processed.

    Idempotent while the item is unprocessed: a phone that loses the response to the first PUT
    retries, and the retry must converge on one object and one `assets` row — not two objects
    and a duplicate row. The row's sha256/byte_size describe the **latest** bytes, because
    that is what the bucket now holds.

    Idempotency ends at `done`. After that the objects in the bucket were derived from the
    bytes that are already there, so a replacement would leave a pack that renders one photo
    and hashes another — a 409, not a silent overwrite.
    """
    storage = _require_storage(request)
    repository = web.repository_of(request)
    _require_key(key)
    _require_signature(storage, key, PURPOSE_UPLOAD, request)
    content_type = _require_content_type(request)

    declared = _declared_length(request)
    if declared is not None and declared > storage.max_upload_bytes:
        raise _too_large(storage.max_upload_bytes, declared)

    parsed = _item_for_key(repository, key)  # 404 before a byte is written
    _require_unprocessed(repository, parsed)  # 409 before a byte is overwritten

    body = await request.body()
    if not body:
        raise errors.bad_request("Upload body is empty.")
    if len(body) > storage.max_upload_bytes:
        raise _too_large(storage.max_upload_bytes, len(body))

    existed = storage.exists(key)
    try:
        stored = storage.write(key, body, content_type=content_type)
    except UnsupportedContentType as exc:  # pragma: no cover - the route already refused
        raise errors.ApiError(
            STATUS_UNSUPPORTED_MEDIA_TYPE, ErrorCode.INVALID_REQUEST, str(exc)
        ) from exc
    except ObjectTooLarge as exc:  # pragma: no cover - checked above
        raise _too_large(storage.max_upload_bytes, len(body)) from exc
    except StorageError as exc:
        raise errors.unavailable(f"Storage refused the upload: {exc}") from exc

    _record_source_asset(repository, storage, key, stored, content_type)
    return Response(
        status_code=200 if existed else 201,
        headers={"x-snaplist-byte-size": str(stored.byte_size)},
    )


def _record_source_asset(
    repository: Repository,
    storage: Storage,
    key: str,
    stored: StoredObject,
    content_type: str,
) -> None:
    """Write the `assets` row that makes the item's job claimable.

    `kind='source'`, `position=0`: the uploaded original is the pipeline's first input and the
    only row `peek_next_queued_job` looks for.
    """
    parsed = parse_item_key(key)
    if parsed is None:  # pragma: no cover - `_item_for_key` already resolved or 404'd
        return
    user_id, item_id = parsed
    repository.set_asset(
        item_id,
        user_id=user_id,
        kind="source",
        storage_path=key,
        position=0,
        byte_size=stored.byte_size,
        meta={
            META_SHA256: stored.sha256,
            META_CONTENT_TYPE: content_type,
            # Which bucket held these bytes. A row that cannot say where it lives is a row
            # that cannot be migrated to another backend without guessing.
            "backend": storage.describe(),
        },
    )


# ---------------------------------------------------------------------------------------
# GET — signed read.
# ---------------------------------------------------------------------------------------
@router.get(
    "/assets/{key:path}",
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    summary="Download one object from a signed URL",
)
def read_asset(key: str, request: Request) -> Response:
    """Bytes out, with `Content-Length` and an immutable cache hint for the URL's TTL.

    The signature check runs before the existence check. A signed-but-absent key is a 404;
    an unsigned key is a 403 even when the object is right there. Reversing that order would
    turn this route into an oracle for "which objects exist".
    """
    storage = _require_storage(request)
    _require_key(key)
    _require_signature(storage, key, PURPOSE_ASSET, request)
    try:
        data = storage.read(key)
    except MissingObject as exc:
        raise errors.not_found("No object behind this signed URL.") from exc
    except StorageError as exc:
        raise errors.unavailable(f"Storage refused the read: {exc}") from exc
    # The cache hint is the *remaining* life of this signature, not the configured TTL: a URL
    # fetched ten seconds before it expires must not teach a proxy to serve it for an hour.
    exp = _exp_of(request) or 0
    max_age = max(0, exp - int(storage.clock()))
    return Response(
        content=data,
        media_type=content_type_for_key(key),
        headers={
            "content-length": str(len(data)),
            "cache-control": f"private, max-age={max_age}",
            "x-snaplist-sha256": sha256_of(data),
        },
    )


__all__ = ["META_CONTENT_TYPE", "META_SHA256", "STATUS_PAYLOAD_TOO_LARGE", "router"]
