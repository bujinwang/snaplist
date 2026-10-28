/**
 * SnapList analytics vocabulary — frozen. Mirror of docs/CONTRACTS.md §7.
 *
 * This lives in the shared package because two consumers need the same strings:
 * `apps/mobile` (emitter) and whatever dashboard reads the events table. It is NOT part of
 * the API wire contract (`CONTRACT_VERSION`), so it carries its own version — the mobile
 * client ships on its own schedule and an event rename must not force an API version bump.
 *
 * Hard rules (docs/METRICS.md §3, §4):
 *  1. Seven event names. Frozen. Adding one is a version bump here, not an improvisation in a
 *     screen component.
 *  2. No PII, ever. No free text, no attribute values, no image content, no email, no
 *     device advertising id. Field *names* are allowed, field *values* are not.
 *  3. Emitted in the same PR as the feature that produces the step. An unmeasured feature is
 *     a feature we cannot defend.
 */

import { z } from "zod";

export const EVENTS_VERSION = "1.2.0";

/**
 * Three different threes live in this product. Naming them apart is the whole point: a
 * dashboard that averages them tells a lie. Values are documented here for labelling only —
 * the *enforcement* authority is `apps/api/settings.py`, and the client must read the real
 * number from `GET /v1/usage` (`Usage.items_included`), never from these constants.
 */
export const ACTIVATION_ITEMS_EXPORTED_7D = 3; // activation: 3 exports within 7 days
export const SESSION_EXPORT_BURST_60M = 3; // QA proxy: 3 exports within a 60-minute window
export const FREE_TIER_ITEM_LIMIT = 3; // billing: free plan allowance per month

export const METRIC_NAMES = {
  activation: "activation_items_exported_7d",
  sessionBurst: "session_export_burst_60m",
  freeLimit: "free_tier_item_limit",
} as const;

/** Server-assigned, persisted on the user row, sticky for the life of the account. */
export const PAYWALL_VARIANTS = ["a", "b", "c"] as const;
export type PaywallVariant = (typeof PAYWALL_VARIANTS)[number];

export const PAYWALL_TRIGGERS = ["upload_4th", "on_model", "export_limit"] as const;
export type PaywallTrigger = (typeof PAYWALL_TRIGGERS)[number];

export const ANALYTICS_EVENTS = [
  "upload_started",
  "prettify_approved",
  "attributes_edited",
  "export_clicked",
  "subscription_started",
  "paywall_impression",
  "export_delivered",
] as const;
export type AnalyticsEventName = (typeof ANALYTICS_EVENTS)[number];

export const MARKETPLACE_IDS = ["ebay", "poshmark", "vinted"] as const;

const Envelope = z
  .object({
    event: z.enum(ANALYTICS_EVENTS),
    /** ISO 8601, client clock, UTC. Server also stamps received_at; the skew is expected. */
    client_ts: z.string().datetime(),
    session_id: z.string().min(1),
    /** Opaque subject: `anon_<uuid>` before signup, `usr_<uuid>` after. Never an email. */
    subject: z.string().min(1),
    app_version: z.string().min(1),
    platform: z.enum(["ios", "android", "web"]),
  })
  // .strict() on purpose: an unknown key is how PII gets in. Better to drop the whole event
  // in development than to ship a payload with `email` in it and find out from a lawyer.
  .strict();

/** 1. the shutter fired, or a library photo was picked */
export const UploadStarted = Envelope.extend({
  event: z.literal("upload_started"),
  source: z.enum(["camera", "library"]),
  capture_mode: z.enum(["single", "batch"]),
  batch_index: z.number().int().min(0).optional(),
});

/**
 * 2. the seller accepted (or rejected) the prettified image.
 *
 * Carries all three latency proxies, not just its own: a seller who never edits an attribute
 * would otherwise never emit `attributes_edited`, and tool time would be silently
 * under-counted. Measuring at "everything is on screen" closes that gap.
 */
export const PrettifyApproved = Envelope.extend({
  event: z.literal("prettify_approved"),
  decision: z.enum(["approve", "retry", "remove_bg_waitlist"]),
  /** server-side job duration, echoed back — audits the pipeline */
  job_latency_ms: z.number().int().min(0),
  /** client: shutter -> prettified image on screen (includes upload + queue) */
  prettify_latency_ms: z.number().int().min(0),
  /** client: attributes visible and rendered in the review sheet */
  attrs_latency_ms: z.number().int().min(0),
});

/** 3. the seller touched the attribute sheet. Counts reading as well as changing. */
export const AttributesEdited = Envelope.extend({
  event: z.literal("attributes_edited"),
  /** fields whose value actually changed */
  edited_field_count: z.number().int().min(0),
  /**
   * fields the seller interacted with, including confirm-only (focus then blur, or an
   * explicit confirm). Repeat visits to one field count once. This is the honest workload
   * number; `edited_field_count` is a lower bound.
   */
  fields_touched: z.number().int().min(0),
  /** field NAMES only ("colors", "size_label"). Never values. */
  field_names: z.array(z.string().regex(/^[a-z][a-z0-9_]*$/)).default([]),
});

/** 4. the seller asked for a marketplace file */
export const ExportClicked = Envelope.extend({
  event: z.literal("export_clicked"),
  marketplace: z.enum(MARKETPLACE_IDS),
  row_count: z.number().int().min(1),
  /** client: tap -> rows previewed on screen */
  export_latency_ms: z.number().int().min(0),
});

/** 5. money moved */
export const SubscriptionStarted = Envelope.extend({
  event: z.literal("subscription_started"),
  plan: z.enum(["starter", "pro"]),
  paywall_variant: z.enum(PAYWALL_VARIANTS),
  amount_cents: z.number().int().min(0),
  currency: z.string().length(3),
});

/** 6. the paywall was shown (whether or not it converted) */
export const PaywallImpression = Envelope.extend({
  event: z.literal("paywall_impression"),
  paywall_variant: z.enum(PAYWALL_VARIANTS),
  trigger: z.enum(PAYWALL_TRIGGERS),
  plan_offered: z.enum(["starter", "pro"]),
});

/**
 * 7. the seller actually HAS the marketplace file (write or download finished).
 *
 * Attempt is not success. `export_clicked` stays "asked for a file"; this one is "got one".
 * `activation_items_exported_7d` counts THIS event, never the click — counting attempts makes
 * activation an upper bound, because a timeout or a disk-write failure would register as an
 * activation. The gap between the two events is the export-failure rate and is a required
 * observable; never drop unmatched clicks. See docs/CONTRACTS.md §7.
 */
export const ExportDelivered = Envelope.extend({
  event: z.literal("export_delivered"),
  marketplace: z.enum(MARKETPLACE_IDS),
  row_count: z.number().int().min(1),
  bytes: z.number().int().min(0),
  /** client: file write/download finished -> byte length known */
  delivery_latency_ms: z.number().int().min(0),
});

export const AnalyticsEvent = z.discriminatedUnion("event", [
  UploadStarted,
  PrettifyApproved,
  AttributesEdited,
  ExportClicked,
  SubscriptionStarted,
  PaywallImpression,
  ExportDelivered,
]);

export type UploadStarted = z.infer<typeof UploadStarted>;
export type PrettifyApproved = z.infer<typeof PrettifyApproved>;
export type AttributesEdited = z.infer<typeof AttributesEdited>;
export type ExportClicked = z.infer<typeof ExportClicked>;
export type SubscriptionStarted = z.infer<typeof SubscriptionStarted>;
export type PaywallImpression = z.infer<typeof PaywallImpression>;
export type ExportDelivered = z.infer<typeof ExportDelivered>;
export type AnalyticsEvent = z.infer<typeof AnalyticsEvent>;

/**
 * tool time = prettify_latency_ms + attrs_latency_ms + export_latency_ms for one item.
 * p50 <= 35s, p90 <= 60s (docs/METRICS.md §3.3). This is the number PLAN.md gates on;
 * end-to-end minutes per item are not measurable by a script and are never an acceptance.
 */
export const TOOL_TIME_P50_MS = 35_000;
export const TOOL_TIME_P90_MS = 60_000;
