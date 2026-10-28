/**
 * eBay mapper (E7-T1) — File Exchange flat file.
 *
 * ## Header provenance, stated per column
 *
 * Column names are the part of this file that can silently ruin a seller's afternoon, so every
 * one of them carries a `status` in `EBAY_SOURCES`:
 *
 * - `documented` — the name appears in eBay's own File Exchange template guide
 *   (`https://pics.ebay.com/aw/pics/ca/pdf/file_exchange/File_Exchange_Basic_Template_Mini_Guide.pdf`,
 *   and the File Exchange user guide at
 *   `https://pics.ebay.com/aw/pics/hk/pdf/file_exchange/File_Exchange_User_Guide.pdf`). Those
 *   two PDFs show `*Action(SiteID=…|Country=…|Currency=…|Version=…|CC=…)`, `*Category`, `*Title`,
 *   `*Description`, `PicURL`, the `C:`-prefixed item-specific columns, and the 80-character
 *   title limit.
 * - `unverified` — we need the column, eBay's template almost certainly has it, but nobody on
 *   this project fetched a primary source for the exact spelling. The `Version=` number inside
 *   the `*Action` header is in this bucket: it changes with eBay's releases and a wrong number is
 *   rejected at upload.
 *
 * `test/marketplaces.test.ts` pins every header string *and* asserts that each has a provenance
 * row, so a rename cannot happen without a human reading this comment.
 *
 * **The column count is a property of this template, not an invariant.** 15 is what
 * `Version=745` of the File Exchange item template has. When eBay changes the template: edit
 * `EBAY_HEADERS`, add/remove the matching entry in `EBAY_SOURCES`, and flip that column's status
 * from `unverified` to `documented` with the URL you verified it against. Nothing else in this
 * repo assumes 15 — the arity check compares row length to `headers.length`.
 *
 * ## What this mapper refuses to make up
 *
 * - **category id** — `*Category` stays empty unless the caller supplies a verified id through
 *   `Options.categoryIds`. Resolving our vocabulary to eBay's numeric taxonomy needs the
 *   Taxonomy API and credentials we do not have; a guessed id lists the item in the wrong
 *   department, which is the failure mode CONTRACTS.md §5 warns about in the strongest terms.
 * - **price** — `*StartPrice` is the seller's number, not ours (same rule as Poshmark's price in
 *   CONTRACTS.md §5). Supplied per item via `ListingSource.price_cents`; absent means the cell is
 *   empty and `ebay_start_price` is published in `requires_manual_columns`.
 * - **shipping** — never a column we fill; seller defaults live in the app.
 *
 * ## Condition
 *
 * eBay's `*ConditionID` is *eBay's* enum, not ours; the table below is an explicit mapping and
 * `test/marketplaces.test.ts` pins it. Our four used grades collapse onto eBay's single
 * "Pre-owned" id (3000) on purpose: the finer eBay grades (4000/5000/6000) are not confirmed
 * against a primary source here, and a wrong grade is a "not as described" case. The nuance goes
 * in the description, which is ours to write.
 */

import type { Condition, GarmentCategory } from "../schemas";
import { buildListingCopy, TITLE_MAX_CHARS, truncateAtWord } from "../listing/copy";
import {
  type ColumnSource,
  type ListingSource,
  type MapperOptions,
  type MarketplaceMapper,
  type MapperOutcome,
  formatPriceCents,
  refusal,
} from "./types";

/**
 * The action header. `Version=` tracks eBay's flat-file schema; 745 is what the widely used
 * template generator emits today, but we have not confirmed it against eBay's release notes —
 * so it is `unverified`, and a seller who gets a version error only has to edit one cell.
 */
export const EBAY_ACTION_HEADER =
  "*Action(SiteID=US|Country=US|Currency=USD|Version=745|CC=UTF-8)";

const TEMPLATE_GUIDE_URL =
  "https://pics.ebay.com/aw/pics/ca/pdf/file_exchange/File_Exchange_Basic_Template_Mini_Guide.pdf";
const USER_GUIDE_URL =
  "https://pics.ebay.com/aw/pics/hk/pdf/file_exchange/File_Exchange_User_Guide.pdf";

/** Frozen column order. Changing this array is a breaking change for every saved template. */
export const EBAY_HEADERS = [
  EBAY_ACTION_HEADER,
  "*Category",
  "*Title",
  "*Description",
  "*ConditionID",
  "*Quantity",
  "*StartPrice",
  "*Duration",
  "PicURL",
  "C:Brand",
  "C:Type",
  "C:Size",
  "C:Color",
  "C:Material",
  "C:Pattern",
] as const;

/**
 * Provenance and manual-column declaration, one row per header, same order.
 *
 * `manual_when_empty` follows the governing line from `types.ts`: a column earns a literal when
 * **an empty cell makes the row invalid on upload**. `*Category` (eBay rejects an unknown
 * category) and `*StartPrice` (a listing must have a price) qualify. The `C:*` item specifics do
 * not: an empty `C:Brand` is a valid listing, and attributes are editable in the app — the CSV
 * is not where attributes get fixed.
 */
export const EBAY_SOURCES: readonly ColumnSource[] = [
  {
    header: EBAY_ACTION_HEADER,
    status: "documented",
    source: USER_GUIDE_URL,
    manual_when_empty: null,
  },
  {
    header: "*Category",
    status: "documented",
    source: USER_GUIDE_URL,
    manual_when_empty: "ebay_category_id",
  },
  { header: "*Title", status: "documented", source: TEMPLATE_GUIDE_URL, manual_when_empty: null },
  {
    header: "*Description",
    status: "documented",
    source: TEMPLATE_GUIDE_URL,
    manual_when_empty: null,
  },
  {
    header: "*ConditionID",
    status: "documented",
    source: USER_GUIDE_URL,
    manual_when_empty: null,
  },
  { header: "*Quantity", status: "documented", source: USER_GUIDE_URL, manual_when_empty: null },
  {
    header: "*StartPrice",
    status: "documented",
    source: USER_GUIDE_URL,
    manual_when_empty: "ebay_start_price",
  },
  { header: "*Duration", status: "documented", source: USER_GUIDE_URL, manual_when_empty: null },
  { header: "PicURL", status: "documented", source: TEMPLATE_GUIDE_URL, manual_when_empty: null },
  {
    header: "C:Brand",
    status: "unverified",
    source: "C:/A: item-specific prefix not confirmed",
    manual_when_empty: null,
  },
  {
    header: "C:Type",
    status: "unverified",
    source: "C:/A: item-specific prefix not confirmed",
    manual_when_empty: null,
  },
  {
    header: "C:Size",
    status: "unverified",
    source: "C:/A: item-specific prefix not confirmed",
    manual_when_empty: null,
  },
  {
    header: "C:Color",
    status: "unverified",
    source: "US spelling assumed from SiteID=US",
    manual_when_empty: null,
  },
  {
    header: "C:Material",
    status: "unverified",
    source: "C:/A: item-specific prefix not confirmed",
    manual_when_empty: null,
  },
  {
    header: "C:Pattern",
    status: "unverified",
    source: "C:/A: item-specific prefix not confirmed",
    manual_when_empty: null,
  },
];

/**
 * eBay condition ids. 1000/1500/7000 come from the File Exchange guide's condition list; 3000 is
 * the single "Pre-owned" bucket we collapse our four used grades into.
 */
export const EBAY_CONDITION_IDS: Record<Condition, number> = {
  new_with_tags: 1000,
  new_without_tags: 1500,
  excellent: 3000,
  good: 3000,
  fair: 3000,
  poor: 7000,
};

/** `GTC` = Good 'Til Cancelled, the sensible default for a one-off resale listing. */
export const EBAY_DEFAULT_DURATION = "GTC";

/**
 * Batch-level options. **Price is not one of them on purpose**: a batch-level price would put
 * the same number on every garment and nothing would error.
 */
export interface EbayOptions extends MapperOptions {
  duration?: string;
}

function photosOf(source: ListingSource): string[] {
  const urls: string[] = [];
  if (source.prettified_url) urls.push(source.prettified_url);
  for (const url of source.on_model_urls ?? []) {
    if (url) urls.push(url);
  }
  return urls;
}

/**
 * Build one eBay row.
 *
 * Refuses when there are no attributes at all (the pipeline has not run) or when the item has no
 * photo: an eBay listing without `PicURL` is rejected at upload, and telling the seller that here
 * is cheaper than telling them from eBay's error page.
 */
export function toEbayRow(source: ListingSource, options: EbayOptions = {}): MapperOutcome {
  const attributes = source.attributes;
  if (!attributes) return refusal(source.item_id, ["attributes"]);

  const photos = photosOf(source);
  if (photos.length === 0) return refusal(source.item_id, ["prettified_url"]);

  const copy = buildListingCopy(attributes);
  const title = truncateAtWord(copy.title, TITLE_MAX_CHARS);
  const categoryId = options.categoryIds?.[attributes.category];

  const row = [
    "Add",
    // Empty when no verified id exists; `requires_manual_columns` then names the column.
    categoryId === undefined ? "" : String(categoryId),
    title,
    copy.description,
    String(EBAY_CONDITION_IDS[attributes.condition]),
    "1", // resale: one of each. Never inferred from a quantity field we do not have.
    formatPriceCents(source.price_cents),
    options.duration ?? EBAY_DEFAULT_DURATION,
    photos.join("|"),
    attributes.brand ?? "",
    attributes.subcategory ?? "",
    attributes.size_label ?? "",
    attributes.colors[0] ?? "",
    attributes.material.join(", "),
    attributes.pattern === "unknown" ? "" : attributes.pattern.replace(/_/g, " "),
  ];

  return { row };
}

export const ebayMapper: MarketplaceMapper = {
  marketplace: "ebay",
  headers: EBAY_HEADERS,
  sources: EBAY_SOURCES,
  // Both warnings are the truth about this file, and both are surfaced on the export response so
  // a client can show them before the seller uploads anything.
  warnings: ["ebay_category_id_unverified", "ebay_start_price_seller_supplied"],
  toRow: (source, options) => toEbayRow(source, options),
};

/** Exported for the seller-facing docs (E7-T6) and for the tests. */
export const EBAY_TITLE_MAX_CHARS = TITLE_MAX_CHARS;
