/**
 * Poshmark mapper (E7-T2).
 *
 * ## The honest state of this file
 *
 * Poshmark has **no official bulk-upload CSV**. That is recorded in `docs/CONTRACTS.md` §5
 * ("UNVERIFIED: Poshmark has no official bulk CSV upload — E7-T2 must confirm the real path")
 * and it means the column names below are ours, modelled on Poshmark's listing form, not on a
 * template we could download. Every column is therefore `unverified` in `POSHMARK_SOURCES`, and
 * the batch carries `poshmark_no_official_bulk_csv`.
 *
 * What that does **not** mean: that this mapper is decorative. It produces a correct, escaped
 * file that a seller can copy from row by row into Poshmark's form, and it enforces the two rules
 * that actually prevent bad listings:
 *
 * 1. **Brand is required.** CONTRACTS.md: "Poshmark *requires* brand — when `brand` is null we
 *    must ask the seller, not invent." A null brand is a refusal naming `brand`.
 * 2. **Condition collapses 6 → 4**, and the collapse is a table, not a guess. `poor` maps to
 *    Poshmark's worst tier because overstating condition is the expensive mistake.
 *
 * Poshmark allows multiple colours, so `colors` is joined rather than truncated; eBay takes the
 * primary only and Vinted takes the primary with the rest in the description.
 *
 * ## Which columns are the seller's job
 *
 * `Price` carries the price from the request body — the same input that fills eBay's
 * `*StartPrice`. It would be actively harmful for one platform to accept a price and the other to
 * leave it blank while both claim success: the seller would assume both are done. So `Price`
 * declares `poshmark_price` and is published in `requires_manual_columns` exactly when some row
 * has no price.
 *
 * `Original Price` is `null` (never published): it is a strikethrough anchor, not a requirement —
 * an empty cell there still produces a valid listing. The governing line is "an empty cell makes
 * the row invalid on upload", and this one does not.
 */

import type { Condition } from "../schemas";
import { buildListingCopy, truncateAtWord } from "../listing/copy";
import {
  type ColumnSource,
  type ListingSource,
  type MarketplaceMapper,
  type MapperOutcome,
  formatPriceCents,
  hasAll,
  refusal,
} from "./types";

/** Our vocabulary, Poshmark's form layout. All unverified — see the module docstring. */
export const POSHMARK_HEADERS = [
  "Title",
  "Description",
  "Category",
  "Subcategory",
  "Brand",
  "Size",
  "Color",
  "Condition",
  "Original Price",
  "Price",
  "Photos",
] as const;

const UNVERIFIED_SOURCE =
  "Poshmark publishes no bulk CSV template; column modelled on the listing form (E7-T2 UNVERIFIED)";

/** `manual_when_empty` per column — see the module docstring for why only `Price` qualifies. */
const POSHMARK_MANUAL: Readonly<Record<string, string | null>> = {
  "Original Price": null,
  Price: "poshmark_price",
};

export const POSHMARK_SOURCES: readonly ColumnSource[] = POSHMARK_HEADERS.map((header) => ({
  header,
  status: "unverified" as const,
  source: UNVERIFIED_SOURCE,
  manual_when_empty: POSHMARK_MANUAL[header] ?? null,
}));

/**
 * Poshmark's four condition values, and which of our six land in each.
 *
 * `poor` deliberately joins `fair`: Poshmark has no "poor" tier, and listing a poor item as
 * "Good" is the version of this table that loses a case.
 */
export const POSHMARK_CONDITIONS: Record<Condition, string> = {
  new_with_tags: "New with tags",
  new_without_tags: "New without tags",
  excellent: "Like new",
  good: "Good",
  fair: "Fair",
  poor: "Fair",
};

/** eBay allows 80; Poshmark's own form is more generous, but we never need more than 80. */
export const POSHMARK_TITLE_MAX_CHARS = 80;

export function toPoshmarkRow(source: ListingSource): MapperOutcome {
  const attributes = source.attributes;
  if (!attributes) return refusal(source.item_id, ["attributes"]);

  // The contract's one hard requirement. Ask the seller; never invent a brand.
  if (!hasAll(attributes, ["brand"])) return refusal(source.item_id, ["brand"]);

  const copy = buildListingCopy(attributes);
  const photos = [
    ...(source.prettified_url ? [source.prettified_url] : []),
    ...(source.on_model_urls ?? []).filter((url) => url.length > 0),
  ];
  if (photos.length === 0) return refusal(source.item_id, ["prettified_url"]);

  const row = [
    truncateAtWord(copy.title, POSHMARK_TITLE_MAX_CHARS),
    copy.description,
    attributes.category,
    attributes.subcategory ?? "",
    attributes.brand ?? "",
    attributes.size_label ?? "",
    attributes.colors.join(", "),
    POSHMARK_CONDITIONS[attributes.condition],
    "", // Original Price: a strikethrough anchor, not required → never a manual column
    formatPriceCents(source.price_cents), // same input that fills eBay's *StartPrice
    photos.join(" | "),
  ];
  return { row };
}

export const poshmarkMapper: MarketplaceMapper = {
  marketplace: "poshmark",
  headers: POSHMARK_HEADERS,
  sources: POSHMARK_SOURCES,
  warnings: ["poshmark_no_official_bulk_csv", "poshmark_price_seller_supplied"],
  toRow: toPoshmarkRow,
};
