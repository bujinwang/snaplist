"""`POST /v1/events` — the analytics funnel ingest (E5-T6).

What this endpoint is for
-------------------------
The mobile client emits the seven events frozen in `docs/CONTRACTS.md` §7 (typed in
`packages/shared/src/analytics.ts`, `EVENTS_VERSION 1.4.0`). This route is the only way a row
reaches `public.events`. There is no read side: reports query the table directly, and **no
`GET /v1/events` exists** on purpose — a client that can read the funnel can read other
sellers' behaviour through any aggregate a future endpoint forgets to filter. A `GET` on this
path is answered by FastAPI's router with **405 Method Not Allowed** (the path exists, the
method does not), which is the answer that tells a confused client it is holding the wrong
verb rather than a wrong URL.

Six things this module does that are easy to get wrong, and how each is done
---------------------------------------------------------------------------
1. **The vocabulary is closed in three places.** `extra="forbid"` on every model (the
   python equivalent of the zod `.strict()` in `analytics.ts`), a discriminated union on
   `event` (an unknown name is 422, not a row), and a CHECK constraint on the table. An
   unknown key — `"email": "a@b"` is the example that matters — is a 422 too, never a silent
   strip: stripping hides the mistake until a lawyer finds it.
2. **`subject` is bound to the caller.** The client supplies `anon_<uuid>` / `usr_<uuid>` and
   the API refuses a suffix that is not `str(caller.user_id)` (**403**). Without this check
   `subject` is just an assertion: any authenticated seller could file events against any
   other seller's id and the funnel would be poisonable by one curl command. The database can
   only check the *shape* of `subject` (it has no JWT), so the binding has to live here.
3. **`item_id` is bound to the caller too, and graded per event** (ADR-008, `1.4.0`). Five
   events require it, `paywall_impression` may omit it, `subscription_started` must not carry
   it. The uuid is checked against `repository.get_item(..., user_id=caller.user_id)` and a
   foreign id is a **404** — the same answer `routers/items.py` gives, for the same reason: a
   403 would confirm that somebody else's item exists. Without this check the field would be
   an assertion exactly like an unbound `subject`, and one seller could attach rows to
   another's items.
4. **Idempotency is on `event_id`, the client's uuid.** Same id + same payload digest → 202
   `{"accepted": true, "duplicate": true}` and no second row. Same id + a *different* digest →
   **409**: that is not a retry, it is a client that reused an id, and silently swallowing it
   would leave the funnel quietly wrong (one id, one row, whichever payload won the race).
5. **8 KB body ceiling, 413.** Checked against `content-length` before the bytes are read and
   again against the real length after, because a chunked request has no such header. An
   event is ~300 bytes; 8 KB is ~25x the largest legal payload (`field_names` with 20 names),
   so the ceiling can only ever be hit by something that is not an event.
6. **Nothing here reaches `apps/api/schemas.py`.** This module is deliberately outside the
   frozen ten-model wire contract: adding `EventAccepted` there would require a zod mirror in
   `packages/shared/src/schemas.ts`, a `CONTRACT_VERSION` bump and an ADR, to describe a
   response that only this route ever returns. `apps/api/responses.py` sets the same
   precedent for the export batch body, and `test_contract_parity.py` scans `schemas.py` and
   `responses.py` only — so keeping the model here is also what keeps that test honest
   instead of asking it to mirror something the shared package has no reason to know.

What this endpoint does not do, and will not pretend to
------------------------------------------------------
* **No rate limit.** One authenticated seller can post events as fast as the process answers.
  Bounded only by the 8 KB ceiling and by infrastructure. See docs/HANDOFF-engineer.md.
* **No clock trust.** `client_ts` is stored as sent (shape-validated, normalised to UTC) and
  `received_at` is stamped server-side. A device with a wrong clock produces a wrong
  `client_ts`; every report must order by `received_at`. Nothing here can fix that.
* **No dedupe of semantically identical events.** Two different `event_id`s carrying the same
  `upload_started` are two rows. Idempotency is per client-generated id, not per content.
* **No proof that `item_id` was ever the caller's at the time of the event.** The check is
  "this item belongs to you now". An item hard-deleted between emit and retry is a 404, which
  is why the ownership lookup includes soft-deleted rows (see `bind_item`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from apps.api import errors, web
from apps.api.auth import Caller, current_caller
from apps.api.repository import EventRecord, Repository, to_wire, utcnow
from apps.api.schemas import ErrorCode, ErrorResponse, Marketplace

router = APIRouter(prefix="/v1", tags=["events"])

#: 8 KB. See the module docstring, point 4: the ceiling bounds the work a single request can
#: cost, and every legal event is far below it.
MAX_EVENT_BYTES = 8 * 1024

#: The wire columns are Postgres `integer`. Without this bound a client sending `10**30` gets a
#: 503 `internal` from the database driver's `OverflowError` — a client mistake reported as a
#: server failure, which is exactly what "5xx means we are broken" is supposed to mean. The
#: database is the second lock, not the first: this one turns it into a 422 first.
INT4_MAX = 2_147_483_647

#: A field *name*, not a value. The alphabet is the contract's; the length cap is ours, because
#: `^[a-z][a-z0-9_]*$` alone leaves a 7 KB lowercase text channel inside a column the contract
#: describes as "names only". 64 is longer than any real attribute key.
MAX_FIELD_NAME_LENGTH = 64

#: At most 20 names on `attributes_edited`. The Postgres CHECK and the zod schema also cap it;
#: this copy exists so the refusal is a 422 with a readable detail instead of a 500 from the
#: database.
MAX_FIELD_NAMES = 20

#: `field_names` elements are field *names*. Lifted verbatim from
#: `packages/shared/src/analytics.ts` (`z.string().regex(/^[a-z][a-z0-9_]*$/)`), and the same
#: pattern appears in `events_field_names_are_name_list()` in
#: `infra/migrations/0002_events.sql`. Three copies, one regex, and `test_events.py` pins the
#: TS one against this one so a change in the frozen vocabulary cannot go unnoticed.
FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"

#: `errors.STATUS_*` has no 413 and `apps/api/errors.py` was not this ticket's to widen.
STATUS_PAYLOAD_TOO_LARGE = 413

#: Shape of `subject`: `anon_<uuid>` before signup, `usr_<uuid>` after. Never an email.
SUBJECT_PREFIXES = ("anon_", "usr_")


# ---------------------------------------------------------------------------------------
# Enums — the literals are duplicated verbatim from packages/shared/src/analytics.ts. They
# are NOT in apps/api/schemas.py because the event vocabulary has its own version
# (`EVENTS_VERSION`), separate from `CONTRACT_VERSION`; see docs/CONTRACTS.md §7.
# ---------------------------------------------------------------------------------------
class Platform(StrEnum):
    IOS = "ios"
    ANDROID = "android"
    WEB = "web"


class CaptureSource(StrEnum):
    CAMERA = "camera"
    LIBRARY = "library"


class CaptureMode(StrEnum):
    SINGLE = "single"
    BATCH = "batch"


class PrettifyDecision(StrEnum):
    APPROVE = "approve"
    RETRY = "retry"
    REMOVE_BG_WAITLIST = "remove_bg_waitlist"


class PaywallVariant(StrEnum):
    A = "a"
    B = "b"
    C = "c"


class PaywallTrigger(StrEnum):
    UPLOAD_4TH = "upload_4th"
    ON_MODEL = "on_model"
    EXPORT_LIMIT = "export_limit"


class PaidPlan(StrEnum):
    """`plan` on `subscription_started` and `plan_offered` on `paywall_impression`.

    One enum for both because the two lists are identical in the frozen schema and a paywall
    that offers a plan the subscription cannot record is a bug nobody would notice. `free` is
    absent deliberately: no event fires for staying free.
    """

    STARTER = "starter"
    PRO = "pro"


# ---------------------------------------------------------------------------------------
# Models. `extra="forbid"` on the base class is inherited by every subclass, which is what
# makes `.strict()` semantics apply to the union members too — pydantic applies the
# discriminator first and then the chosen member's own configuration.
# ---------------------------------------------------------------------------------------
class EventEnvelope(BaseModel):
    """The envelope every event carries.

    `event_id` is part of the frozen vocabulary as of `EVENTS_VERSION = 1.3.0` (ADR-007): it was
    added to `Envelope` in `analytics.ts` because this route needs a client-generated idempotency
    key and the shared schema had none, which meant a client validating with that schema could
    not build a body this route accepts. The two are now identical on purpose — the parity test
    in `apps/api/tests/test_events.py` compares them with an empty additive-field set.

    `item_id` joined at `1.4.0` (ADR-008) and is **graded per event**, not merely optional:
    the five per-item events redeclare it as a required `UUID`, `subscription_started`
    redeclares it as `None`, and only `paywall_impression` inherits the optional form below.
    The grade is the point — `docs/METRICS.md` had assumed every funnel event carried an item
    id for two quarters while the table had no such column, and a globally optional field
    would have reproduced that bug in a shape nobody could test.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: UUID
    item_id: UUID | None = None
    client_ts: datetime
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    subject: str = Field(min_length=1, pattern=r"^(anon|usr)_[0-9a-f-]{36}$")
    app_version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z.+-]{1,32}$")
    platform: Platform

    @field_validator("client_ts")
    @classmethod
    def _client_ts_is_absolute(cls, value: datetime) -> datetime:
        """Reject a naive timestamp and store UTC.

        `analytics.ts` says `z.string().datetime()`, which in zod requires a `Z`/offset — so
        this is not a new restriction, it is the same one. A naive `client_ts` would be
        silently interpreted in the server's timezone, which is how a latency series ends up
        eight hours wrong on one cohort and looks like a product regression.
        """
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("client_ts must carry a timezone offset (UTC is expected)")
        return value.astimezone(UTC)


class UploadStarted(EventEnvelope):
    """1. the shutter fired, or a library photo was picked."""

    event: Literal["upload_started"]
    item_id: UUID
    source: CaptureSource
    capture_mode: CaptureMode
    batch_index: int | None = Field(default=None, ge=0)


class PrettifyApproved(EventEnvelope):
    """2. the seller accepted, retried, or asked for manual background removal.

    One event per *decision*: a retried item emits several, and only the final `approve` is
    the item's verdict (docs/CONTRACTS.md §7). `job_latency_ms` is the server's number and the
    other two are the client's; conflating them destroys the only server/client split we have.
    `item_id` is required precisely because of that "several rows, one item" rule: without it,
    a retried item is indistinguishable from three items.
    """

    event: Literal["prettify_approved"]
    item_id: UUID
    decision: PrettifyDecision
    job_latency_ms: int = Field(ge=0, le=INT4_MAX)
    prettify_latency_ms: int = Field(ge=0, le=INT4_MAX)
    attrs_latency_ms: int = Field(ge=0, le=INT4_MAX)


class AttributesEdited(EventEnvelope):
    """3. the attribute sheet was dismissed. `fields_touched` may be 0 (blind accept)."""

    event: Literal["attributes_edited"]
    item_id: UUID
    edited_field_count: int = Field(ge=0, le=INT4_MAX)
    fields_touched: int = Field(ge=0, le=INT4_MAX)
    field_names: list[
        Annotated[
            str, Field(pattern=FIELD_NAME_PATTERN, max_length=MAX_FIELD_NAME_LENGTH)
        ]
    ] = Field(default_factory=list, max_length=MAX_FIELD_NAMES)


class ExportClicked(EventEnvelope):
    """4. the seller *asked* for a marketplace file. Attempt, not success."""

    event: Literal["export_clicked"]
    item_id: UUID
    marketplace: Marketplace
    row_count: int = Field(ge=1, le=INT4_MAX)
    export_latency_ms: int = Field(ge=0, le=INT4_MAX)


class SubscriptionStarted(EventEnvelope):
    """5. money moved.

    `item_id` is **forbidden**, and forbidden by the type rather than by a comment: the field
    is redeclared as `None`, so a body that carries an item id is a 422 with `item_id` named
    in the details. The reason is factual — this event is written by the API from a Stripe
    webhook, where no item is in scope. A subscription is not about one garment, and a
    plausible id attached to it would silently attribute revenue to whichever item happened to
    be on the seller's screen (ADR-008). The table's reverse CHECK pins the same rule for
    writes that bypass this route; that duplication is deliberate, because one lock guards the
    client and the other guards us.
    """

    event: Literal["subscription_started"]
    item_id: None = None
    plan: PaidPlan
    paywall_variant: PaywallVariant
    amount_cents: int = Field(ge=0, le=INT4_MAX)
    # `analytics.ts` says `z.string().length(3)`, so the alphabet stays whatever the client
    # sends: an ISO-4217 pattern here would 422 a body the shared schema called valid, which is
    # the contract violation this repo refuses to make. What is excluded is control characters,
    # because a NUL is not a currency: SQLite's `length()` stops at it, the column CHECK sees 0,
    # and a client typo came back as a 503 from the database driver.
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[^\x00-\x1f\x7f]{3}$")


class PaywallImpression(EventEnvelope):
    """6. the paywall was shown, converted or not. Never smuggled into `export_clicked`.

    The one event where `item_id` stays optional — inherited from the envelope, not
    redeclared. `trigger=on_model` and `trigger=export_limit` always have an item; a paywall
    opened from the settings screen has none, and none of the paywall metrics segment by item.
    Requiring it would 422 a legal impression; forbidding it would refuse the two triggers that
    do have one (ADR-008).
    """

    event: Literal["paywall_impression"]
    paywall_variant: PaywallVariant
    trigger: PaywallTrigger
    plan_offered: PaidPlan


class ExportDelivered(EventEnvelope):
    """7. the seller **has** the file. This — never `export_clicked` — counts activation."""

    event: Literal["export_delivered"]
    item_id: UUID
    marketplace: Marketplace
    row_count: int = Field(ge=1, le=INT4_MAX)
    bytes: int = Field(ge=0, le=INT4_MAX)
    delivery_latency_ms: int = Field(ge=0, le=INT4_MAX)


#: The seven. A discriminated union rather than seven optional fields, so an unknown `event`
#: fails at the tag instead of matching a model that happens to accept it.
AnalyticsEvent = Annotated[
    UploadStarted
    | PrettifyApproved
    | AttributesEdited
    | ExportClicked
    | SubscriptionStarted
    | PaywallImpression
    | ExportDelivered,
    Field(discriminator="event"),
]

#: One adapter, built once. `validate_python` on it is what the route uses instead of a
#: FastAPI body parameter, because the 8 KB ceiling has to be answered *before* validation
#: (a 9 KB body is 413 whether or not it parses) and FastAPI parses a declared body param
#: before the view function runs.
EVENT_ADAPTER = TypeAdapter(AnalyticsEvent)


class EventAccepted(BaseModel):
    """The 202 body. Two booleans, no row id.

    `accepted` is always true on a 2xx (a refusal is a 4xx with the standard error envelope);
    it exists so the client has one field to branch on without inspecting the status code, and
    is not a second place to encode failure. `duplicate` is the interesting one: a client that
    queued an event offline wants to stop retrying it, and "you already sent this" is a
    different fact from "I stored it just now".
    """

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    duplicate: bool


# ---------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------
def _validation_details(exc: ValidationError) -> dict[str, str]:
    """Flatten pydantic errors into `{"body.category": "Input should be ..."}`.

    Same shape as `apps/api/web.py::parse_json_body` and `apps/api/errors.py` produce, so a
    client parses one error body regardless of which layer refused the request. Truncated to
    ten entries: a 500-key JSON body must not turn into a 500-key error body.
    """
    return {
        ".".join(str(part) for part in error["loc"]) or "body": str(error["msg"])[:200]
        for error in exc.errors()[:10]
    }


def parse_event(raw: bytes) -> AnalyticsEvent:
    """Validate `raw` as one of the seven events, or raise a 422 with the standard envelope."""
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
        return EVENT_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise errors.unprocessable(
            "Request body is not one of the seven frozen analytics events. "
            "See details for the fields.",
            details=_validation_details(exc),
        ) from exc


def bind_subject(subject: str, caller: Caller) -> None:
    """`subject` must name the caller: `anon_<uid>` or `usr_<uid>`. Otherwise 403.

    The subject is supplied by the client, so without this check it is a claim rather than a
    fact — any authenticated seller could file events against another seller's id, and the
    funnel would be poisonable by one curl. The `anon_`/`usr_` prefix is *not* trusted for
    anything else: it records which side of signup the event came from, and E5-T5's in-place
    conversion keeps the uuid, so `usr_<uid>` and `anon_<uid>` for the same account are the
    same person and reports must join on the suffix, not the prefix.

    A 403 (not 404) because the caller is authenticated and the mismatch is about *whose*
    data this is. The offending subject is not echoed back: it is client-controlled text and
    has no business in a response body.
    """
    account = str(caller.user_id)
    allowed = tuple(f"{prefix}{account}" for prefix in SUBJECT_PREFIXES)
    if subject not in allowed:
        raise errors.forbidden(
            "`subject` must be `anon_<your user id>` or `usr_<your user id>`; it is not a "
            "free-form identifier and it may not name another account.",
            details={"reason": "subject_not_bound_to_caller"},
        )


def bind_item(item_id: UUID | None, caller: Caller, repository: Repository) -> None:
    """`item_id`, when present, must name an item the caller owns. Otherwise 404.

    Same shape of reasoning as `bind_subject`, applied to the other identifying field: the
    uuid is supplied by the client, so without a lookup it is a claim. The consequences of
    trusting it are worse than for `subject`, because `item_id` is the join key every
    per-item report uses — a forged one does not just poison a count, it attaches rows to
    another seller's item and makes their tool-time and workload numbers nonsense.

    **404, not 403**, and for the reason `routers/items.py` gives for the same choice:
    confirming that a foreign id exists is itself the leak. The caller has no use for the
    distinction between "not yours" and "not there", and an attacker probing uuids must not be
    able to tell them apart either.

    `include_deleted=True` is deliberate. Events are append-only history and an item that was
    soft-deleted an hour ago still owns its rows; the ownership question is "was this ever
    yours", not "is it still live". With the default (`include_deleted=False`) deleting an
    item would start 404-ing retries of events that were legal when they were emitted, i.e.
    the client would be punished for the seller tidying up.

    **There is deliberately no FK on `events.item_id`; this is the only existence check** —
    `ON DELETE SET NULL` would be an implicit UPDATE of an append-only row and would collide
    with the forward CHECK, so a hard-deleted item leaves existing rows holding their original
    id (ADR-008 Corrigendum) while new events naming it are refused here with a 404.

    `None` is a no-op: absent is legal on `paywall_impression`, and forbidden-on
    `subscription_started` is enforced by the model's type and by the table's reverse CHECK,
    not here — a validator that silently accepted `item_id=None` for `subscription_started`
    and rejected a real one would still let a *stale* id through if it ever appeared.
    """
    if item_id is None:
        return
    if repository.get_item(item_id, user_id=caller.user_id, include_deleted=True) is None:
        raise errors.not_found(
            "No item for this account matches `item_id`. Events may only be filed against "
            "your own items.",
            details={"reason": "item_id_not_owned_by_caller"},
        )


def canonical_payload_digest(event: AnalyticsEvent) -> str:
    """sha256 of the canonical JSON payload — the replay-vs-misuse discriminator.

    Canonical means: sorted keys, no whitespace, and `client_ts` normalised to UTC
    microsecond precision. That last part matters — `2026-05-01T10:00:00Z` and
    `2026-05-01T12:00:00+02:00` are the same instant written two ways, and a retry that
    re-serialised the timestamp must not be mistaken for a client reusing an id. Everything
    else is hashed exactly as validated, so a genuinely different payload gets a different
    digest.
    """
    data = event.model_dump(mode="json")
    data["client_ts"] = to_wire(event.client_ts)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _to_record(event: AnalyticsEvent, *, digest: str, received_at: datetime) -> EventRecord:
    """Map a validated union member onto the flat row.

    This is the only place the wire shape (a discriminated union) and the storage shape (one
    nullable column per payload field) meet. Every field that does not belong to this event is
    passed as `None`, which is exactly what the reverse CHECK constraints in
    `0002_events.sql` demand: a column carrying a value on the wrong event is refused by the
    database, not just by pydantic. The `isinstance` ladder is also the tuple of columns each
    event owns, written in one screen — if it and the migration ever disagree, the insert
    fails loudly instead of storing a payload under the wrong event name.
    """
    return EventRecord(
        event_id=event.event_id,
        event=event.event,
        client_ts=event.client_ts,
        received_at=received_at,
        session_id=event.session_id,
        subject=event.subject,
        app_version=event.app_version,
        platform=event.platform.value,
        payload_sha256=digest,
        # envelope (ADR-008). One line rather than an `isinstance` branch: the per-event grade
        # lives in the models (`UUID` for the five, `None` for `subscription_started`,
        # optional for `paywall_impression`), so by the time a record is built the value is
        # already either a legal uuid or `None`. Re-deciding it here would be a third copy of
        # the matrix, and the third copy is the one that drifts.
        item_id=event.item_id,
        # 1. upload_started
        source=event.source.value if isinstance(event, UploadStarted) else None,
        capture_mode=event.capture_mode.value if isinstance(event, UploadStarted) else None,
        batch_index=event.batch_index if isinstance(event, UploadStarted) else None,
        # 2. prettify_approved
        decision=event.decision.value if isinstance(event, PrettifyApproved) else None,
        job_latency_ms=event.job_latency_ms if isinstance(event, PrettifyApproved) else None,
        prettify_latency_ms=(
            event.prettify_latency_ms if isinstance(event, PrettifyApproved) else None
        ),
        attrs_latency_ms=event.attrs_latency_ms if isinstance(event, PrettifyApproved) else None,
        # 3. attributes_edited
        edited_field_count=(
            event.edited_field_count if isinstance(event, AttributesEdited) else None
        ),
        fields_touched=event.fields_touched if isinstance(event, AttributesEdited) else None,
        field_names=(
            tuple(event.field_names) if isinstance(event, AttributesEdited) else None
        ),
        # 4. export_clicked / 7. export_delivered
        marketplace=(
            event.marketplace.value
            if isinstance(event, ExportClicked | ExportDelivered)
            else None
        ),
        row_count=event.row_count if isinstance(event, ExportClicked | ExportDelivered) else None,
        export_latency_ms=event.export_latency_ms if isinstance(event, ExportClicked) else None,
        bytes=event.bytes if isinstance(event, ExportDelivered) else None,
        delivery_latency_ms=(
            event.delivery_latency_ms if isinstance(event, ExportDelivered) else None
        ),
        # 5. subscription_started
        plan=event.plan.value if isinstance(event, SubscriptionStarted) else None,
        amount_cents=event.amount_cents if isinstance(event, SubscriptionStarted) else None,
        currency=event.currency if isinstance(event, SubscriptionStarted) else None,
        # 5. subscription_started + 6. paywall_impression — the one shared payload field, so
        # that an impression and the subscription it converts into can be joined on the
        # variant. Splitting it in two would delete the A/B report.
        paywall_variant=(
            event.paywall_variant.value
            if isinstance(event, SubscriptionStarted | PaywallImpression)
            else None
        ),
        # 6. paywall_impression
        paywall_trigger=event.trigger.value if isinstance(event, PaywallImpression) else None,
        plan_offered=event.plan_offered.value if isinstance(event, PaywallImpression) else None,
    )


def _too_large(observed: int | None) -> errors.ApiError:
    """413 in the standard envelope. `code` is `invalid_request`: the contract's enum is
    frozen at seven values and has no `payload_too_large`, exactly like 409."""
    details = {"limit_bytes": str(MAX_EVENT_BYTES)}
    if observed is not None:
        details["observed_bytes"] = str(observed)
    return errors.ApiError(
        STATUS_PAYLOAD_TOO_LARGE,
        ErrorCode.INVALID_REQUEST,
        f"Analytics event body must be at most {MAX_EVENT_BYTES} bytes; "
        f"{observed if observed is not None else 'more'} was too large.",
        details=details,
    )


# ---------------------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------------------
@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventAccepted,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Ingest one analytics event (idempotent on the client-generated event_id)",
)
async def ingest_event(
    request: Request,
    caller: Caller = Depends(current_caller),
) -> EventAccepted:
    """Store one funnel row. Idempotent on `event_id`.

    202 rather than 201: the row is not addressable afterwards (there is no
    `GET /v1/events/{id}`), so there is no `Location` to return, and "accepted" is the honest
    word for "this will be counted".

    The body is read and validated by hand (`parse_event`) rather than declared as a FastAPI
    parameter, because the 8 KB ceiling must be answered before validation: a 9 KB body is a
    413 whether or not it happens to parse, and FastAPI parses a declared body before the view
    function is entered. `content-length` is checked first so an oversized request is refused
    without being buffered, then the real length is checked after reading, because a chunked
    request carries no such header.

    Order of refusals is deliberate: authentication (401) → size (413) → shape (422) →
    ownership of `subject` (403) → ownership of `item_id` (404) → replay/conflict (409). Size
    before shape means an attacker cannot use validation timing on a 10 MB body as an oracle;
    ownership after shape means a caller cannot learn which subjects exist by watching 403s
    for malformed payloads, and the item check runs before the idempotency read so a replay of
    a foreign item is refused rather than answered "duplicate".
    """
    repository: Repository = web.repository_of(request)

    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            declared = int(declared_length)
        except ValueError:
            declared = None
        if declared is not None and declared > MAX_EVENT_BYTES:
            raise _too_large(declared)

    raw = await request.body()
    if len(raw) > MAX_EVENT_BYTES:
        raise _too_large(len(raw))

    event = parse_event(raw)
    bind_subject(event.subject, caller)
    bind_item(event.item_id, caller, repository)

    digest = canonical_payload_digest(event)
    received_at = utcnow()

    existing = repository.get_analytics_event(event.event_id)
    if existing is not None:
        if existing.payload_sha256 == digest:
            return EventAccepted(accepted=True, duplicate=True)
        raise errors.conflict(
            "This `event_id` was already ingested with a different payload. A reused id is a "
            "client bug: event ids must be generated once per event and reused only for "
            "retries of that same event.",
            details={"event_id": str(event.event_id)},
        )

    record = _to_record(event, digest=digest, received_at=received_at)
    if repository.append_analytics_event(record):
        return EventAccepted(accepted=True, duplicate=False)

    # The insert lost a race: another request wrote this `event_id` between the read above and
    # the insert. Re-read to decide which of the two outcomes it was — a genuine duplicate is
    # still a 202, a different payload is still a 409. The window is small but it is real, and
    # `on conflict (event_id) do nothing` is what makes it a re-read rather than a lost event.
    raced = repository.get_analytics_event(event.event_id)
    if raced is not None and raced.payload_sha256 == digest:
        return EventAccepted(accepted=True, duplicate=True)
    raise errors.conflict(
        "This `event_id` was already ingested with a different payload.",
        details={"event_id": str(event.event_id)},
    )
