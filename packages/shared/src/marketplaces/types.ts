/**
 * Shared types for the three marketplace mappers (E7-T1/T2).
 *
 * The mapper contract is a **discriminated outcome**, not an exception and not an empty string:
 *
 *     { row: [...] }      → this item is exportable
 *     { refusal: {...} }  → this item is not, and here is exactly what blocked it
 *
 * CONTRACTS.md §5: "Refusal beats guessing. A listing the seller has to fix is recoverable; a
 * wrong category id silently costs them the sale." A mapper that throws would abort the whole
 * batch for one incomplete item; a mapper that emits an empty cell would produce a file eBay
 * rejects at upload time with a worse error message than ours.
 *
 * ## `requires_manual_columns` — a rule, not a maintained list
 *
 * Some columns are always empty because the product does not collect the value at all (an eBay
 * category id, a price, a package size). A seller must fill those by hand, and the client has to
 * say so *before* the file is uploaded. That signal is per **column**, computed at export time
 * from the rows actually produced — never a hardcoded array, which would drift the first time a
 * column or a marketplace was added.
 *
 * The rule has two halves, both required:
 *
 * 1. **Declaration** — `ColumnSource.manual_when_empty` names the literal (or is `null`).
 *    Provenance is already mandatory per header (a test enforces one `ColumnSource` per header),
 *    so adding a column forces whoever adds it to answer "is an empty cell here the seller's job?".
 * 2. **Evidence** — the column is actually empty in at least one exported row (quantifier `any`).
 *
 * Why `any` and not `all`: a batch of three items with prices supplied for two still needs the
 * third price. Requiring *all* rows to be empty would hide that gap behind a mostly-filled
 * column — the hardest kind of missing value for a seller to notice.
 *
 * `null` is an assertion, not a default: it means "an empty cell here is the correct resting
 * state" (an item that genuinely has no `era`), not "I could not decide".
 */

import type {
  ExportedRowModel as ExportedRow,
  GarmentCategory,
  ItemAttributes,
  Marketplace,
  RefusedRowModel as RefusedRow,
} from "../schemas";

/**
 * The two wire shapes the export batch publishes come from `schemas.ts`, derived from the zod
 * models rather than re-declared. `schemas.ts` is the mirror of `apps/api/responses.py`, so there
 * is exactly one definition of `{item_id, row_index}` and of a refusal in the whole repo; a local
 * interface would be a second one, and the first time somebody adds a field the two would part
 * ways without a single test noticing.
 *
 * `RefusedRow.reason` is `EXPORT_REFUSAL_REASONS`, **not** `ReasonCode`: an export refusal is
 * fixed by the seller, a job failure is retried by the pipeline. See CONTRACTS §5.
 *
 * Re-exported, not redeclared. A local `type ExportedRow = z.infer<...>` here was structurally
 * identical to the one in `schemas.ts` but a *different* declaration, so `src/index.ts` (which does
 * `export *` from both modules) failed with TS2308 "has already exported a member named
 * 'ExportedRow'". Two declarations of one wire shape is the exact drift this repo tests against —
 * the fix is one declaration, re-exported.
 */
export type {
  ExportedRowModel as ExportedRow,
  RefusedRowModel as RefusedRow,
} from "../schemas";

/**
 * The refusal shape comes from `schemas.ts` (`RefusedRow`), not from a local declaration.
 *
 * `schemas.ts` is the zod mirror of `apps/api/responses.py`, and the API returns exactly that
 * shape. Declaring a second, structurally identical interface here would be two definitions of one
 * wire object — the drift this repo spends most of its tests preventing. `reason` on it is
 * `EXPORT_REFUSAL_REASONS`, which is **not** `ReasonCode`: an export refusal is fixed by the
 * seller, a job failure is retried by the pipeline.
 */

/** Everything a mapper may look at. `attributes` is null until the VLM has run (E3). */
export interface ListingSource {
  item_id: string;
  attributes: ItemAttributes | null;
  /** Signed Supabase Storage URL, or null while the job is still queued. */
  prettified_url?: string | null;
  /** In render order; empty unless an on-model job ran. */
  on_model_urls?: readonly string[];
  /**
   * Seller-set price in **integer cents**. Absent or `null` means "not provided".
   *
   * Per item, never per batch: a batch-level price would silently price every garment the same,
   * and nothing would error. `0` is a real price (a giveaway / bundle filler) and is NOT the
   * same as absent — see `formatPriceCents`.
   */
  price_cents?: number | null;
}

export type MapperOutcome = { row: string[] } | { refusal: RefusedRow };

/**
 * How much to trust one exported column, recorded per marketplace so a seller-facing doc can
 * never quietly upgrade an unverified header to fact.
 *
 * `documented`  — the name appears in the marketplace's own template/developer docs (URL given)
 * `unverified`  — we believe it, but no primary source was confirmed for this field
 * `ours`        — a column of ours that the marketplace will ignore (kept for the seller)
 */
export type ColumnStatus = "documented" | "unverified" | "ours";

export interface ColumnSource {
  header: string;
  status: ColumnStatus;
  source: string;
  /**
   * Literal to publish in `requires_manual_columns` when this column is empty in any exported
   * row, or `null` when an empty cell is a legitimate state rather than the seller's job.
   *
   * Naming: `<marketplace>_<column meaning>` in snake_case — never the raw CSV header, which
   * carries `*` and spaces and makes a poor JSON key.
   */
  manual_when_empty: string | null;
}

/** Options a mapper may accept. Batch-level only; anything per item lives on `ListingSource`. */
export interface MapperOptions {
  /**
   * Verified marketplace category ids, by our vocabulary. Empty until a taxonomy source exists
   * (E7-T1 for eBay). When an id is present, the column is no longer empty and therefore drops
   * out of `requires_manual_columns` — no code change needed.
   */
  categoryIds?: Partial<Record<GarmentCategory, number>>;
}

export interface MarketplaceMapper {
  readonly marketplace: Marketplace;
  readonly headers: readonly string[];
  /** Per-column provenance. Length must equal `headers.length`; a test enforces that. */
  readonly sources: readonly ColumnSource[];
  /** Machine-readable flags surfaced on the export response (see `ExportBatch.warnings`). */
  readonly warnings: readonly string[];
  toRow(source: ListingSource, options?: MapperOptions): MapperOutcome;
}

export interface ExportBatch {
  /** Empty exactly when every row was refused; a caller must never treat that as success. */
  csv: string;
  exported: ExportedRow[];
  refused: RefusedRow[];
  /** Derived from the rule above, in header order. Empty array means "nothing to fill in". */
  requires_manual_columns: string[];
  headers: readonly string[];
  warnings: readonly string[];
}

export function refusal(itemId: string, fields: readonly string[]): MapperOutcome {
  return { refusal: { item_id: itemId, reason: "insufficient_data", fields: [...fields] } };
}

/** True when every field in `fields` is present and non-empty. */
export function hasAll(
  attributes: ItemAttributes,
  fields: readonly (keyof ItemAttributes)[],
): boolean {
  return fields.every((field) => {
    const value = attributes[field];
    if (value === null || value === undefined) return false;
    if (typeof value === "string") return value.trim().length > 0;
    if (Array.isArray(value)) return value.length > 0;
    return true;
  });
}

/**
 * Render a price for a CSV cell.
 *
 * - absent / `null` / non-finite → `""` (the column is empty; the seller must fill it)
 * - `0` → `"0.00"` — a real zero-price listing, not "missing"
 * - negative → throws, because a negative price is invalid input, not a formatting choice
 *
 * The `0` vs `null` distinction is the expensive one on this path: both render as a short string
 * in a spreadsheet, the difference only surfaces when a buyer receives a free item.
 */
export function formatPriceCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "";
  if (!Number.isFinite(cents)) return "";
  if (cents < 0) {
    throw new Error(`price_cents must not be negative (got ${cents})`);
  }
  return (cents / 100).toFixed(2);
}

/**
 * The rule, executable: which declared manual columns are actually empty in this batch.
 *
 * Reads `sources` (declaration) and the produced rows (evidence). `any`, not `all` — see the
 * module docstring. Order follows the header order so the output is deterministic and readable
 * next to the CSV.
 */
export function computeRequiresManualColumns(
  mapper: MarketplaceMapper,
  rows: readonly (readonly (string | number | boolean | null | undefined)[])[],
): string[] {
  if (rows.length === 0) return [];
  const required: string[] = [];
  for (let index = 0; index < mapper.headers.length; index += 1) {
    const literal = mapper.sources[index]?.manual_when_empty ?? null;
    if (!literal) continue;
    const emptyInSomeRow = rows.some((row) => {
      const cell = row[index];
      return cell === null || cell === undefined || String(cell) === "";
    });
    if (emptyInSomeRow) required.push(literal);
  }
  return required;
}
