"""SnapList wire contracts — Pydantic v2 source of truth.

This module is normative. `packages/shared/src/schemas.ts` mirrors every model here
field-for-field (same names, same types, same nullability). Changing anything in this
file requires an ADR in `docs/` and a version bump of `CONTRACT_VERSION` — see
docs/CONTRACTS.md.

Type-mapping rules (apply to every field, both directions):
    str / int / float / bool        -> same primitive
    X | None = None                 -> nullable().optional()
    list[T]                         -> z.array(T)
    dict[str, float]                -> z.record(z.string(), z.number())
    StrEnum                        -> z.enum([...same literals, same order...])
    datetime (ISO 8601, UTC, "Z")   -> z.string().datetime()
    uuid.UUID (lowercase string)    -> z.string().uuid()

Naming: snake_case on the wire in both languages. No camelCase aliases — that is the
single most common source of contract drift.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.1.0"


# --------------------------------------------------------------------------------------
# Enums — literals are duplicated verbatim in packages/shared/src/schemas.ts
# --------------------------------------------------------------------------------------
class GarmentCategory(StrEnum):
    TOP = "top"
    BOTTOM = "bottom"
    DRESS = "dress"
    OUTERWEAR = "outerwear"
    SHOE = "shoe"
    ACCESSORY = "accessory"
    OTHER = "other"


class Pattern(StrEnum):
    SOLID = "solid"
    STRIPED = "striped"
    PLAID = "plaid"
    FLORAL = "floral"
    GRAPHIC = "graphic"
    ANIMAL = "animal"
    GEOMETRIC = "geometric"
    TIE_DYE = "tie_dye"
    KNIT = "knit"
    OTHER = "other"
    UNKNOWN = "unknown"


class Fit(StrEnum):
    SLIM = "slim"
    REGULAR = "regular"
    RELAXED = "relaxed"
    OVERSIZED = "oversized"
    CROPPED = "cropped"
    HIGH_WAISTED = "high_waisted"
    UNKNOWN = "unknown"


class Condition(StrEnum):
    NEW_WITH_TAGS = "new_with_tags"
    NEW_WITHOUT_TAGS = "new_without_tags"
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class Defect(StrEnum):
    STAIN = "stain"
    HOLE = "hole"
    TEAR = "tear"
    PILLING = "pilling"
    FADING = "fading"
    MISSING_BUTTON = "missing_button"
    BROKEN_ZIPPER = "broken_zipper"
    LOOSE_SEAM = "loose_seam"
    ALTERATION = "alteration"
    OTHER = "other"


class MeasurementSource(StrEnum):
    ESTIMATED = "estimated"
    SELLER_PROVIDED = "seller_provided"


class JobKind(StrEnum):
    FULL = "full"  # segment + prettify + attributes
    PRETTIFY = "prettify"
    ATTRIBUTES = "attributes"
    VTON = "vton"
    EXPORT = "export"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ReasonCode(StrEnum):
    BAD_PHOTO = "bad_photo"
    NO_GARMENT = "no_garment"
    LOW_CONFIDENCE = "low_confidence"
    VTON_REJECTED = "vton_rejected"
    INTERNAL = "internal"


class ErrorCode(StrEnum):
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    QUOTA_EXCEEDED = "quota_exceeded"
    NOT_IMPLEMENTED = "not_implemented"
    INTERNAL = "internal"


class PlanTier(StrEnum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"


class Marketplace(StrEnum):
    EBAY = "ebay"
    POSHMARK = "poshmark"
    VINTED = "vinted"


# --------------------------------------------------------------------------------------
# ItemAttributes — the shape the VLM must emit and the seller may edit
# --------------------------------------------------------------------------------------
class Measurements(BaseModel):
    """Flat-lay measurements in centimetres. Absent values are unknown, never guessed as 0."""

    model_config = ConfigDict(extra="forbid")

    chest_cm: float | None = None
    length_cm: float | None = None
    waist_cm: float | None = None
    rise_cm: float | None = None
    inseam_cm: float | None = None
    sleeve_cm: float | None = None
    source: MeasurementSource = MeasurementSource.ESTIMATED


class ItemAttributes(BaseModel):
    """Structured attributes for one garment.

    Attribution policy (enforced by packages/vision/attrs.py and the listing generator):
    `brand` may only be populated when the mark is legible in the photo; `material` may only
    contain fibres with visual or care-label evidence. Everything else is a judgement call
    and is allowed to be wrong — that is what `confidence` + `needs_review` are for.
    """

    model_config = ConfigDict(extra="forbid")

    category: GarmentCategory
    subcategory: str | None = None  # free text: "crewneck tee", "straight leg jean"
    colors: list[str] = Field(min_length=1, max_length=3)  # primary first, lowercase
    pattern: Pattern = Pattern.UNKNOWN
    material: list[str] = Field(default_factory=list)  # lowercase fibres, no percentages
    fit: Fit = Fit.UNKNOWN
    era: str | None = None  # "90s", "y2k", "modern" — free text
    brand: str | None = None
    size_label: str | None = None  # exactly as printed: "M", "W30 L32", "EU 39"
    condition: Condition = Condition.GOOD
    defects: list[Defect] = Field(default_factory=list)
    measurements: Measurements | None = None
    keywords: list[str] = Field(default_factory=list, max_length=10)
    confidence: dict[str, float] = Field(default_factory=dict)  # field name -> 0.0..1.0
    needs_review: list[str] = Field(default_factory=list)  # field names below threshold
    model_version: str = "unset"  # adapter + model id that produced this


# --------------------------------------------------------------------------------------
# AssetPack — the GET /v1/items/{id} response
# --------------------------------------------------------------------------------------
class AssetPack(BaseModel):
    """Exactly the five keys promised in BRIEF.md "Contracts". Do not add fields here
    without an ADR; clients cache this shape on disk."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    prettified_url: str | None = None  # null while the job has not finished
    cutout_url: str | None = None
    on_model_urls: list[str] = Field(default_factory=list)
    attributes: ItemAttributes | None = None


# --------------------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------------------
class Job(BaseModel):
    """State machine: queued -> running -> done | failed(reason_code).

    See docs/CONTRACTS.md for the full transition table, retry and refund semantics.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    item_id: UUID
    kind: JobKind = JobKind.FULL
    state: JobState = JobState.QUEUED
    reason_code: ReasonCode | None = None
    attempts: int = 0
    progress: float = 0.0  # 0.0..1.0, for the mobile progress bar only
    error_detail: str | None = None  # human readable, never shown to the seller verbatim
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CreateItemResponse(BaseModel):
    """POST /v1/items — 202 only."""

    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    job_id: UUID
    upload_url: str | None = None  # signed Supabase Storage URL when using direct upload


# --------------------------------------------------------------------------------------
# Errors — one envelope for every non-2xx response
# --------------------------------------------------------------------------------------
class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    retry_after_seconds: int | None = None
    details: dict[str, str] | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ErrorBody


# --------------------------------------------------------------------------------------
# Usage / billing (E8 surface, defined now so mobile and web can code against it)
# --------------------------------------------------------------------------------------
class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: PlanTier
    period_start: datetime
    period_end: datetime
    items_included: int
    items_used: int
    items_remaining: int
    on_model_included: int = 0
    on_model_used: int = 0


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Literal["starter", "pro"]
    success_url: str
    cancel_url: str


class CheckoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkout_url: str
    session_id: str


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketplace: Marketplace
    item_ids: list[UUID] = Field(min_length=1, max_length=200)
