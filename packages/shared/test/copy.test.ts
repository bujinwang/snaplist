/**
 * Copy generator tests (E7-T3).
 *
 * Two of these tests are the reason the module exists, and both are written so they can actually
 * go red:
 *
 * 1. **No unsupported brand or fibre claims.** `unsupportedClaims` is a real checker over a small
 *    brand/fibre vocabulary. The honest case asserts it finds nothing; the forged case feeds it
 *    copy with "Burberry" glued on and asserts it *does* find it. A test that can only pass is
 *    not a test.
 * 2. **No ALL CAPS.** `hasShoutedToken` is asserted true for a deliberately shouted string and
 *    false for the generated title, so a regression in `normalizeShouting` fails here rather than
 *    shipping "BURBERRY CASHMERE SCARF" to eBay.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ItemAttributes,
  TITLE_MAX_CHARS,
  buildListingCopy,
  hasShoutedToken,
  normalizeShouting,
  truncateAtWord,
} from "../src/index";

/** A black cotton crewneck with no brand — the case where invention is most tempting. */
function bareTee(overrides: Record<string, unknown> = {}) {
  return ItemAttributes.parse({
    category: "top",
    subcategory: "crewneck tee",
    colors: ["black"],
    pattern: "solid",
    material: ["cotton"],
    fit: "regular",
    size_label: "M",
    condition: "good",
    ...overrides,
  });
}

/**
 * Terms the copy could plausibly invent, and which must therefore never appear unless the
 * attributes actually contain them. Kept tiny and explicit so a reader can audit the claim.
 */
const BRAND_VOCABULARY = ["Burberry", "Nike", "Levi's", "Chanel", "Uniqlo"];
const FIBRE_VOCABULARY = ["cashmere", "silk", "linen", "leather", "merino"];

function unsupportedClaims(
  text: string,
  attributes: ItemAttributes,
): { brands: string[]; fibres: string[] } {
  const haystack = text.toLowerCase();
  const declaredBrand = (attributes.brand ?? "").toLowerCase();
  const declaredFibres = attributes.material.map((fibre) => fibre.toLowerCase());
  return {
    brands: BRAND_VOCABULARY.filter((brand) => {
      const needle = brand.toLowerCase();
      return haystack.includes(needle) && needle !== declaredBrand;
    }),
    fibres: FIBRE_VOCABULARY.filter(
      (fibre) => haystack.includes(fibre) && !declaredFibres.includes(fibre),
    ),
  };
}

/* -------------------------------------------------------------------------------------------- */
/* The hard rule, with its counter-example                                                       */
/* -------------------------------------------------------------------------------------------- */

test("copy never names a brand or fibre the attributes do not have", () => {
  const attributes = bareTee({ brand: null, material: ["cotton"] });
  const copy = buildListingCopy(attributes);
  const claims = unsupportedClaims(`${copy.title}\n${copy.description}\n${copy.keywords.join(" ")}`, attributes);
  assert.deepEqual(claims, { brands: [], fibres: [] });
  assert.equal(copy.description.includes("Brand:"), false, "no brand line when brand is null");
  assert.equal(copy.description.includes("cashmere"), false);
});

test("the unsupported-claim checker itself catches an injected brand (proof it can fail)", () => {
  const attributes = bareTee({ brand: null, material: ["cotton"] });
  const copy = buildListingCopy(attributes);
  // Simulate the bug this test exists to catch: somebody templates a brand in.
  const forged = {
    title: `Burberry ${copy.title}`,
    description: `${copy.description}\n\nBrand: Burberry\nMaterial: cashmere`,
    keywords: [...copy.keywords],
  };
  const claims = unsupportedClaims(
    `${forged.title}\n${forged.description}\n${forged.keywords.join(" ")}`,
    attributes,
  );
  assert.deepEqual(claims.brands, ["Burberry"], "the checker must see the injected brand");
  assert.deepEqual(claims.fibres, ["cashmere"], "the checker must see the injected fibre");
});

test("a declared brand and fibre are allowed through", () => {
  const attributes = bareTee({ brand: "Nike", material: ["cotton", "cashmere"] });
  const copy = buildListingCopy(attributes);
  assert.ok(copy.title.includes("Nike"));
  assert.ok(copy.description.includes("cashmere"));
  assert.deepEqual(unsupportedClaims(copy.description, attributes).brands, []);
});

/* -------------------------------------------------------------------------------------------- */
/* Title rules                                                                                   */
/* -------------------------------------------------------------------------------------------- */

test("titles stay within 80 characters and cut on a word boundary", () => {
  const attributes = bareTee({
    subcategory: "longline oversized heavyweight boxy crewneck sweatshirt with kangaroo pocket",
    era: "early two thousands",
    fit: "oversized",
    size_label: "XXL",
  });
  const copy = buildListingCopy(attributes);
  assert.ok(copy.title.length <= TITLE_MAX_CHARS, `title was ${copy.title.length} chars`);
  assert.equal(copy.title.endsWith(" "), false);
  // A cut word would leave a fragment; assert the last token is a whole word from the input.
  const lastToken = copy.title.split(" ").at(-1) as string;
  assert.ok(lastToken.length >= 1);
  assert.equal(/[a-z]{2,}$/i.test(lastToken), true);
});

test("truncateAtWord never returns more than the limit, even for one long token", () => {
  assert.equal(truncateAtWord("aaa bbb", 3), "aaa");
  assert.equal(truncateAtWord("a".repeat(120), 80).length, 80);
  assert.equal(truncateAtWord("short", 80), "short");
});

test("an ALL CAPS brand is title-cased, and the detector is proven to work", () => {
  assert.equal(normalizeShouting("BURBERRY CASHMERE SCARF"), "Burberry Cashmere Scarf");
  // size labels must survive untouched
  assert.equal(normalizeShouting("EU 39"), "EU 39");
  assert.equal(normalizeShouting("M"), "M");

  const copy = buildListingCopy(bareTee({ brand: "BURBERRY" }));
  assert.equal(hasShoutedToken("NIKE AIR FORCE"), true, "the detector must see shouting");
  assert.equal(hasShoutedToken(copy.title), false, `title shouted: ${copy.title}`);
  assert.ok(copy.title.includes("Burberry"));
});

/* -------------------------------------------------------------------------------------------- */
/* Description rules                                                                             */
/* -------------------------------------------------------------------------------------------- */

test("the description carries a measurements block when measurements exist", () => {
  const copy = buildListingCopy(
    bareTee({
      measurements: {
        chest_cm: 52,
        length_cm: 68.5,
        source: "seller_provided",
      },
    }),
  );
  assert.ok(copy.description.includes("Measurements"));
  assert.ok(copy.description.includes("Chest: 52 cm"));
  assert.ok(copy.description.includes("Length: 68.5 cm"));
  assert.ok(copy.description.includes("Measured by the seller"));
});

test("no measurements block when measurements are absent — never zeros", () => {
  const copy = buildListingCopy(bareTee({ measurements: null }));
  assert.equal(copy.description.includes("Measurements"), false);
  assert.equal(copy.description.includes("0 cm"), false);
});

test("condition and defects are always stated", () => {
  const copy = buildListingCopy(bareTee({ condition: "fair", defects: ["stain", "loose_seam"] }));
  assert.ok(copy.description.includes("fair pre-owned condition"));
  assert.ok(copy.description.includes("Noted flaws: stain and loose seam"));
});

/* -------------------------------------------------------------------------------------------- */
/* Keywords                                                                                      */
/* -------------------------------------------------------------------------------------------- */

test("at most 10 keywords, lowercase, and none invented", () => {
  const copy = buildListingCopy(
    bareTee({ colors: ["black", "grey"], material: ["cotton", "polyester"], era: "90s" }),
  );
  assert.ok(copy.keywords.length <= 10);
  assert.ok(copy.keywords.length > 0);
  for (const keyword of copy.keywords) {
    assert.equal(keyword, keyword.toLowerCase());
    assert.ok(keyword.trim().length > 0);
  }
  assert.equal(copy.keywords.some((keyword) => keyword.includes("burberry")), false);
  assert.equal(new Set(copy.keywords).size, copy.keywords.length, "no duplicates");
});

/* -------------------------------------------------------------------------------------------- */
/* The no-invention rule, structurally rather than by allowlist                                  */
/* -------------------------------------------------------------------------------------------- */

/**
 * Words a title is *allowed* to contain for one item: everything the seller or the VLM put into
 * the attributes, plus the seven garment nouns the generator uses as a fallback label.
 *
 * The allowlist tests above only catch the five brands and five fibres somebody typed into
 * `BRAND_VOCABULARY` / `FIBRE_VOCABULARY`. A regression that hard-codes any *other* brand —
 * "Zara", "H&M", "Gap" — sails through them: verified by injecting `parts.push("Zara")` into
 * `buildTitle`, which left all 56 tests green. This test inverts the question, so the list of
 * inventable words is "everything not in the attributes", not "five words we thought of".
 */
const GARMENT_NOUNS = ["top", "bottoms", "dress", "jacket", "shoes", "accessory", "item"];

function allowedTitleWords(attributes: ItemAttributes): Set<string> {
  const words = new Set<string>(GARMENT_NOUNS);
  const add = (value: string | null | undefined) => {
    if (!value) return;
    for (const token of value.toLowerCase().split(/\s+/)) {
      const cleaned = token.replace(/[^\w'-]/g, "");
      if (cleaned) words.add(cleaned);
    }
  };
  add(attributes.brand);
  add(attributes.subcategory);
  add(attributes.era);
  add(attributes.size_label);
  if (attributes.fit !== "unknown") add(attributes.fit.replace(/_/g, " "));
  for (const color of attributes.colors) add(color);
  for (const fibre of attributes.material) add(fibre);
  return words;
}

test("no word in a generated title exists outside the attributes", () => {
  const attributes = bareTee({ brand: null, material: ["cotton"], colors: ["black"] });
  const copy = buildListingCopy(attributes);
  const allowed = allowedTitleWords(attributes);
  const invented = copy.title
    .toLowerCase()
    .split(/\s+/)
    .filter((token) => token.length > 0 && !allowed.has(token));
  assert.deepEqual(invented, [], `title invented word(s) absent from attributes: ${invented}`);
});

test("the structural guard still allows a declared brand through", () => {
  const attributes = bareTee({ brand: "Nike" });
  const copy = buildListingCopy(attributes);
  assert.ok(copy.title.toLowerCase().includes("nike"));
  const allowed = allowedTitleWords(attributes);
  const invented = copy.title
    .toLowerCase()
    .split(/\s+/)
    .filter((token) => token.length > 0 && !allowed.has(token));
  assert.deepEqual(invented, [], "a declared brand must not be reported as invented");
});
