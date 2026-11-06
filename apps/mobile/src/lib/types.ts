/**
 * Shared domain types for the mobile app (E6-T2..T6).
 *
 * These are a hand-written TypeScript mirror of the frozen contract in
 * `docs/CONTRACTS.md` §1 (`ItemAttributes`), §3 (`Job` state machine) and §4
 * (`reason_code`). They are NOT imported from `@snaplist/shared` (that package
 * cannot be fetched in this workspace — see DEPS.md) so the app builds standalone.
 * The enumerations below must keep the SAME literal order as the contract; the
 * order is part of the wire contract.
 */

// ---------------------------------------------------------------------------
// Enumerations (order is part of the contract — do not reorder)
// ---------------------------------------------------------------------------

export const GARMENT_CATEGORIES = [
  "top",
  "bottom",
  "dress",
  "outerwear",
  "shoe",
  "accessory",
  "other",
] as const;
export type GarmentCategory = (typeof GARMENT_CATEGORIES)[number];

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
export type Pattern = (typeof PATTERNS)[number];

export const FITS = [
  "slim",
  "regular",
  "relaxed",
  "oversized",
  "cropped",
  "high_waisted",
  "unknown",
] as const;
export type Fit = (typeof FITS)[number];

export const CONDITIONS = [
  "new_with_tags",
  "new_without_tags",
  "excellent",
  "good",
  "fair",
  "poor",
] as const;
export type Condition = (typeof CONDITIONS)[number];

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
export type Defect = (typeof DEFECTS)[number];

// ---------------------------------------------------------------------------
// Nested structures
// ---------------------------------------------------------------------------

export type MeasurementSource = "estimated" | "seller_provided";

export interface Measurements {
  chest_cm?: number | null;
  length_cm?: number | null;
  waist_cm?: number | null;
  rise_cm?: number | null;
  inseam_cm?: number | null;
  sleeve_cm?: number | null;
  source: MeasurementSource;
}

export interface ItemAttributes {
  category: GarmentCategory;
  subcategory?: string | null;
  colors: string[];
  pattern: Pattern;
  material: string[];
  fit: Fit;
  era?: string | null;
  brand?: string | null;
  size_label?: string | null;
  condition: Condition;
  defects: Defect[];
  measurements?: Measurements | null;
  keywords: string[];
  confidence: Record<string, number>;
  needs_review: string[];
  model_version: string;
}

// ---------------------------------------------------------------------------
// Job / failure vocabulary
// ---------------------------------------------------------------------------

export type ReasonCode =
  | "bad_photo"
  | "no_garment"
  | "low_confidence"
  | "vton_rejected"
  | "internal";

export type JobState = "queued" | "running" | "done" | "failed";

export type JobKind = "full" | "prettify" | "attributes" | "vton" | "export";

export type OnModelState = "not_requested" | "rendering" | "ready";

export type CaptureMode = "single" | "batch";

/**
 * A captured photo as it lives in the local draft queue (E6-T5). It is the
 * single source of truth for every screen: capture enqueues it, review reads
 * `attributes`, on-model reads `on_model_state`, export reads `attributes`, and
 * the draft list persists it. `status` follows the contract's `Job` state machine
 * (queued → running → done | failed). A `blocked_by_quota` item is a captured
 * photo that hit the free-tier wall: it is SAVED (never discarded) but not
 * processed — the paywall interacts with drafts, not the camera (PLAN E6-T5).
 */
export interface DraftItem {
  item_id: string;
  capture_mode: CaptureMode;
  batch_index?: number;
  /** Mock "photo": a flat color stands in for the original capture. */
  originalColor: string;
  /** Mock "prettified" result: a flat color stands in for the prettified image. */
  prettifiedColor: string;
  status: JobState;
  reason_code?: ReasonCode;
  attributes?: ItemAttributes;
  /** Server-side job latency (ms) — carried on `prettify_approved`. */
  job_latency_ms: number;
  /** Client shutter→image-on-screen latency (ms). */
  prettify_latency_ms: number;
  /** Client attributes-rendered latency (ms). */
  attrs_latency_ms: number;
  on_model_state: OnModelState;
  on_model_urls: string[];
  vton_status: JobState;
  vton_reason_code?: ReasonCode;
  created_at: string;
  retry_count: number;
  /** True when this capture hit the free-tier quota wall (photo still saved). */
  blocked_by_quota?: boolean;
}
