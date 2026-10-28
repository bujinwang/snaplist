/**
 * Minimal RFC-4180 CSV writer.
 *
 * Why not a library: the whole contract with a marketplace is "the header line and the escaping
 * are byte-exact". A 60-line writer we can read line by line is easier to defend in a review
 * than a dependency whose defaults we would have to pin anyway.
 *
 * Rules (RFC 4180, plus the two things Excel needs):
 *  1. a field is quoted when it contains `,`, `"`, CR or LF, or has leading/trailing space
 *  2. an embedded `"` is doubled
 *  3. records are terminated with CRLF — what eBay's templates and Excel both expect; a
 *     seller who opens a LF-only file in Excel on Windows gets one giant row
 *  4. `null` / `undefined` become an empty field, never the strings "null" / "undefined"
 *  5. a trailing CRLF terminates the last record
 *  6. **a row whose cell count differs from the header is refused, not padded** (see below)
 *
 * The escaping is unit-tested against the four hostile inputs (comma, quote, newline, and a
 * field that is exactly one quote) in `test/marketplaces.test.ts`.
 */

export type CsvValue = string | number | boolean | null | undefined;

const NEEDS_QUOTING = /[",\r\n]/;
const LEADING_OR_TRAILING_SPACE = /^\s|\s$/;

/** Preview length for a diagnostic; enough to recognise the row, short enough for a log line. */
const PREVIEW_CHARS = 40;
const PREVIEW_CELLS = 3;

/**
 * A row and the header disagree about how many columns there are.
 *
 * This is thrown instead of padding, because of what the two failure modes look like:
 *
 * - **padded** — the file is well-formed and every column after the missing one is silently
 *   shifted by one. A Poshmark `Size` ends up in `Color`. Nothing errors, the upload even
 *   succeeds, and the seller finds out when a buyer receives the wrong size.
 * - **refused** — one loud error, one refused export, zero wrong listings.
 *
 * There is no third option where the tool guesses which column was meant.
 */
export class CsvShapeError extends Error {
  readonly rowIndex: number;
  readonly got: number;
  readonly expected: number;

  constructor(rowIndex: number, got: number, expected: number, preview: string) {
    super(
      `CSV row ${rowIndex} has ${got} cells but the header has ${expected}; ` +
        `refusing to emit a file that would shift every column after it. ` +
        `First cells: [${preview}]`,
    );
    this.name = "CsvShapeError";
    this.rowIndex = rowIndex;
    this.got = got;
    this.expected = expected;
  }
}

/** Escape one field. Exported because the mappers test it directly. */
export function csvEscape(value: CsvValue): string {
  if (value === null || value === undefined) return "";
  const text = typeof value === "string" ? value : String(value);
  if (text === "") return "";
  if (NEEDS_QUOTING.test(text) || LEADING_OR_TRAILING_SPACE.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

/** One record, without the line terminator. */
export function formatCsvRow(cells: readonly CsvValue[]): string {
  return cells.map(csvEscape).join(",");
}

function previewOf(row: readonly CsvValue[]): string {
  return row
    .slice(0, PREVIEW_CELLS)
    .map((cell) => {
      const text = cell === null || cell === undefined ? "" : String(cell);
      const flat = text.replace(/[\r\n]+/g, " ");
      return flat.length > PREVIEW_CHARS ? `${flat.slice(0, PREVIEW_CHARS - 1)}…` : flat;
    })
    .join(" | ");
}

/**
 * A complete document: header, then one line per row, CRLF-terminated including the last.
 *
 * Every row must have exactly `header.length` cells. A mismatch throws `CsvShapeError` with the
 * row index, both counts and a short preview — a bare assertion would leave whoever debugs it
 * counting commas by hand. See `CsvShapeError` for why padding is the wrong repair.
 */
export function writeCsv(header: readonly string[], rows: readonly (readonly CsvValue[])[]): string {
  const lines = [formatCsvRow(header)];
  rows.forEach((row, rowIndex) => {
    if (row.length !== header.length) {
      throw new CsvShapeError(rowIndex, row.length, header.length, previewOf(row));
    }
    lines.push(formatCsvRow(row));
  });
  return `${lines.join("\r\n")}\r\n`;
}

/** Number of data rows (excludes the header). Handy in tests and diagnostics. */
export function countCsvRows(document: string): number {
  const lines = document.split("\r\n").filter((line) => line.length > 0);
  return Math.max(lines.length - 1, 0);
}

/**
 * Number of columns in the header line.
 *
 * Note: `split("\r\n")` is safe for our documents because a quoted field never contains CRLF
 * (descriptions join lines with LF only), but it would not survive a hand-edited file. It is a
 * diagnostic helper for tests, not a parser for untrusted input.
 */
export function countCsvColumns(document: string): number {
  const [header = ""] = document.split("\r\n");
  if (header.length === 0) return 0;
  return parseCsvLine(header).length;
}

/**
 * A deliberately small CSV line parser, used only by tests and diagnostics (`countCsvColumns`).
 * It handles quoted fields and doubled quotes, and LF inside a quoted field; it does not handle
 * CRLF inside one, which our documents never contain.
 */
export function parseCsvLine(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index] as string;
    if (inQuotes) {
      if (char === '"') {
        if (line[index + 1] === '"') {
          current += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        current += char;
      }
      continue;
    }
    if (char === '"') {
      inQuotes = true;
    } else if (char === ",") {
      fields.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  fields.push(current);
  return fields;
}
