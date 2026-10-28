/**
 * SnapList wire contracts — zod mirror of `apps/api/schemas.py`.
 *
 * MIRROR RULE: same field names (snake_case, no aliases), same types, same nullability,
 * same enum literals in the same order. `apps/api/tests/test_contract_parity.py` fails the
 * build when the two drift, so do not "fix" a mismatch by editing this file alone:
 * change docs/CONTRACTS.md, bump CONTRACT_VERSION, write an ADR.
 *
 * Mapping rules (see the python docstring for the other direction):
 *   str | None = None   -> .nullable().optional()
 *   field with default  -> .default(<same value>)
 *   float  (pydantic)   -> z.number()
 *   int                 -> z.number().int()
 *   datetime            -> z.string().datetime()
 *   UUID                -> z.string().uuid()
 */

import { z } from "zod";

export const CONTRACT_VERSION = "1.1.0";

/* -------------------------------------------------------------------------- */
/* Enums — literals must match the python StrEnum member order                 */
/* -------------------------------------------------------------------------- */

export const GARMENT_CATEGORIES = [
  "top",
  "bottom",
  "dress",
  "outerwear",
  "shoe",
  "accessory",
  "other",
] as const;

export const PATTERNS = [
  "solid",
  "striped",
  "plaid",
  "floral",
  "graphic",
  "animal",
  "geometric",
  "tie_dye",
  "knit",
  "other",
  "unknown",
] as const;

export const FITS = [
  "slim",
  "regular",
  "relaxed",
  "oversized",
  "cropped",
  "high_waisted",
  "unknown",
] as const;

export const CONDITIONS = [
  "new_with_tags",
  "new_without_tags",
  "excellent",
  "good",
  "fair",
  "poor",
] as const;

export const DEFECTS = [
  "stain",
  "hole",
  "tear",
  "pilling",
  "fading",
  "missing_button",
  "broken_zipper",
  "loose_seam",
  "alteration",
  "other",
] as const;

export const MEASUREMENT_SOURCES = ["estimated", "seller_provided"] as const;

export const JOB_KINDS = ["full", "prettify", "attributes", "vton", "export"] as const;

export const JOB_STATES = ["queued", "running", "done", "failed"] as const;

export const REASON_CODES = [
  "bad_photo",
  "no_garment",
  "low_confidence",
  "vton_rejected",
  "internal",
] as const;

export const ERROR_CODES = [
  "unauthorized",
  "forbidden",
  "not_found",
  "invalid_request",
  "quota_exceeded",
  "not_implemented",
  "internal",
] as const;

export const PLAN_TIERS = ["free", "starter", "pro"] as const;

export const MARKETPLACES = ["ebay", "poshmark", "vinted"] as const;

/* -------------------------------------------------------------------------- */
/* Schemas                                                                     */
/* -------------------------------------------------------------------------- */

export const Measurements = z
  .object({
    chest_cm: z.number().nullable().optional(),
    length_cm: z.number().nullable().optional(),
    waist_cm: z.number().nullable().optional(),
    rise_cm: z.number().nullable().optional(),
    inseam_cm: z.number().nullable().optional(),
    sleeve_cm: z.number().nullable().optional(),
    source: z.enum(MEASUREMENT_SOURCES).default("estimated"),
  })
  .strict();

export const ItemAttributes = z
  .object({
    category: z.enum(GARMENT_CATEGORIES),
    subcategory: z.string().nullable().optional(),
    /** primary colour first, lowercase, 1..3 entries */
    colors: z.array(z.string()).min(1).max(3),
    pattern: z.enum(PATTERNS).default("unknown"),
    /** lowercase fibres, no percentages */
    material: z.array(z.string()).default([]),
    fit: z.enum(FITS).default("unknown"),
    era: z.string().nullable().optional(),
    /** only when the mark is legible in the photo */
    brand: z.string().nullable().optional(),
    /** exactly as printed: "M", "W30 L32", "EU 39" */
    size_label: z.string().nullable().optional(),
    condition: z.enum(CONDITIONS).default("good"),
    defects: z.array(z.enum(DEFECTS)).default([]),
    measurements: Measurements.nullable().optional(),
    keywords: z.array(z.string()).max(10).default([]),
    /** field name -> 0.0..1.0 */
    confidence: z.record(z.string(), z.number()).default({}),
    needs_review: z.array(z.string()).default([]),
    model_version: z.string().default("unset"),
  })
  .strict();

export const AssetPack = z
  .object({
    item_id: z.string().uuid(),
    prettified_url: z.string().nullable().optional(),
    cutout_url: z.string().nullable().optional(),
    on_model_urls: z.array(z.string()).default([]),
    attributes: ItemAttributes.nullable().optional(),
  })
  .strict();

export const Job = z
  .object({
    id: z.string().uuid(),
    item_id: z.string().uuid(),
    kind: z.enum(JOB_KINDS).default("full"),
    state: z.enum(JOB_STATES).default("queued"),
    reason_code: z.enum(REASON_CODES).nullable().optional(),
    attempts: z.number().int().default(0),
    progress: z.number().default(0),
    error_detail: z.string().nullable().optional(),
    created_at: z.string().datetime(),
    updated_at: z.string().datetime(),
    started_at: z.string().datetime().nullable().optional(),
    finished_at: z.string().datetime().nullable().optional(),
  })
  .strict();

export const CreateItemResponse = z
  .object({
    item_id: z.string().uuid(),
    job_id: z.string().uuid(),
    upload_url: z.string().nullable().optional(),
  })
  .strict();

export const ErrorBody = z
  .object({
    code: z.enum(ERROR_CODES),
    message: z.string(),
    retry_after_seconds: z.number().int().nullable().optional(),
    details: z.record(z.string(), z.string()).nullable().optional(),
  })
  .strict();

export const ErrorResponse = z.object({ error: ErrorBody }).strict();

export const Usage = z
  .object({
    plan: z.enum(PLAN_TIERS),
    period_start: z.string().datetime(),
    period_end: z.string().datetime(),
    items_included: z.number().int(),
    items_used: z.number().int(),
    items_remaining: z.number().int(),
    on_model_included: z.number().int().default(0),
    on_model_used: z.number().int().default(0),
  })
  .strict();

export const CheckoutRequest = z
  .object({
    plan: z.enum(["starter", "pro"]),
    success_url: z.string(),
    cancel_url: z.string(),
  })
  .strict();

export const CheckoutResponse = z
  .object({
    checkout_url: z.string(),
    session_id: z.string(),
  })
  .strict();

export const ExportRequest = z
  .object({
    marketplace: z.enum(MARKETPLACES),
    item_ids: z.array(z.string().uuid()).min(1).max(200),
  })
  .strict();

/* -------------------------------------------------------------------------- */
/* POST /v1/exports — the 200 JSON body (apps/api/responses.py mirrors this)   */
/* -------------------------------------------------------------------------- */

/**
 * Why a mapper refused an item. Mirrors `ExportRefusalReason` in `apps/api/responses.py`.
 *
 * Deliberately its own enum, NOT a subset of `ReasonCode`. They answer different questions — a
 * `Job` failure is retryable by the pipeline, an export refusal is fixed by the seller — and
 * sharing one enum would let `insufficient_data` inherit retry semantics it does not have.
 * See docs/CONTRACTS.md §5, "Export refusals are not job failures".
 */
export const EXPORT_REFUSAL_REASONS = ["insufficient_data"] as const;
export const ExportRefusalReason = z.enum(EXPORT_REFUSAL_REASONS);

export const ExportedRowModel = z
  .object({
    item_id: z.string(),
    row_index: z.number().int().min(0),
  })
  .strict();

export const RefusedRowModel = z
  .object({
    item_id: z.string(),
    reason: ExportRefusalReason,
    fields: z.array(z.string()),
  })
  .strict();

export const ExportBatchResponse = z
  .object({
    csv: z.string(),
    // Empty array, never omitted: "nothing to fill" and "we did not tell you" must be
    // distinguishable by the client.
    requires_manual_columns: z.array(z.string()),
    exported: z.array(ExportedRowModel),
    // Non-empty on a partial export, which is still a 200. Only "every row refused" is a 422.
    refused: z.array(RefusedRowModel),
    warnings: z.array(z.string()),
  })
  .strict();

/* -------------------------------------------------------------------------- */
/* Inferred types — what apps/mobile and packages/shared commit against        */
/* -------------------------------------------------------------------------- */

export type Measurements = z.infer<typeof Measurements>;
export type ItemAttributes = z.infer<typeof ItemAttributes>;
export type AssetPack = z.infer<typeof AssetPack>;
export type Job = z.infer<typeof Job>;
export type CreateItemResponse = z.infer<typeof CreateItemResponse>;
export type ErrorBody = z.infer<typeof ErrorBody>;
export type ErrorResponse = z.infer<typeof ErrorResponse>;
export type Usage = z.infer<typeof Usage>;
export type CheckoutRequest = z.infer<typeof CheckoutRequest>;
export type CheckoutResponse = z.infer<typeof CheckoutResponse>;
export type ExportRequest = z.infer<typeof ExportRequest>;
export type ExportRefusalReason = z.infer<typeof ExportRefusalReason>;
export type ExportedRowModel = z.infer<typeof ExportedRowModel>;
export type RefusedRowModel = z.infer<typeof RefusedRowModel>;
export type ExportBatchResponse = z.infer<typeof ExportBatchResponse>;

export type GarmentCategory = (typeof GARMENT_CATEGORIES)[number];
export type Pattern = (typeof PATTERNS)[number];
export type Fit = (typeof FITS)[number];
export type Condition = (typeof CONDITIONS)[number];
export type Defect = (typeof DEFECTS)[number];
export type JobKind = (typeof JOB_KINDS)[number];
export type JobState = (typeof JOB_STATES)[number];
export type ReasonCode = (typeof REASON_CODES)[number];
export type ErrorCode = (typeof ERROR_CODES)[number];
export type PlanTier = (typeof PLAN_TIERS)[number];
export type Marketplace = (typeof MARKETPLACES)[number];
