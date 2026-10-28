/**
 * Comps pricing tests (E7-T4). Fixed fixtures, no network, no API key.
 *
 * The point of these tests is the *refusal*, not the median: a suggestion built on four comps is
 * a guess, and this module must not produce one. `insufficient_data` is therefore asserted on the
 * exact boundary (4 → refused, 5 → allowed), so an off-by-one in `MIN_COMPS` cannot slip through.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { MIN_COMPS, medianCents, suggestPrice } from "../src/index";

const FIXTURE = [
  { price_cents: 2_400, sold_at: "2026-09-02", url: "https://example.test/c/1" },
  { price_cents: 3_100, sold_at: "2026-09-11", url: "https://example.test/c/2" },
  { price_cents: 2_800, sold_at: "2026-09-18", url: "https://example.test/c/3" },
  { price_cents: 2_000, sold_at: "2026-09-25", url: "https://example.test/c/4" },
  { price_cents: 9_900, sold_at: "2026-10-01", url: "https://example.test/c/5" },
  { price_cents: 2_600, sold_at: "2026-10-03", url: "https://example.test/c/6" },
];

test("four comps is not enough data, and no price is returned", () => {
  const result = suggestPrice(FIXTURE.slice(0, MIN_COMPS - 1));
  assert.equal(result.status, "insufficient_data");
  assert.equal(result.status === "insufficient_data" && result.comp_count, 4);
  assert.equal(result.status === "insufficient_data" && result.required, 5);
  assert.equal("median_cents" in result, false, "no price may leak out of a refusal");
});

test("exactly five comps is enough, and the median ignores the outlier", () => {
  const result = suggestPrice(FIXTURE.slice(0, MIN_COMPS));
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  // sorted: 2000, 2400, 2800, 3100, 9900 → median 2800 (the 9900 outlier is ignored)
  assert.equal(result.median_cents, 2_800);
  assert.equal(result.comp_count, 5);
  assert.equal(result.currency, "USD");
  assert.equal(result.min_cents, 2_000);
  assert.equal(result.max_cents, 9_900);
});

test("an even number of comps averages the two middle values", () => {
  const result = suggestPrice(FIXTURE);
  assert.equal(result.status, "ok");
  if (result.status !== "ok") return;
  // sorted: 2000, 2400, 2600, 2800, 3100, 9900 → (2600 + 2800) / 2
  assert.equal(result.median_cents, 2_700);
  assert.equal(result.comp_count, 6);
});

test("medianCents handles odd, even and single values", () => {
  assert.equal(medianCents([3, 1, 2]), 2);
  assert.equal(medianCents([4, 1, 3, 2]), 3); // (2 + 3) / 2 rounded
  assert.equal(medianCents([700]), 700);
  assert.throws(() => medianCents([]), /at least one value/);
});

test("zero and non-finite comps do not count toward the threshold", () => {
  const polluted = [
    { price_cents: 0 },
    { price_cents: Number.NaN },
    { price_cents: -100 },
    { price_cents: 2_000 },
    { price_cents: 2_100 },
  ];
  const result = suggestPrice(polluted);
  assert.equal(result.status, "insufficient_data");
  assert.equal(result.status === "insufficient_data" && result.comp_count, 2);
});

test("an empty comp list refuses rather than defaulting to a number", () => {
  const result = suggestPrice([]);
  assert.equal(result.status, "insufficient_data");
  assert.equal(result.status === "insufficient_data" && result.comp_count, 0);
});
