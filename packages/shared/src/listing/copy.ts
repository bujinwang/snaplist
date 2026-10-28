/**
 * Listing copy generator (E7-T3).
 *
 * The one non-negotiable rule in this file, from `docs/CONTRACTS.md` §1:
 *
 * > The copy generator must never state a brand or fibre that is absent from `ItemAttributes`.
 *
 * It is enforced structurally rather than by review: every brand and fibre this module can emit
 * is read out of the `attributes` object it was handed. There is no vocabulary, no fallback
 * ("a nice cotton tee"), no plural of a fibre we guessed. `test/copy.test.ts` proves the rule
 * bites by feeding in an item with `brand: null` and asserting that a term which *would* have
 * been plausible ("Burberry") appears nowhere in the output.
 *
 * The second rule is eBay's: a title is ≤ 80 characters, keyword-front-loaded, and not
 * SHOUTING. `normalizeShouting` title-cases any token of three or more upper-case letters
 * instead of lower-casing it, so a seller-entered "NIKE" renders as "Nike" rather than "nike".
 * One- and two-letter tokens ("M", "EU", "XS") are left alone: those are size labels, not
 * shouting, and mutating them would corrupt `size_label`.
 */

import type { Condition, ItemAttributes } from "../schemas";

/** eBay allows 80 characters in a title. Vinted is stricter; see the mapper. */
export const TITLE_MAX_CHARS = 80;

/** A token of 3+ capitals is shouting. "M" and "EU" are size labels and pass through. */
const SHOUTED_TOKEN = /^[A-Z]{3,}$/;

/** Human labels for our category vocabulary. Keys mirror GARMENT_CATEGORIES exactly. */
const CATEGORY_LABEL: Record<ItemAttributes["category"], string> = {
  top: "top",
  bottom: "bottoms",
  dress: "dress",
  outerwear: "jacket",
  shoe: "shoes",
  accessory: "accessory",
  other: "item",
};

/**
 * One sentence per condition. Deliberately plain: a grading claim the seller did not make is
 * the fastest way to an "item not as described" case.
 */
const CONDITION_SENTENCE: Record<Condition, string> = {
  new_with_tags: "New with tags, never worn.",
  new_without_tags: "New without tags, never worn.",
  excellent: "In excellent pre-owned condition with no visible flaws.",
  good: "In good pre-owned condition, worn with normal signs of use.",
  fair: "In fair pre-owned condition with visible signs of wear.",
  poor: "Sold as seen, with significant signs of wear described below.",
};

/** Lowercase, trimmed, single-spaced words only. */
function cleanWords(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

/** Title-case any token that is entirely capitals (3+ letters). Leaves everything else alone. */
export function normalizeShouting(value: string): string {
  return cleanWords(value)
    .split(" ")
    .map((token) => {
      if (!SHOUTED_TOKEN.test(token)) return token;
      return token.charAt(0) + token.slice(1).toLowerCase();
    })
    .join(" ");
}

/** True when the text still contains a shouted token. Used by the tests *and* by `assertTitle`. */
export function hasShoutedToken(value: string): boolean {
  return cleanWords(value)
    .split(" ")
    .some((token) => SHOUTED_TOKEN.test(token.replace(/[^\w']/g, "")));
}

/**
 * Cut on a word boundary. Never returns a half word, never exceeds `max`.
 * If a single token is longer than `max`, it is cut hard — a 90-character word is a UUID, not a
 * garment description, and keeping it whole would break the marketplace's parser.
 */
export function truncateAtWord(value: string, max: number = TITLE_MAX_CHARS): string {
  const text = cleanWords(value);
  if (text.length <= max) return text;
  const sliced = text.slice(0, max);
  const lastSpace = sliced.lastIndexOf(" ");
  if (lastSpace > Math.floor(max / 2)) return sliced.slice(0, lastSpace).trim();
  return sliced.trim();
}

/** Capitalise the first letter of a phrase without touching the rest of it. */
function capitalize(value: string): string {
  if (value.length === 0) return value;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function joinList(values: readonly string[]): string {
  const items = values.filter((value) => value.trim().length > 0);
  if (items.length <= 1) return items[0] ?? "";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

export interface ListingCopy {
  /** ≤ 80 chars, keyword-front-loaded, no shouted tokens. */
  title: string;
  /** Multi-line text: identity line, condition, flaws, details, measurements. */
  description: string;
  /** Up to 10 lowercase search terms. Deduplicated. */
  keywords: string[];
}

/**
 * Title order is the order a buyer searches in: brand (when known) → colour → fibre → garment
 * type → fit → size → era. Everything is optional except the garment type, so the title is
 * never empty.
 */
function buildTitle(attributes: ItemAttributes): string {
  const parts: string[] = [];
  if (attributes.brand) parts.push(normalizeShouting(attributes.brand));
  const primaryColor = attributes.colors[0];
  if (primaryColor) parts.push(primaryColor);
  const primaryFibre = attributes.material[0];
  if (primaryFibre) parts.push(primaryFibre);
  if (attributes.subcategory) {
    parts.push(normalizeShouting(attributes.subcategory));
  } else {
    parts.push(CATEGORY_LABEL[attributes.category]);
  }
  if (attributes.fit !== "unknown") parts.push(attributes.fit.replace(/_/g, " "));
  if (attributes.size_label) parts.push(normalizeShouting(attributes.size_label));
  if (attributes.era) parts.push(normalizeShouting(attributes.era));

  const title = truncateAtWord(parts.filter((part) => part.length > 0).join(" "));
  return capitalize(title);
}

/** The measurements block, in the order a seller reads it off a tape measure. */
function measurementLines(attributes: ItemAttributes): string[] {
  const measurements = attributes.measurements;
  if (!measurements) return [];
  const labels: ReadonlyArray<readonly [keyof typeof measurements, string]> = [
    ["chest_cm", "Chest"],
    ["length_cm", "Length"],
    ["waist_cm", "Waist"],
    ["rise_cm", "Rise"],
    ["inseam_cm", "Inseam"],
    ["sleeve_cm", "Sleeve"],
  ];
  const lines: string[] = [];
  for (const [key, label] of labels) {
    const value = measurements[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      lines.push(`- ${label}: ${formatMeasurement(value)} cm`);
    }
  }
  if (lines.length === 0) return [];
  const source =
    measurements.source === "seller_provided"
      ? "Measured by the seller, flat."
      : "Measured flat, approximate.";
  return ["Measurements", source, ...lines];
}

/** Trim a trailing `.0` so "52.0 cm" reads as "52 cm" — cosmetic, and it keeps the columns tidy. */
function formatMeasurement(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function buildDescription(attributes: ItemAttributes): string {
  const garmentType = attributes.subcategory ?? CATEGORY_LABEL[attributes.category];
  const identity = [joinList(attributes.colors), garmentType].filter(Boolean).join(" ");
  const blocks: string[] = [];

  blocks.push(`${capitalize(identity)}.`);
  blocks.push(CONDITION_SENTENCE[attributes.condition]);

  if (attributes.defects.length > 0) {
    const flaws = attributes.defects.map((defect) => defect.replace(/_/g, " "));
    blocks.push(`Noted flaws: ${joinList(flaws)}.`);
  }

  const details: string[] = [];
  if (attributes.size_label) details.push(`Size: ${attributes.size_label}`);
  if (attributes.brand) details.push(`Brand: ${normalizeShouting(attributes.brand)}`);
  if (attributes.material.length > 0) details.push(`Material: ${joinList(attributes.material)}`);
  details.push(`Colour: ${joinList(attributes.colors)}`);
  if (attributes.pattern !== "unknown") details.push(`Pattern: ${attributes.pattern.replace(/_/g, " ")}`);
  if (attributes.fit !== "unknown") details.push(`Fit: ${attributes.fit.replace(/_/g, " ")}`);
  if (attributes.era) details.push(`Era: ${attributes.era}`);
  blocks.push(["Details", ...details.map((line) => `- ${line}`)].join("\n"));

  const measurements = measurementLines(attributes);
  if (measurements.length > 0) blocks.push(measurements.join("\n"));

  return blocks.join("\n\n");
}

/**
 * Up to 10 search terms. Built only out of attribute values, so a keyword can never smuggle in
 * a brand or a fibre the item does not have.
 */
function buildKeywords(attributes: ItemAttributes, title: string): string[] {
  const candidates: string[] = [];
  const garmentType = attributes.subcategory ?? CATEGORY_LABEL[attributes.category];
  const primaryColor = attributes.colors[0];

  if (attributes.brand) candidates.push(attributes.brand);
  for (const color of attributes.colors) candidates.push(color);
  for (const fibre of attributes.material) candidates.push(fibre);
  candidates.push(garmentType);
  if (primaryColor) candidates.push(`${primaryColor} ${garmentType}`);
  if (attributes.fit !== "unknown") candidates.push(attributes.fit.replace(/_/g, " "));
  if (attributes.pattern !== "unknown") candidates.push(attributes.pattern.replace(/_/g, " "));
  if (attributes.era) candidates.push(attributes.era);
  if (attributes.size_label) candidates.push(`size ${attributes.size_label}`);

  const seen = new Set<string>();
  const keywords: string[] = [];
  for (const candidate of candidates) {
    const keyword = cleanWords(candidate).toLowerCase();
    if (keyword.length < 2 || seen.has(keyword)) continue;
    seen.add(keyword);
    keywords.push(keyword);
    if (keywords.length === 10) break;
  }
  // The title itself is already keyword-front-loaded; if there is room, keep it for search.
  if (keywords.length < 10) {
    const fromTitle = title.toLowerCase();
    if (!seen.has(fromTitle)) keywords.push(fromTitle);
  }
  return keywords.slice(0, 10);
}

/**
 * Build the title, description and keywords for one item.
 *
 * Throws only if `attributes` is not an object at all — a mapper passes `null` attributes as a
 * refusal long before calling this, so a throw here means a programming error, not a data
 * problem.
 */
export function buildListingCopy(attributes: ItemAttributes): ListingCopy {
  const title = buildTitle(attributes);
  return {
    title,
    description: buildDescription(attributes),
    keywords: buildKeywords(attributes, title),
  };
}
