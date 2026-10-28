/**
 * Zod-side contract tests. The cross-language parity check lives in
 * apps/api/tests/test_contract_parity.py (python reads dist/src/schemas.js through node).
 * Here we assert the behaviour mobile and growth code will actually depend on.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  AssetPack,
  CONTRACT_VERSION,
  ErrorResponse,
  ItemAttributes,
  Job,
  REASON_CODES,
  JOB_STATES,
  GARMENT_CATEGORIES,
} from "../src/index";

const UUID = "00000000-0000-4000-8000-000000000000";

test("contract version matches the python side", () => {
  assert.equal(CONTRACT_VERSION, "1.1.0");
});

test("ItemAttributes requires only category and colors", () => {
  const parsed = ItemAttributes.parse({ category: "top", colors: ["black"] });
  assert.equal(parsed.pattern, "unknown");
  assert.equal(parsed.fit, "unknown");
  assert.equal(parsed.condition, "good");
  assert.equal(parsed.model_version, "unset");
  assert.deepEqual(parsed.material, []);
});

test("ItemAttributes rejects unknown keys", () => {
  const result = ItemAttributes.safeParse({
    category: "top",
    colors: ["black"],
    surprise: 1,
  });
  assert.equal(result.success, false);
});

test("colors must not be empty and must not exceed three", () => {
  assert.equal(ItemAttributes.safeParse({ category: "top", colors: [] }).success, false);
  assert.equal(
    ItemAttributes.safeParse({ category: "top", colors: ["a", "b", "c", "d"] }).success,
    false,
  );
});

test("AssetPack accepts the five contract keys and refuses a sixth", () => {
  const ok = AssetPack.safeParse({
    item_id: UUID,
    prettified_url: null,
    cutout_url: null,
    on_model_urls: [],
    attributes: null,
  });
  assert.equal(ok.success, true);

  const extra = AssetPack.safeParse({
    item_id: UUID,
    on_model_urls: [],
    surprise: true,
  });
  assert.equal(extra.success, false);
});

test("AssetPack requires a uuid item_id", () => {
  const bad = AssetPack.safeParse({ item_id: "item-1", on_model_urls: [] });
  assert.equal(bad.success, false);
});

test("Job defaults to queued with no reason_code", () => {
  const job = Job.parse({
    id: UUID,
    item_id: UUID,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });
  assert.equal(job.state, "queued");
  assert.equal(job.reason_code, undefined);
  assert.equal(job.attempts, 0);
});

test("failed jobs carry one of the five frozen reason codes", () => {
  const failed = Job.safeParse({
    id: UUID,
    item_id: UUID,
    state: "failed",
    reason_code: "not_a_reason",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });
  assert.equal(failed.success, false);
  assert.deepEqual([...REASON_CODES], [
    "bad_photo",
    "no_garment",
    "low_confidence",
    "vton_rejected",
    "internal",
  ]);
});

test("job state machine vocabulary is frozen", () => {
  assert.deepEqual([...JOB_STATES], ["queued", "running", "done", "failed"]);
});

test("categories match the brief vocabulary", () => {
  assert.deepEqual([...GARMENT_CATEGORIES], [
    "top",
    "bottom",
    "dress",
    "outerwear",
    "shoe",
    "accessory",
    "other",
  ]);
});

test("error envelope is {error:{code,message}}", () => {
  const err = ErrorResponse.parse({
    error: { code: "quota_exceeded", message: "plan limit reached" },
  });
  assert.equal(err.error.code, "quota_exceeded");
});
