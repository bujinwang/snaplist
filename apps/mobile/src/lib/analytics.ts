/**
 * Analytics emitter — MOBILE side.
 *
 * This is a *local typed mirror* of the frozen vocabulary in `packages/shared/src/analytics.ts`
 * (docs/CONTRACTS.md §7). It is dependency-free so the app builds without `@snaplist/shared`
 * installed (the workspace currently can't fetch it). TODO(E6): once `pnpm install` links
 * `@snaplist/shared`, replace this with `import { AnalyticsEvent } from "@snaplist/shared"` and
 * validate through the shared zod schema — the two must never drift.
 *
 * Hard rules (docs/METRICS.md): no PII, no free text, field *names* only. A malformed event
 * fails loudly in dev; in prod it is dropped with a console error and never breaks the UX.
 */

import { appVersion, config, hasApi, PlatformName } from "./env";
import { uuidv4 } from "./uuid";
import { Platform } from "react-native";

export type MarketplaceId = "ebay" | "poshmark" | "vinted";
export type PaywallVariant = "a" | "b" | "c";
export type PaywallTrigger = "upload_4th" | "on_model" | "export_limit";

interface Envelope {
  event_id: string;
  client_ts: string;
  session_id: string;
  subject: string;
  app_version: string;
  platform: PlatformName;
}

export interface UploadStarted extends Envelope {
  event: "upload_started";
  /** required: an upload with no item cannot be placed in any per-item funnel (ADR-008) */
  item_id: string;
  source: "camera" | "library";
  capture_mode: "single" | "batch";
  batch_index?: number;
}
export interface PrettifyApproved extends Envelope {
  event: "prettify_approved";
  item_id: string;
  decision: "approve" | "retry" | "remove_bg_waitlist";
  job_latency_ms: number;
  prettify_latency_ms: number;
  attrs_latency_ms: number;
}
export interface AttributesEdited extends Envelope {
  event: "attributes_edited";
  item_id: string;
  edited_field_count: number;
  fields_touched: number;
  field_names: string[];
}
export interface ExportClicked extends Envelope {
  event: "export_clicked";
  item_id: string;
  marketplace: MarketplaceId;
  row_count: number;
  export_latency_ms: number;
}
export interface ExportDelivered extends Envelope {
  event: "export_delivered";
  item_id: string;
  marketplace: MarketplaceId;
  row_count: number;
  bytes: number;
  delivery_latency_ms: number;
}
export interface PaywallImpression extends Envelope {
  event: "paywall_impression";
  paywall_variant: PaywallVariant;
  trigger: PaywallTrigger;
  plan_offered: "starter" | "pro";
}
export interface SubscriptionStarted extends Envelope {
  event: "subscription_started";
  /** `item_id` is FORBIDDEN on this event (ADR-008): it is written server-side
   * from a Stripe webhook where no item is in scope. The interface simply omits
   * it, so a caller cannot accidentally attach one. */
  plan: "starter" | "pro";
  paywall_variant: PaywallVariant;
  amount_cents: number;
  currency: string;
}

export type AnalyticsEvent =
  | UploadStarted
  | PrettifyApproved
  | AttributesEdited
  | ExportClicked
  | ExportDelivered
  | PaywallImpression
  | SubscriptionStarted;

export type AnalyticsEventName = AnalyticsEvent["event"];

interface AnalyticsContext {
  subject: string;
  sessionId: string;
}

let ctx: AnalyticsContext = { subject: "anon_unknown", sessionId: uuidv4() };

export function configureAnalytics(context: AnalyticsContext): void {
  ctx = context;
}

const DEV = typeof __DEV__ !== "undefined" && __DEV__ === true;

/** Runtime guard. The typed overloads already enforce shape at the call site; this is the
 * dev-only backstop that proves a malformed event is caught before it would reach the wire. */
function validate(event: AnalyticsEvent): void {
  const problems: string[] = [];
  const need: Array<keyof AnalyticsEvent> = [
    "event_id",
    "client_ts",
    "session_id",
    "subject",
    "app_version",
    "platform",
    "event",
  ];
  for (const key of need) {
    if (event[key] === undefined || event[key] === null) problems.push(String(key));
  }
  if (problems.length > 0) {
    const msg = `analytics: malformed event ${event.event} — missing ${problems.join(", ")}`;
    if (DEV) throw new Error(msg);
    console.error(msg);
  }
}

async function send(event: AnalyticsEvent): Promise<void> {
  if (!hasApi) {
    if (DEV) console.debug("[analytics:mock]", event.event, event);
    return;
  }
  try {
    await fetch(`${config.apiBase}/v1/events`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(event),
    });
  } catch (err) {
    // Analytics must never break the UX. Log and move on.
    console.warn("analytics: send failed", err);
  }
}

/** Call-site fields: the envelope is filled by `track()`, and the discriminant `event`
 *  is the first argument — both are omitted so callers never pass them twice. */
type EventFields<T> = Omit<T, keyof Envelope | "event">;
type UploadStartedInput = EventFields<UploadStarted>;
type PrettifyApprovedInput = EventFields<PrettifyApproved>;
type AttributesEditedInput = EventFields<AttributesEdited>;
type ExportClickedInput = EventFields<ExportClicked>;
type ExportDeliveredInput = EventFields<ExportDelivered>;
type PaywallImpressionInput = EventFields<PaywallImpression>;
type SubscriptionStartedInput = EventFields<SubscriptionStarted>;

export function track(event: "upload_started", fields: UploadStartedInput): void;
export function track(event: "prettify_approved", fields: PrettifyApprovedInput): void;
export function track(event: "attributes_edited", fields: AttributesEditedInput): void;
export function track(event: "export_clicked", fields: ExportClickedInput): void;
export function track(event: "export_delivered", fields: ExportDeliveredInput): void;
export function track(event: "paywall_impression", fields: PaywallImpressionInput): void;
export function track(event: "subscription_started", fields: SubscriptionStartedInput): void;
export function track(event: AnalyticsEventName, fields: Record<string, unknown>): void {
  const platform = Platform.OS as PlatformName;
  const full = {
    event_id: uuidv4(),
    client_ts: new Date().toISOString(),
    session_id: ctx.sessionId,
    subject: ctx.subject,
    app_version: appVersion,
    platform,
    event,
    ...fields,
  } as unknown as AnalyticsEvent;
  validate(full);
  void send(full);
}
