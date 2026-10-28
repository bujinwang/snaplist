/**
 * Vinted mapper (E7-T2).
 *
 * ## The honest state of this file
 *
 * Vinted's bulk-upload availability varies by country and is not something this repo has
 * confirmed against a primary source — `docs/CONTRACTS.md` §5 says exactly that and marks it
 * UNVERIFIED. Two consequences, both deliberate:
 *
 * - the column names are `unverified` in `VINTED_SOURCES` and the batch carries
 *   `vinted_bulk_upload_unverified`;
 * - the mapper still enforces the rules Vinted states plainly in its own listing guidance: a
 *   shorter title, a required brand, a required size, and **measurements in the description** —
 *   which is why `copy.ts` always emits a measurements block when it has any.
 *
 * ## What is dropped rather than guessed
 *
 * Vinted takes a *single* colour and a **controlled fibre vocabulary**. We take `colors[0]` (the
 * primary, by contract) and map only fibres we recognise; an unknown fibre is dropped from the
 * Material column instead of being smuggled in, because "cashmere" on a polyester jumper is a
 * return, not a typo. The full colour list and the original fibres still reach the buyer through
 * the description, which `copy.ts` builds from the same attributes.
 *
 * ## Which columns are the seller's job
 *
 * `Package size` is a shipping decision that cannot be derived from any attribute, so every row
 * leaves it empty and `vinted_package_size` is always published in `requires_manual_columns`.
 * Everything else on this form is either derived from attributes or a refusal (`brand`, `size`).
 */

import type { Condition } from "../schemas";
import { buildListingCopy, truncateAtWord } from "../listing/copy";
import {
  type ColumnSource,
  type ListingSource,
  type MarketplaceMapper,
  type MapperOutcome,
  hasAll,
  refusal,
} from "./types";

export const VINTED_HEADERS = [
  "Title",
  "Description",
  "Category",
  "Brand",
  "Size",
  "Condition",
  "Colour",
  "Material",
  "Package size",
] as const;

const UNVERIFIED_SOURCE =
  "Vinted publishes no stable bulk-upload template per country; column modelled on the listing form (E7-T2 UNVERIFIED)";

/** `manual_when_empty` per column — only the shipping decision is permanently the seller's. */
const VINTED_MANUAL: Readonly<Record<string, string | null>> = {
  "Package size": "vinted_package_size",
};

export const VINTED_SOURCES: readonly ColumnSource[] = VINTED_HEADERS.map((header) => ({
  header,
  status: "unverified" as const,
  source: UNVERIFIED_SOURCE,
  manual_when_empty: VINTED_MANUAL[header] ?? null,
}));

/**
 * Vinted's five condition values (`docs/CONTRACTS.md` §5 calls it a 5-value enum). Our six
 * collapse: `fair` and `poor` both land on "Satisfactory", the lowest tier Vinted offers.
 */
export const VINTED_CONDITIONS: Record<Condition, string> = {
  new_with_tags: "New with tags",
  new_without_tags: "New without tags",
  excellent: "Very good",
  good: "Good",
  fair: "Satisfactory",
  poor: "Satisfactory",
};

/**
 * Vinted's controlled fibre list, as far as we use it. Anything not in this map is **dropped**.
 * `unverified`: the display spelling is ours; the vocabulary itself is Vinted's.
 */
export const VINTED_MATERIALS: Readonly<Record<string, string>> = {
  cotton: "Cotton",
  polyester: "Polyester",
  wool: "Wool",
  silk: "Silk",
  linen: "Linen",
  denim: "Denim",
  leather: "Leather",
  viscose: "Viscose",
  nylon: "Nylon",
  elastane: "Elastane",
  acrylic: "Acrylic",
  cashmere: "Cashmere",
  mohair: "Mohair",
  suede: "Suede",
};

/** Vinted titles are shorter than eBay's; 60 is our working ceiling (UNVERIFIED exact limit). */
export const VINTED_TITLE_MAX_CHARS = 60;

function mapMaterials(materials: readonly string[]): string {
  const mapped: string[] = [];
  for (const fibre of materials) {
    const display = VINTED_MATERIALS[fibre.trim().toLowerCase()];
    if (display && !mapped.includes(display)) mapped.push(display);
  }
  return mapped.join(", ");
}

export function toVintedRow(source: ListingSource): MapperOutcome {
  const attributes = source.attributes;
  if (!attributes) return refusal(source.item_id, ["attributes"]);
  // Vinted requires brand and size; both must come from the seller, never from us.
  if (!hasAll(attributes, ["brand", "size_label"])) {
    const missing: string[] = [];
    if (!hasAll(attributes, ["brand"])) missing.push("brand");
    if (!hasAll(attributes, ["size_label"])) missing.push("size_label");
    return refusal(source.item_id, missing);
  }

  const copy = buildListingCopy(attributes);
  const primaryColor = attributes.colors[0] ?? "";

  const row = [
    truncateAtWord(copy.title, VINTED_TITLE_MAX_CHARS),
    copy.description,
    attributes.category,
    attributes.brand ?? "",
    attributes.size_label ?? "",
    VINTED_CONDITIONS[attributes.condition],
    primaryColor,
    mapMaterials(attributes.material),
    "", // Package size: the seller's shipping decision, never invented
  ];
  return { row };
}

export const vintedMapper: MarketplaceMapper = {
  marketplace: "vinted",
  headers: VINTED_HEADERS,
  sources: VINTED_SOURCES,
  warnings: ["vinted_bulk_upload_unverified", "vinted_package_size_seller_supplied"],
  toRow: toVintedRow,
};
