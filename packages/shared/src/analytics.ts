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
 *     screen component. `1.3.0` added a field, not an event: `event_id`, the idempotency key
 *     for `POST /v1/events` (ADR-007). `1.4.0` added `item_id`, again a field and not an
 *     event — and, like `event_id`, it moved into the contract because the *server* needed it
 *     and the shared schema had no way to express it (ADR-008).
 *  2. No PII, ever. No free text, no attribute values, no image content, no email, no
 *     device advertising id. Field *names* are allowed, field *values* are not.
 *  3. Emitted in the same PR as the feature that produces the step. An unmeasured feature is
 *     a feature we cannot defend.
 *  4. `item_id` is **graded per event**, not globally optional. Five events cannot be
 *     reported without it, `paywall_impression` may omit it, `subscription_started` must not
 *     carry it. A per-event schema is the only place that distinction can live: an envelope
 *     that says "optional" everywhere is an envelope that lets an emitter forget the one
 *     field every per-item metric joins on.
 */

import { z } from "zod";

export const EVENTS_VERSION = "1.4.0";

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
    /**
     * Client-generated uuid, one per event, reused only when retrying that same event.
     * It is the idempotency key for `POST /v1/events`: a retry with the same id is a no-op,
     * while a reused id carrying different content is a 409, not a silent overwrite.
     * Added in `EVENTS_VERSION = 1.3.0` (ADR-007) — the API needed an id and the envelope did
     * not have one, so a client validating with this schema could not have built a sendable body.
     */
    event_id: z.string().uuid(),
    event: z.enum(ANALYTICS_EVENTS),
    /**
     * The item this event is about — the join key every per-item metric needs.
     *
     * Added in `EVENTS_VERSION = 1.4.0` (ADR-008). Declared here as optional because the
     * envelope cannot know which event it is attached to; the per-event schemas below then
     * re-declare it and make the grade explicit:
     *   * **required** — `upload_started`, `prettify_approved`, `attributes_edited`,
     *     `export_clicked`, `export_delivered`. Without it, `tool_time_ms` per item,
     *     `activation_items_exported_7d` (deduped on one row per item whose final
     *     `prettify_approved` is an `approve`) and the export-failure rate are all
     *     uncomputable: three reports would degrade into "count of events", which is a
     *     different and misleading number.
     *   * **optional** — `paywall_impression`. Two of its three triggers always have an item
     *     (`on_model`, `export_limit`), but a paywall raised from the settings screen has
     *     none, and none of the paywall metrics segment by item. Requiring it would either
     *     block a legal impression or push an emitter to invent an item id, and an invented
     *     join key is worse than a missing one.
     *   * **forbidden** — `subscription_started`. It is emitted by the API from a Stripe
     *     webhook, where no item is in scope. A subscription is not about one garment, and a
     *     plausible-looking id attached to it would silently attribute revenue to whichever
     *     item happened to be on screen.
     *
     * `null` is accepted by the envelope because the python mirror is `UUID | None = None`;
     * an event that requires an item still rejects `null` at its own schema.
     */
    item_id: z.string().uuid().nullable().optional(),
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
  /** required: an upload with no item cannot be placed in any per-item funnel (ADR-008) */
  item_id: z.string().uuid(),
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
  /**
   * required: this event is where per-item tool time is measured, and the retry rate is
   * "several rows, one item". Without the id, an item retried three times looks like three
   * items approved (ADR-008).
   */
  item_id: z.string().uuid(),
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
  /** required: workload (`fields_touched`) is a per-item number, reported per item (ADR-008) */
  item_id: z.string().uuid(),
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
  /** required: it is half of the export-failure rate, which is computed per item (ADR-008) */
  item_id: z.string().uuid(),
  marketplace: z.enum(MARKETPLACE_IDS),
  row_count: z.number().int().min(1),
  /** client: tap -> rows previewed on screen */
  export_latency_ms: z.number().int().min(0),
});

/**
 * 5. money moved.
 *
 * `item_id` is **forbidden** here, and the shape says so rather than a comment: it is
 * `z.null().optional()`, so an emitter that attaches an item id gets a parse error instead
 * of a row. The reason is factual, not stylistic — this event is emitted by the API from a
 * Stripe webhook, where no item exists. A subscription is not about one garment, and a
 * plausible id attached to it would attribute revenue to whichever item happened to be on
 * the seller's screen (ADR-008). The python model and the table's reverse CHECK pin the same
 * rule, which is deliberate duplication: this one catches the client, that one catches us.
 */
export const SubscriptionStarted = Envelope.extend({
  event: z.literal("subscription_started"),
  item_id: z.null().optional(),
  plan: z.enum(["starter", "pro"]),
  paywall_variant: z.enum(PAYWALL_VARIANTS),
  amount_cents: z.number().int().min(0),
  currency: z.string().length(3),
});

/**
 * 6. the paywall was shown (whether or not it converted).
 *
 * The one event where `item_id` stays optional — inherited from the envelope, not
 * re-declared. `trigger=on_model` and `trigger=export_limit` always have an item; a paywall
 * opened from the settings screen has none, and no paywall metric segments by item. Making it
 * required would block a legal impression; making it "must be null" would forbid the two
 * triggers that do have one (ADR-008).
 */
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
  /**
   * required: `activation_items_exported_7d` is "3 **items** delivered in 7 days", deduped on
   * this id. Without it the metric silently becomes "3 deliveries", which a seller who
   * re-downloads the same file reaches without activating (ADR-008).
   */
  item_id: z.string().uuid(),
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
