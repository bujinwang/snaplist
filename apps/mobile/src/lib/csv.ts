/**
 * Mock CSV preview for the Export screen (E6-T4).
 *
 * This is a STANDALONE mock. The real, byte-verified marketplace mappers live in
 * `packages/shared/src/marketplaces/` (E7-T1/T2) and are NOT imported here (the
 * workspace cannot fetch `@snaplist/shared`). The columns below are illustrative
 * and deliberately labeled "mock" in the UI so nobody mistakes them for the
 * contract-verified headers. This module only has to render a plausible preview
 * and a byte count for the `export_delivered` event.
 */

import { DraftItem } from "./types";
import { MarketplaceId } from "./analytics";

type MarketplaceCols = {
  ebay: string[];
  poshmark: string[];
  vinted: string[];
};

const HEADERS: MarketplaceCols = {
  ebay: [
    "Title",
    "Description",
    "Category",
    "Colour",
    "Pattern",
    "Material",
    "Size",
    "Condition",
    "Brand",
    "Quantity",
  ],
  poshmark: ["Title", "Description", "Category", "Brand", "Size", "Color", "Condition"],
  vinted: [
    "Title",
    "Description",
    "Category",
    "Brand",
    "Size",
    "Condition",
    "Colour",
    "Material",
  ],
};

function esc(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

function titleCase(value: string): string {
  if (value.length === 0) return value;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function rowFor(item: DraftItem, marketplace: MarketplaceId): string[] {
  const a = item.attributes;
  const category = a?.category ?? "other";
  const color = a?.colors[0] ?? "unknown";
  const title = `${color} ${category}`.trim();
  const description = `Prettified ${category}. Condition: ${a?.condition ?? "good"}.`;
  const brand = a?.brand ?? "";
  const size = a?.size_label ?? "";
  const material = (a?.material ?? []).join(", ");
  const pattern = a?.pattern && a.pattern !== "unknown" ? a.pattern : "";
  const condition = a?.condition ?? "good";

  switch (marketplace) {
    case "ebay":
      return [
        title,
        description,
        category,
        titleCase(color),
        pattern,
        material,
        size,
        condition,
        brand,
        "1",
      ];
    case "poshmark":
      return [title, description, category, brand, size, color, condition];
    case "vinted":
      return [
        title,
        description,
        category,
        brand,
        size,
        condition,
        color,
        material,
      ];
  }
}

export function buildCsvPreview(
  items: DraftItem[],
  marketplace: MarketplaceId,
): string {
  const header = HEADERS[marketplace];
  const rows = items.map((it) => rowFor(it, marketplace).map(esc).join(","));
  return [header.join(","), ...rows].join("\n");
}

/** Rough byte size of the preview, used for `export_delivered.bytes`. */
export function csvBytes(csv: string): number {
  // UTF-8 byte length (BMP-safe approximation; our strings are ASCII/Latin).
  let bytes = 0;
  for (let i = 0; i < csv.length; i++) {
    const code = csv.charCodeAt(i);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else bytes += 3;
  }
  return bytes;
}
