/**
 * Mock data + deterministic simulation helpers for E6 (no backend needed).
 *
 * Everything here is FROZEN-FREE MOCK: nothing is random per render — values are
 * derived from a string hash so a given `item_id` always produces the same
 * attributes, the same pass/fail outcome, etc. That keeps the screens
 * reproducible for design review and lets a failure surface deterministically.
 */

import {
  CONDITIONS,
  DraftItem,
  FITS,
  GARMENT_CATEGORIES,
  ItemAttributes,
  JobState,
  PATTERNS,
  ReasonCode,
} from "./types";
import { uuidv4 } from "./uuid";

export const SAMPLE_COLORS = [
  "black",
  "white",
  "navy",
  "olive",
  "rust",
  "cream",
  "gray",
  "burgundy",
] as const;
export const SAMPLE_MATERIALS = [
  "cotton",
  "wool",
  "denim",
  "linen",
  "polyester",
  "silk",
] as const;
const SAMPLE_KEYWORDS = [
  "vintage",
  "minimal",
  "everyday",
  "cozy",
  "tailored",
  "streetwear",
] as const;
const ORIGINAL_COLORS = [
  "#cdc6bd",
  "#b9c2c9",
  "#c9bfae",
  "#bcb2c4",
  "#c2c9b9",
  "#c9b9b9",
];
const PRETTIFIED_COLORS = [
  "#f7f5f1",
  "#eef2f5",
  "#f3efe7",
  "#f1ecf4",
  "#eef3ea",
  "#f4eaea",
];

/** FNV-1a string hash → unsigned 32-bit int. Deterministic across runs. */
export function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function pick<T>(arr: readonly T[], seed: number): T {
  if (arr.length === 0) {
    throw new Error("pick() called on an empty array");
  }
  const idx = ((seed % arr.length) + arr.length) % arr.length;
  const v = arr[idx];
  // noUncheckedIndexedAccess: guard the (impossible here) undefined.
  return v as T;
}

export function makeMockAttributes(seed: string): ItemAttributes {
  const h = hashString(seed);
  const category = pick(GARMENT_CATEGORIES, h);
  const pattern = pick(PATTERNS, h >> 3);
  const fit = pick(FITS, h >> 5);
  const condition = pick(CONDITIONS, h >> 7);
  const color1 = pick(SAMPLE_COLORS, h >> 2);
  const color2 = pick(SAMPLE_COLORS, (h >> 4) + 3);
  const colors = color1 === color2 ? [color1] : [color1, color2];
  const material1 = pick(SAMPLE_MATERIALS, h >> 6);
  const keyword = pick(SAMPLE_KEYWORDS, h >> 1);

  const confidence: Record<string, number> = {
    category: 0.9 + (h % 9) / 100,
    colors: 0.95,
    pattern: 0.7 + (h % 20) / 100,
    material: 0.68,
    fit: 0.8,
  };
  const needs_review: string[] =
    (confidence.material ?? 0) < 0.75 ? ["material"] : [];

  return {
    category,
    subcategory: null,
    colors,
    pattern,
    material: [material1],
    fit,
    era: null,
    brand: null,
    size_label: null,
    condition,
    defects: [],
    measurements: null,
    keywords: [keyword],
    confidence,
    needs_review,
    model_version: "vlm:mock:1.0",
  };
}

export function defaultAttributes(): ItemAttributes {
  return {
    category: "other",
    subcategory: null,
    colors: ["unknown"],
    pattern: "unknown",
    material: [],
    fit: "unknown",
    era: null,
    brand: null,
    size_label: null,
    condition: "good",
    defects: [],
    measurements: null,
    keywords: [],
    confidence: {},
    needs_review: [],
    model_version: "unset",
  };
}

export function createDraftItem(
  mode: "single" | "batch",
  batchIndex: number | undefined,
  id: string,
): DraftItem {
  const h = hashString(id);
  return {
    item_id: id,
    capture_mode: mode,
    batch_index: batchIndex,
    originalColor: pick(ORIGINAL_COLORS, h),
    prettifiedColor: pick(PRETTIFIED_COLORS, h >> 2),
    status: "queued",
    job_latency_ms: 0,
    prettify_latency_ms: 0,
    attrs_latency_ms: 0,
    on_model_state: "not_requested",
    on_model_urls: [],
    vton_status: "queued",
    created_at: new Date().toISOString(),
    retry_count: 0,
  };
}

// `bad_photo`, `no_garment`, `low_confidence`, `internal` are valid full-pipeline
// job failures. `vton_rejected` is vton-specific and is NOT used here.
const FULL_JOB_REASONS: readonly ReasonCode[] = [
  "bad_photo",
  "no_garment",
  "low_confidence",
  "internal",
];

/** Simulate a full prettify+attributes job. ~1 in 5 items fails deterministically. */
export function simulateJob(seed: string): {
  status: JobState;
  reason_code?: ReasonCode;
} {
  const h = hashString(seed);
  if (h % 5 === 0) {
    const reason = FULL_JOB_REASONS[h % FULL_JOB_REASONS.length] ?? "internal";
    return { status: "failed", reason_code: reason };
  }
  return { status: "done" };
}

/** Simulate a vton (on-model) job. ~1 in 7 fails with vton_rejected. */
export function simulateVtonFail(seed: string): boolean {
  return hashString(seed) % 7 === 0;
}

/** Latencies (ms) are mock client/server timings for the funnel events. */
export function mockLatencies(seed: string): {
  job_latency_ms: number;
  prettify_latency_ms: number;
  attrs_latency_ms: number;
} {
  const h = hashString(seed);
  return {
    job_latency_ms: 1100 + (h % 600),
    prettify_latency_ms: 850 + (h % 500),
    attrs_latency_ms: 320 + (h % 280),
  };
}

export function newItemId(): string {
  return uuidv4();
}
