/**
 * The export entry point: `exportRows(marketplace, items) -> ExportBatch`.
 *
 * This single function is what both consumers call — the API (`apps/api/exporters.py` pipes JSON
 * into it through `node`) and the tests. There is no second implementation of the mapping rules,
 * which is the only way "the header line is byte-identical to the template" can stay true.
 *
 * It never throws for a data problem: a row either exports or comes back in `refused` with the
 * blocking attribute names. It *does* throw for:
 *
 * - an unknown marketplace — a bug in the caller, not something a seller can fix
 * - a row whose cell count differs from the header — see `CsvShapeError`; padding would shift
 *   every following column and produce a file that uploads cleanly with the wrong values
 * - a negative price — invalid input, not a formatting choice
 */

import { writeCsv } from "../csv";
import type { Marketplace } from "../schemas";
import { ebayMapper } from "./ebay";
import { poshmarkMapper } from "./poshmark";
import type {
  ExportBatch,
  ExportedRow,
  ListingSource,
  MarketplaceMapper,
  MapperOptions,
  RefusedRow,
} from "./types";
import { computeRequiresManualColumns } from "./types";
import { vintedMapper } from "./vinted";

export const MAPPERS: Readonly<Record<Marketplace, MarketplaceMapper>> = {
  ebay: ebayMapper,
  poshmark: poshmarkMapper,
  vinted: vintedMapper,
};

export function mapperFor(marketplace: Marketplace): MarketplaceMapper {
  const mapper = MAPPERS[marketplace];
  if (!mapper) {
    throw new Error(`no mapper for marketplace ${String(marketplace)}`);
  }
  return mapper;
}

/**
 * Map and serialize a batch.
 *
 * `csv` is empty exactly when every row was refused — callers must treat that as an error to
 * show the seller, never as an empty success.
 *
 * `requires_manual_columns` is derived (see `computeRequiresManualColumns`), not maintained by
 * hand: it names the columns a seller still has to fill before the file is uploadable. An empty
 * array is a real answer ("nothing to fill in") and must be published, not omitted.
 */
export function exportRows(
  marketplace: Marketplace,
  items: readonly ListingSource[],
  options: MapperOptions = {},
): ExportBatch {
  const mapper = mapperFor(marketplace);
  const rows: string[][] = [];
  const exported: ExportedRow[] = [];
  const refused: RefusedRow[] = [];

  for (const item of items) {
    const outcome = mapper.toRow(item, options);
    if ("row" in outcome) {
      exported.push({ item_id: item.item_id, row_index: rows.length });
      rows.push(outcome.row);
    } else {
      refused.push(outcome.refusal);
    }
  }

  return {
    csv: rows.length > 0 ? writeCsv(mapper.headers, rows) : "",
    exported,
    refused,
    requires_manual_columns: computeRequiresManualColumns(mapper, rows),
    headers: mapper.headers,
    warnings: mapper.warnings,
  };
}

// Explicit, not `export *`: `ExportedRow` and `RefusedRow` are owned by `../schemas` (they are
// wire shapes, mirrored in apps/api/responses.py), and a star export here would collide with
// `export * from "./schemas"` in `src/index.ts`.
export type {
  ColumnSource,
  ColumnStatus,
  ExportBatch,
  ListingSource,
  MarketplaceMapper,
  MapperOptions,
  MapperOutcome,
} from "./types";
export { computeRequiresManualColumns, formatPriceCents, hasAll, refusal } from "./types";
export {
  EBAY_ACTION_HEADER,
  EBAY_CONDITION_IDS,
  EBAY_DEFAULT_DURATION,
  EBAY_HEADERS,
  EBAY_SOURCES,
  EBAY_TITLE_MAX_CHARS,
  ebayMapper,
  toEbayRow,
} from "./ebay";
export type { EbayOptions } from "./ebay";
export {
  POSHMARK_CONDITIONS,
  POSHMARK_HEADERS,
  POSHMARK_SOURCES,
  POSHMARK_TITLE_MAX_CHARS,
  poshmarkMapper,
  toPoshmarkRow,
} from "./poshmark";
export {
  VINTED_CONDITIONS,
  VINTED_HEADERS,
  VINTED_MATERIALS,
  VINTED_SOURCES,
  VINTED_TITLE_MAX_CHARS,
  vintedMapper,
  toVintedRow,
} from "./vinted";
