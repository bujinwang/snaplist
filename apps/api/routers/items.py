"""`/v1/items` — create, read, patch attributes, delete, and the two provider-blocked actions.

What is real here
-----------------
* create with an idempotency key, atomically spending one quota unit
* a **signed upload URL** for the original photo (`upload_url`), issued from the key the item
  will own — the client PUTs the bytes to it, and the job becomes claimable the moment they
  land (E5-T4; see `apps/api/routers/uploads.py`)
* `GET` returning the five-key `AssetPack`, with its three URL fields signed **at read time**
  from the stored object keys, so a cached pack never holds an expired link
* `PATCH .../attributes` validated by `ItemAttributes` (illegal values are 422, never stored)
* `DELETE` as a soft delete that immediately stops resolving
* cross-user isolation: every query carries `user_id`, a foreign id is a 404 (not a 403, so
  the endpoint does not confirm that somebody else's item exists)

What is still 501, and why
--------------------------
`POST /v1/items/{id}/rerender` and `POST /v1/items/{id}/on-model` need a worker that runs
`packages/vision` stages. Those stages raise `StageNotImplemented` today (`E1`-`E4`), so a 501
is the truth. Returning 202 and quietly doing nothing would be worse than refusing.

A `multipart/form-data` body on create is also 501, but for a different reason: the bytes are
not uploaded *here* any more. They go to `upload_url` in a second request, which keeps this
endpoint a small transaction and keeps a 20 MB body out of the JSON API.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse

from apps.api import errors, usage, web
from apps.api.auth import Caller, current_caller
from apps.api.repository import Repository
from apps.api.requests import CreateItemRequest
from apps.api.schemas import (
    AssetPack,
    CreateItemResponse,
    ErrorCode,
    ErrorResponse,
    ItemAttributes,
    JobKind,
)
from apps.api.storage import StorageError, source_object_key, storage_of

router = APIRouter(prefix="/v1", tags=["items"])

CREATE_ENDPOINT = "POST /v1/items"
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MIN_IDEMPOTENCY_KEY_LENGTH = 8
MAX_FIELDS_TOUCHED = 100


def quota_error(exc: usage.QuotaExceeded) -> errors.ApiError:
    """The 402 body. `code` is `quota_exceeded` and mobile keys its upsell sheet off it."""
    plan = exc.plan.value
    return errors.ApiError(
        errors.STATUS_PAYMENT_REQUIRED,
        ErrorCode.QUOTA_EXCEEDED,
        f"{plan.title()} plan allows {exc.limit} items per month.",
        details={"plan": plan, "limit": str(exc.limit), "used": str(exc.used)},
    )


def _idempotency_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key:
        return None
    if not MIN_IDEMPOTENCY_KEY_LENGTH <= len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH:
        raise errors.bad_request(
            f"`Idempotency-Key` must be "
            f"{MIN_IDEMPOTENCY_KEY_LENGTH}-{MAX_IDEMPOTENCY_KEY_LENGTH} characters.",
            details={"length": str(len(key))},
        )
    return key


def _fields_touched(request: Request, edited: int) -> int:
    """Client-reported interaction count (reads included), for the analytics ledger.

    The server cannot observe a read, so on its own it can only record `edited`.
    `X-SnapList-Fields-Touched` lets the client report the honest workload number; a malformed
    value is ignored rather than failing a write the seller already made.
    """
    raw = request.headers.get("x-snaplist-fields-touched")
    if raw is None:
        return edited
    try:
        touched = int(raw)
    except ValueError:
        return edited
    if not 0 <= touched <= MAX_FIELDS_TOUCHED:
        return edited
    return max(touched, edited)


def _as_uuid(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise errors.not_found(f"No item {raw} for this account.") from exc


def _base_url(request: Request) -> str:
    """The origin the client used, so a signed URL is absolute and fetchable from a phone.

    Taken from the request rather than a setting: a dev laptop, a phone on the LAN and the
    deployed API each need a different host, and the request already knows which one this is.
    A proxy that rewrites `Host` is therefore also handled, which is the usual deployment.
    """
    return str(request.base_url).rstrip("/")


def _upload_url(
    request: Request, *, user_id: UUID, item_id: UUID, content_type: str | None
) -> str | None:
    """A signed PUT URL for the original photo, or None when no bucket is configured.

    `null` is allowed by the contract (`CreateItemResponse.upload_url`) and is the honest
    answer when the deployment has no storage backend: an invented URL that 404s is worse than
    a client that knows it must ask an operator.
    """
    storage = storage_of(request)
    key = source_object_key(user_id, item_id, content_type)
    try:
        return storage.signed_upload_url(key, base_url=_base_url(request))
    except StorageError as exc:
        raise errors.unavailable(f"Storage could not issue an upload URL: {exc}") from exc


@router.post(
    "/items",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CreateItemResponse,
    responses={402: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Create an item and enqueue its pipeline job",
)
async def create_item(
    request: Request,
    caller: Caller = Depends(current_caller),
) -> CreateItemResponse:
    """Create `items` + `jobs` rows and spend one quota unit, all or nothing.

    The body is parsed by hand: a `multipart/form-data` request must answer 501 with a message
    that says *where the bytes go instead* (`upload_url`, a second request), and letting
    FastAPI validate it as JSON would answer 422 and tell the client to fix the wrong thing.

    `upload_url` is signed after the transaction commits, from the key the item will own:
    `items/<user_id>/<item_id>/source.<ext>`. It is a capability, not a bearer-authenticated
    endpoint (see `apps/api/routers/uploads.py`), and it expires after
    `Settings.storage_upload_ttl_seconds`.
    """
    repository: Repository = web.repository_of(request)
    idempotency_key = _idempotency_key(request.headers.get("idempotency-key"))

    media_type = web.media_type_of(request)
    if media_type not in web.JSON_MEDIA_TYPES:
        raise errors.not_implemented(
            f"Binary upload (content-type {media_type or 'unset'}) is a two-step flow: "
            "POST /v1/items without a body, then PUT the bytes to the `upload_url` it returns "
            "(`PUT /v1/uploads/{key}?exp=&sig=`). This endpoint accepts a JSON description "
            "of the photo only."
        )

    raw = await request.body()
    body = web.parse_json_body(request, raw, CreateItemRequest) if raw else None
    content_type = body.content_type if body is not None else None
    limits = usage.limits_from_settings(web.settings_of(request))

    replay_of: tuple[UUID, UUID] | None = None
    item_id: UUID
    job_id: UUID
    try:
        with repository.transaction():
            if idempotency_key is not None:
                replay_of = repository.get_idempotent_item(
                    idempotency_key, user_id=caller.user_id, endpoint=CREATE_ENDPOINT
                )
            if replay_of is not None:
                # Nothing is written: the caller is retrying a create it already succeeded at,
                # and the quota unit was spent the first time.
                item_id, job_id = replay_of
            else:
                item = repository.create_item(user_id=caller.user_id, status="draft")
                job = repository.create_job(
                    item_id=item.id, user_id=caller.user_id, kind=JobKind.FULL
                )
                usage.spend_one(
                    repository,
                    caller.user_id,
                    job_id=job.id,
                    item_id=item.id,
                    limits=limits,
                )
                if idempotency_key is not None:
                    repository.store_idempotent_item(
                        idempotency_key,
                        user_id=caller.user_id,
                        endpoint=CREATE_ENDPOINT,
                        item_id=item.id,
                        job_id=job.id,
                    )
                item_id, job_id = item.id, job.id
    except usage.QuotaExceeded as exc:
        raise quota_error(exc) from exc

    # Signed **after** the transaction: for Supabase this is an HTTPS round trip, and holding
    # a write lock across the network is how a 200 ms provider hiccup becomes a stalled queue.
    # A replay gets a fresh URL too — the one the first response carried expires in 15 minutes,
    # and a retry hours later would otherwise be handed a dead link.
    return CreateItemResponse(
        item_id=item_id,
        job_id=job_id,
        upload_url=_upload_url(
            request, user_id=caller.user_id, item_id=item_id, content_type=content_type
        ),
    )


@router.get(
    "/items/{item_id}",
    response_model=AssetPack,
    responses={404: {"model": ErrorResponse}},
    summary="The AssetPack for one of the caller's items",
)
def get_item(
    item_id: str,
    request: Request,
    caller: Caller = Depends(current_caller),
) -> AssetPack:
    """`item_id` is taken as a string and validated here so a malformed id is a 404.

    FastAPI would answer 422 for a non-uuid path parameter; the client only ever holds uuids,
    so a bad one means "no such item" and a 404 keeps one not-found shape on the wire.

    The three URL fields are signed here, from the object keys on the row. The row keeps a key
    and the caller gets a URL with a TTL; that is why `AssetPack` can be cached by the client
    without the cache going stale in any way a re-fetch could not fix.
    """
    repository: Repository = web.repository_of(request)
    record = repository.get_item(_as_uuid(item_id), user_id=caller.user_id)
    if record is None:
        raise errors.not_found(f"No item {item_id} for this account.")
    storage = storage_of(request)
    base_url = _base_url(request)
    return record.to_asset_pack(
        url_for=lambda key: storage.signed_asset_url(key, base_url=base_url)
    )


@router.patch(
    "/items/{item_id}/attributes",
    response_model=ItemAttributes,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Replace the attributes of one item",
)
def patch_attributes(
    item_id: str,
    attributes: ItemAttributes,
    request: Request,
    caller: Caller = Depends(current_caller),
) -> ItemAttributes:
    """Validated by `ItemAttributes`: an unknown key or a bad enum is a 422 and nothing is
    written. The diff is recorded in the ledger as `attributes_edited`, with the two counters
    docs/METRICS.md insists on keeping apart (`fields_touched` vs `edited_field_count`).
    """
    repository: Repository = web.repository_of(request)
    resolved_id = _as_uuid(item_id)
    current = repository.get_item(resolved_id, user_id=caller.user_id)
    if current is None:
        raise errors.not_found(f"No item {item_id} for this account.")

    before = current.attributes.model_dump(mode="json") if current.attributes else {}
    after = attributes.model_dump(mode="json")
    changed = sum(1 for field in after if before.get(field) != after[field])
    touched = _fields_touched(request, changed)

    with repository.transaction():
        updated = repository.update_item_attributes(
            resolved_id, user_id=caller.user_id, attributes=attributes
        )
        if updated is None:  # pragma: no cover - the row was read one statement ago
            raise errors.not_found(f"No item {item_id} for this account.")
        if changed:
            repository.append_usage_event(
                usage.UsageEventRecord(
                    user_id=caller.user_id,
                    event=usage.EVENT_ATTRIBUTES_EDITED,
                    units=1,
                    item_id=resolved_id,
                    fields_touched=touched,
                    edited_field_count=changed,
                    metadata={"changed": ",".join(sorted(after))[:200]},
                    created_at=usage.utcnow(),
                )
            )
    stored = updated.attributes
    assert stored is not None  # written by update_item_attributes one statement above
    return stored


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
    summary="Soft-delete one item",
)
def delete_item(
    item_id: str,
    request: Request,
    caller: Caller = Depends(current_caller),
) -> Response:
    repository: Repository = web.repository_of(request)
    deleted = repository.soft_delete_item(_as_uuid(item_id), user_id=caller.user_id)
    if not deleted:
        raise errors.not_found(f"No item {item_id} for this account.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/items/{item_id}/rerender",
    responses={501: {"model": ErrorResponse}},
    summary="Re-run prettify for one item (blocked: no worker)",
)
def rerender_item(item_id: str, caller: Caller = Depends(current_caller)) -> JSONResponse:
    """E5-T3 + E2-T2: needs a worker that runs `packages/vision/prettify.py`, still a stub."""
    raise errors.not_implemented(
        "POST /v1/items/{item_id}/rerender needs the job worker (E5-T3) and "
        "packages.vision.prettify (E2-T2), neither of which is implemented."
    )


@router.post(
    "/items/{item_id}/on-model",
    responses={501: {"model": ErrorResponse}},
    summary="Render the garment on a model (blocked: FASHN credentials)",
)
def on_model(item_id: str, caller: Caller = Depends(current_caller)) -> JSONResponse:
    """E4-T1: needs `FASHN_API_KEY` and the quality gate. 501, not a fake queue entry."""
    raise errors.not_implemented(
        "POST /v1/items/{item_id}/on-model needs packages.vision.vton (E4-T1) and "
        "FASHN_API_KEY. Refusing rather than charging for a render that cannot happen."
    )
