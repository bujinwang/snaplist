/**
 * The analytics vocabulary is a contract: a rename here silently breaks every dashboard and
 * makes `subscription_started` attribution impossible. These tests exist to make that loud.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ANALYTICS_EVENTS,
  ACTIVATION_ITEMS_EXPORTED_7D,
  AnalyticsEvent,
  AttributesEdited,
  EVENTS_VERSION,
  ExportClicked,
  ExportDelivered,
  FREE_TIER_ITEM_LIMIT,
  METRIC_NAMES,
  PAYWALL_VARIANTS,
  PaywallImpression,
  PrettifyApproved,
  SESSION_EXPORT_BURST_60M,
  SubscriptionStarted,
  UploadStarted,
} from "../src/index";

const envelope = {
  event_id: "0f8fad5b-d9cb-469f-a165-70867728950e",
  client_ts: "2026-10-28T12:00:00Z",
  session_id: "sess_1",
  subject: "anon_9f1b0c62",
  app_version: "0.1.0",
  platform: "ios" as const,
};

test("exactly seven frozen event names, in frozen order", () => {
  assert.equal(EVENTS_VERSION, "1.3.0");
  assert.deepEqual([...ANALYTICS_EVENTS], [
    "upload_started",
    "prettify_approved",
    "attributes_edited",
    "export_clicked",
    "subscription_started",
    "paywall_impression",
    "export_delivered",
  ]);
});

test("the envelope carries event_id, and it must be a uuid", () => {
  // ADR-007: `POST /v1/events` is idempotent on a client-generated id. Before 1.3.0 the
  // envelope had no such field, so a client validating with this schema could not build a
  // sendable body at all — the API would 422 it for the key it required and this schema
  // would 422 it for the key it forbade.
  const parsed = UploadStarted.parse({ ...envelope, event: "upload_started", source: "camera", capture_mode: "single" });
  assert.equal(parsed.event_id, envelope.event_id);

  const { event_id: _omitted, ...withoutId } = envelope;
  assert.equal(
    UploadStarted.safeParse({ ...withoutId, event: "upload_started", source: "camera", capture_mode: "single" }).success,
    false,
  );
  assert.equal(
    UploadStarted.safeParse({
      ...envelope,
      event_id: "not-a-uuid",
      event: "upload_started",
      source: "camera",
      capture_mode: "single",
    }).success,
    false,
  );
});

test("export_delivered exists as a real event, not just a version number", () => {
  // A version bump alone does not prove the event survived: someone could roll the array back
  // and leave EVENTS_VERSION at 1.2.0. Activation counts on this event existing.
  assert.ok(
    (ANALYTICS_EVENTS as readonly string[]).includes("export_delivered"),
    "export_delivered missing from ANALYTICS_EVENTS — activation_items_exported_7d cannot be computed",
  );
  assert.equal(ExportDelivered.safeParse({ ...envelope, event: "export_delivered" }).success, false);
  assert.equal(AnalyticsEvent.safeParse({ ...envelope, event: "export_delivered" }).success, false);
});

test("export_delivered carries bytes and delivery latency, not export latency", () => {
  const ok = ExportDelivered.parse({
    ...envelope,
    event: "export_delivered",
    marketplace: "ebay",
    row_count: 5,
    bytes: 2048,
    delivery_latency_ms: 640,
  });
  assert.equal(ok.bytes, 2048);
  assert.equal(ok.marketplace, "ebay");
});


test("the three different threes are named apart", () => {
  assert.equal(ACTIVATION_ITEMS_EXPORTED_7D, 3);
  assert.equal(SESSION_EXPORT_BURST_60M, 3);
  assert.equal(FREE_TIER_ITEM_LIMIT, 3);
  assert.deepEqual(
    [METRIC_NAMES.activation, METRIC_NAMES.sessionBurst, METRIC_NAMES.freeLimit],
    ["activation_items_exported_7d", "session_export_burst_60m", "free_tier_item_limit"],
  );
});

test("upload_started distinguishes single from batch capture", () => {
  const ok = UploadStarted.parse({
    ...envelope,
    event: "upload_started",
    source: "camera",
    capture_mode: "single",
  });
  assert.equal(ok.capture_mode, "single");
  assert.equal(
    UploadStarted.safeParse({
      ...envelope,
      event: "upload_started",
      source: "library",
      capture_mode: "batch",
      batch_index: 4,
    }).success,
    true,
  );
});

test("prettify_approved carries all three latency proxies", () => {
  const ok = PrettifyApproved.parse({
    ...envelope,
    event: "prettify_approved",
    decision: "approve",
    job_latency_ms: 18_000,
    prettify_latency_ms: 21_500,
    attrs_latency_ms: 6_100,
  });
  // tool time for this item, before export latency
  assert.equal(ok.prettify_latency_ms + ok.attrs_latency_ms, 27_600);
});

test("attributes_edited separates touched from edited", () => {
  const ok = AttributesEdited.parse({
    ...envelope,
    event: "attributes_edited",
    edited_field_count: 1,
    fields_touched: 4,
    field_names: ["colors", "size_label"],
  });
  assert.equal(ok.fields_touched > ok.edited_field_count, true);
});

test("field_names rejects anything that looks like a leaked value", () => {
  const leaked = AttributesEdited.safeParse({
    ...envelope,
    event: "attributes_edited",
    edited_field_count: 1,
    fields_touched: 1,
    field_names: ["Brand: Burberry"], // a field name with a value glued on
  });
  assert.equal(leaked.success, false);

  const ok = AttributesEdited.parse({
    ...envelope,
    event: "attributes_edited",
    edited_field_count: 0,
    fields_touched: 2,
    field_names: ["size_label", "measurements"],
  });
  assert.deepEqual(ok.field_names, ["size_label", "measurements"]);
});

test("export_clicked names a real marketplace and a row count", () => {
  const ok = ExportClicked.parse({
    ...envelope,
    event: "export_clicked",
    marketplace: "ebay",
    row_count: 5,
    export_latency_ms: 1_900,
  });
  assert.equal(ok.marketplace, "ebay");
  assert.equal(
    ExportClicked.safeParse({
      ...envelope,
      event: "export_clicked",
      marketplace: "etsy",
      row_count: 5,
      export_latency_ms: 1,
    }).success,
    false,
  );
});

test("subscription_started carries the paywall variant for attribution", () => {
  const ok = SubscriptionStarted.parse({
    ...envelope,
    event: "subscription_started",
    plan: "pro",
    paywall_variant: "b",
    amount_cents: 4900,
    currency: "USD",
  });
  assert.equal(ok.amount_cents, 4900);
});

test("paywall_impression is its own event, not export_clicked", () => {
  const ok = PaywallImpression.parse({
    ...envelope,
    event: "paywall_impression",
    paywall_variant: "a",
    trigger: "upload_4th",
    plan_offered: "starter",
  });
  assert.equal(ok.trigger, "upload_4th");
  assert.equal(PAYWALL_VARIANTS.length, 3);
});

test("the discriminated union rejects an unknown event name", () => {
  const bad = AnalyticsEvent.safeParse({
    ...envelope,
    event: "item_created",
  });
  assert.equal(bad.success, false);
});

test("an unknown key is rejected, so PII cannot ride along", () => {
  const withEmail = UploadStarted.safeParse({
    ...envelope,
    event: "upload_started",
    source: "camera",
    capture_mode: "single",
    email: "seller@example.com",
  });
  assert.equal(withEmail.success, false);
});
