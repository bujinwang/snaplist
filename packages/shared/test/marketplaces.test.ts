/**
 * Marketplace mapper tests (E7-T1/T2).
 *
 * The header assertions are written as **literal strings**, not imported constants, on purpose:
 * if somebody edits `EBAY_HEADERS` this test must go red and force them to read
 * `EBAY_SOURCES` and decide whether the change is verified. A test that imported the constant
 * would keep passing through a silent rename, which is the exact drift this file exists to stop.
 *
 * Everything here runs offline against fixtures on disk. No eBay API, no credentials.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  EBAY_CONDITION_IDS,
  EBAY_HEADERS,
  EBAY_SOURCES,
  ItemAttributes,
  POSHMARK_HEADERS,
  POSHMARK_SOURCES,
  VINTED_HEADERS,
  VINTED_SOURCES,
  countCsvColumns,
  countCsvRows,
  csvEscape,
  exportRows,
  parseCsvLine,
  toEbayRow,
  writeCsv,
} from "../src/index";

const ITEM_ID = "9f1b0c62-5f2f-4a59-9a1d-0c9a1f66b0aa";

/** A plain black cotton crewneck. No brand, no invented fibres. */
function tee(overrides: Record<string, unknown> = {}) {
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

function source(overrides: Record<string, unknown> = {}) {
  return {
    item_id: ITEM_ID,
    attributes: tee(),
    prettified_url: "https://example.test/items/1/prettified.png",
    on_model_urls: [] as string[],
    ...overrides,
  };
}

/* -------------------------------------------------------------------------------------------- */
/* Header provenance: byte-exact, and every column accounted for                                 */
/* -------------------------------------------------------------------------------------------- */

test("eBay headers are exactly the pinned File Exchange column list", () => {
  assert.deepEqual([...EBAY_HEADERS], [
    "*Action(SiteID=US|Country=US|Currency=USD|Version=745|CC=UTF-8)",
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
  ]);
});

test("Poshmark headers are pinned", () => {
  assert.deepEqual([...POSHMARK_HEADERS], [
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
  ]);
});

test("Vinted headers are pinned", () => {
  assert.deepEqual([...VINTED_HEADERS], [
    "Title",
    "Description",
    "Category",
    "Brand",
    "Size",
    "Condition",
    "Colour",
    "Material",
    "Package size",
  ]);
});

test("every header has exactly one provenance row, in order", () => {
  const pairs: ReadonlyArray<{
    headers: readonly string[];
    sources: readonly { header: string; status: string; source: string }[];
  }> = [
    { headers: EBAY_HEADERS, sources: EBAY_SOURCES },
    { headers: POSHMARK_HEADERS, sources: POSHMARK_SOURCES },
    { headers: VINTED_HEADERS, sources: VINTED_SOURCES },
  ];
  for (const { headers, sources } of pairs) {
    assert.equal(sources.length, headers.length);
    headers.forEach((header: string, index: number) => {
      assert.equal(sources[index]?.header, header, `provenance out of order at ${header}`);
      assert.ok(
        ["documented", "unverified", "ours"].includes(sources[index]?.status ?? ""),
        `unknown status for ${header}`,
      );
    });
  }
});

test("no column claims 'documented' without a source URL or an explicit note", () => {
  for (const column of [...EBAY_SOURCES, ...POSHMARK_SOURCES, ...VINTED_SOURCES]) {
    if (column.status === "documented") {
      assert.match(column.source, /^https?:\/\//, `${column.header} needs a real source URL`);
    } else {
      assert.ok(column.source.length > 10, `${column.header} needs a written reason`);
    }
  }
});

/* -------------------------------------------------------------------------------------------- */
/* Condition tables are eBay's / Poshmark's / Vinted's enums, not ours                          */
/* -------------------------------------------------------------------------------------------- */

test("condition maps to eBay's numeric enum, never to our vocabulary", () => {
  assert.deepEqual(EBAY_CONDITION_IDS, {
    new_with_tags: 1000,
    new_without_tags: 1500,
    excellent: 3000,
    good: 3000,
    fair: 3000,
    poor: 7000,
  });
  const outcome = toEbayRow(source());
  assert.ok(outcome && "row" in outcome);
  const headers = [...EBAY_HEADERS];
  const conditionIndex = headers.indexOf("*ConditionID");
  assert.equal(outcome.row[conditionIndex], "3000");
});

/* -------------------------------------------------------------------------------------------- */
/* CSV shape and escaping                                                                        */
/* -------------------------------------------------------------------------------------------- */

test("a one-item eBay export has exactly one data row and 15 columns", () => {
  const batch = exportRows("ebay", [source()]);
  assert.equal(batch.exported.length, 1);
  assert.equal(batch.refused.length, 0);
  assert.equal(countCsvRows(batch.csv), 1);
  assert.equal(countCsvColumns(batch.csv), EBAY_HEADERS.length);
  // CRLF, including a terminator after the last record (Excel needs it).
  assert.ok(batch.csv.endsWith("\r\n"));
  assert.ok(batch.csv.split("\r\n")[0]?.startsWith("*Action(SiteID=US"));
});

test("two items produce two data rows and no duplicate header", () => {
  const batch = exportRows("ebay", [
    source(),
    source({ item_id: "11111111-1111-4111-8111-111111111111" }),
  ]);
  assert.equal(batch.exported.length, 2);
  assert.equal(countCsvRows(batch.csv), 2);
  assert.equal(batch.csv.split(EBAY_HEADERS[1] as string).length, 2, "header must appear once");
});

test("fields containing a comma, a quote, a newline and a bare quote are escaped", () => {
  assert.equal(csvEscape("plain"), "plain");
  assert.equal(csvEscape("has,comma"), '"has,comma"');
  assert.equal(csvEscape('has"quote'), '"has""quote"');
  assert.equal(csvEscape('"'), '""""');
  assert.equal(csvEscape("line\nbreak"), '"line\nbreak"');
  assert.equal(csvEscape("  padded  "), '"  padded  "');
  assert.equal(csvEscape(null), "");
  assert.equal(csvEscape(undefined), "");
  assert.equal(csvEscape(0), "0");

  // A description with all three hostile characters still round-trips through a quoted field.
  const nasty = tee({ subcategory: 'tee, "the good one"\nsecond line' });
  const batch = exportRows("ebay", [source({ attributes: nasty })]);
  assert.equal(batch.exported.length, 1);
  assert.ok(batch.csv.includes('"'));
  // Parse the header back: the arity must not change because a description got creative.
  const headerLine = batch.csv.split("\r\n")[0] as string;
  assert.equal(parseCsvLine(headerLine).length, EBAY_HEADERS.length);
});

test("an item with no photo is refused rather than exported with an empty PicURL", () => {
  const batch = exportRows("ebay", [source({ prettified_url: null, on_model_urls: [] })]);
  assert.equal(batch.exported.length, 0);
  assert.equal(batch.refused.length, 1);
  assert.deepEqual(batch.refused[0]?.fields, ["prettified_url"]);
  assert.equal(batch.csv, "");
});

test("an item whose pipeline has not produced attributes is refused", () => {
  const batch = exportRows("ebay", [source({ attributes: null })]);
  assert.equal(batch.exported.length, 0);
  assert.deepEqual(batch.refused[0]?.fields, ["attributes"]);
});

/* -------------------------------------------------------------------------------------------- */
/* Refusal rules specific to each marketplace                                                   */
/* -------------------------------------------------------------------------------------------- */

test("Poshmark refuses a null brand instead of inventing one", () => {
  const batch = exportRows("poshmark", [source()]); // brand is null in the fixture
  assert.equal(batch.exported.length, 0);
  assert.deepEqual(batch.refused[0]?.fields, ["brand"]);
  assert.equal(batch.refused[0]?.reason, "insufficient_data");

  const withBrand = exportRows("poshmark", [source({ attributes: tee({ brand: "Nike" }) })]);
  assert.equal(withBrand.exported.length, 1);
  assert.equal(countCsvRows(withBrand.csv), 1);
  assert.equal(countCsvColumns(withBrand.csv), POSHMARK_HEADERS.length);
});

test("Vinted requires both brand and size", () => {
  const noBrand = exportRows("vinted", [source()]);
  assert.deepEqual(noBrand.refused[0]?.fields, ["brand"]);

  const noSize = exportRows("vinted", [source({ attributes: tee({ brand: "Nike", size_label: null }) })]);
  assert.deepEqual(noSize.refused[0]?.fields, ["size_label"]);

  const ok = exportRows("vinted", [
    source({ attributes: tee({ brand: "Nike", size_label: "M" }) }),
  ]);
  assert.equal(ok.exported.length, 1);
  assert.equal(countCsvColumns(ok.csv), VINTED_HEADERS.length);
});

test("Vinted keeps a known fibre, drops an unknown one, and takes only the primary colour", () => {
  const batch = exportRows("vinted", [
    source({
      attributes: tee({ brand: "Nike", material: ["cotton", "unobtainium"], colors: ["black", "grey"] }),
    }),
  ]);
  assert.equal(batch.exported.length, 1);
  // The description is a quoted field containing commas, so it must be parsed, not split.
  const row = parseCsvLine(batch.csv.split("\r\n")[1] as string);
  assert.equal(row.length, VINTED_HEADERS.length);
  assert.equal(row[6], "black", "primary colour only");
  assert.equal(row[7], "Cotton", "known fibre mapped, unknown dropped");
});

/* -------------------------------------------------------------------------------------------- */
/* Warnings are part of the payload, not a comment                                              */
/* -------------------------------------------------------------------------------------------- */

test("unverified exports say so in machine-readable warnings", () => {
  for (const marketplace of ["ebay", "poshmark", "vinted"] as const) {
    const batch = exportRows(marketplace, [
      source({ attributes: tee({ brand: "Nike" }) }),
    ]);
    assert.ok(batch.warnings.length > 0, `${marketplace} must declare its warnings`);
  }
  assert.ok(
    exportRows("ebay", [source()]).warnings.includes("ebay_category_id_unverified"),
    "the eBay category id is not resolved, and the export must say so",
  );
});

test("the eBay category column stays empty rather than carrying a guessed id", () => {
  const batch = exportRows("ebay", [source()]);
  const headerLine = batch.csv.split("\r\n")[0] as string;
  const columns = parseCsvLine(headerLine);
  const categoryIndex = columns.indexOf("*Category");
  assert.ok(categoryIndex > 0);
  const firstRow = parseCsvLine(batch.csv.split("\r\n")[1] as string);
  // The description contains commas and newlines, so a naive split is wrong; assert on the
  // column count instead and that no numeric id was smuggled in before the title.
  assert.equal(firstRow.length, columns.length);
  assert.equal(firstRow[1], "", "no fabricated category id");
});

/* -------------------------------------------------------------------------------------------- */
/* `requires_manual_columns`: the rule, not a list                                              */
/*                                                                                              */
/* Governing line: a column earns a literal when an EMPTY CELL MAKES THE ROW INVALID ON UPLOAD.  */
/* The quantifier is `any` — a batch of three with two prices still needs the third one filled.  */
/* These tests derive the expectation from the CSV cells the mapper actually produced, so adding */
/* a marketplace or a column cannot silently skip the question.                                  */
/* -------------------------------------------------------------------------------------------- */

/** Literals a mapper can publish while a given column is empty, read from its own declaration. */
function declaredManuals(mapper: { sources: readonly { header: string; manual_when_empty: string | null }[] }, header: string): string | null {
  return mapper.sources.find((column) => column.header === header)?.manual_when_empty ?? null;
}

test("every mapper declares manual_when_empty for every column", () => {
  const mappers = [
    { name: "ebay", sources: EBAY_SOURCES },
    { name: "poshmark", sources: POSHMARK_SOURCES },
    { name: "vinted", sources: VINTED_SOURCES },
  ];
  for (const { name, sources } of mappers) {
    for (const column of sources) {
      assert.ok(
        column.manual_when_empty === null || /^[a-z][a-z0-9_]*$/.test(column.manual_when_empty),
        `${name}/${column.header}: manual_when_empty must be null or a snake_case literal`,
      );
    }
  }
});

test("only columns whose emptiness invalidates the row carry a literal", () => {
  // The declaration IS the rule's input, so this pins the judgement for the four cases that
  // matter today. Attribute columns (brand, size, era) are edited in the app, not in the CSV.
  assert.equal(declaredManuals({ sources: EBAY_SOURCES }, "*Category"), "ebay_category_id");
  assert.equal(declaredManuals({ sources: EBAY_SOURCES }, "*StartPrice"), "ebay_start_price");
  assert.equal(declaredManuals({ sources: EBAY_SOURCES }, "C:Brand"), null);
  assert.equal(declaredManuals({ sources: POSHMARK_SOURCES }, "Price"), "poshmark_price");
  assert.equal(
    declaredManuals({ sources: POSHMARK_SOURCES }, "Original Price"),
    null,
    "a strikethrough anchor does not block an upload",
  );
  assert.equal(declaredManuals({ sources: VINTED_SOURCES }, "Package size"), "vinted_package_size");
});

test("with no price: both eBay columns are reported, in header order", () => {
  const batch = exportRows("ebay", [source()]);
  assert.deepEqual(batch.requires_manual_columns, ["ebay_category_id", "ebay_start_price"]);
});

test("a supplied price drops ebay_start_price from the list", () => {
  const batch = exportRows("ebay", [source({ price_cents: 1900 })]);
  assert.deepEqual(batch.requires_manual_columns, ["ebay_category_id"]);
  const row = parseCsvLine(batch.csv.split("\r\n")[1] as string);
  const priceIndex = [...EBAY_HEADERS].indexOf("*StartPrice");
  assert.equal(row[priceIndex], "19.00");
});

test("a zero price renders 0.00 and is NOT a manual column", () => {
  // A giveaway is a real price. Rendering 0 as empty (or as null) would ask the seller to fill a
  // field they already filled — and turn a giveaway into a missing price on upload.
  const batch = exportRows("ebay", [source({ price_cents: 0 })]);
  const row = parseCsvLine(batch.csv.split("\r\n")[1] as string);
  const priceIndex = [...EBAY_HEADERS].indexOf("*StartPrice");
  assert.equal(row[priceIndex], "0.00");
  assert.equal(batch.requires_manual_columns.includes("ebay_start_price"), false);
});

test("a partially priced batch still reports the price column (quantifier is any, not all)", () => {
  const batch = exportRows("ebay", [
    source({ item_id: "11111111-1111-4111-8111-111111111111", price_cents: 1900 }),
    source({ item_id: "22222222-2222-4222-8222-222222222222", price_cents: 2400 }),
    source({ item_id: "33333333-3333-4333-8333-333333333333" }), // no price
  ]);
  assert.equal(batch.exported.length, 3);
  assert.deepEqual(batch.requires_manual_columns, ["ebay_category_id", "ebay_start_price"]);
});

test("a fully priced batch reports only the category column", () => {
  const batch = exportRows("ebay", [
    source({ item_id: "11111111-1111-4111-8111-111111111111", price_cents: 1900 }),
    source({ item_id: "22222222-2222-4222-8222-222222222222", price_cents: 2400 }),
  ]);
  assert.deepEqual(batch.requires_manual_columns, ["ebay_category_id"]);
});

test("an attribute that one item lacks does not become a batch-level manual column", () => {
  // Two items, only one has an era. `C:*` columns declare null, so nothing is reported: an empty
  // era is a fact about that garment, not an instruction to the seller. This is the mirror of the
  // partial-price case above and the reason the declaration, not the data, decides.
  const batch = exportRows("ebay", [
    source({ item_id: "11111111-1111-4111-8111-111111111111", attributes: tee({ era: "90s" }) }),
    source({ item_id: "22222222-2222-4222-8222-222222222222" }),
  ]);
  assert.equal(batch.exported.length, 2);
  assert.deepEqual(batch.requires_manual_columns, ["ebay_category_id", "ebay_start_price"]);
});

test("a single-item batch missing an attribute still reports nothing extra", () => {
  // The extreme case: with one row, structural emptiness and item-level emptiness look identical
  // in the data. Only the declaration tells them apart — which is why it is the primary signal.
  const batch = exportRows("ebay", [source({ attributes: tee({ era: null, brand: null }) })]);
  assert.deepEqual(batch.requires_manual_columns, ["ebay_category_id", "ebay_start_price"]);
});

test("an empty batch reports no manual columns", () => {
  const batch = exportRows("ebay", [source({ attributes: null })]);
  assert.equal(batch.exported.length, 0);
  assert.deepEqual(batch.requires_manual_columns, []);
});

test("Poshmark reports the price column exactly when a row has no price", () => {
  const withBrand = { attributes: tee({ brand: "Nike" }) };
  const priced = exportRows("poshmark", [source({ ...withBrand, price_cents: 2500 })]);
  assert.deepEqual(priced.requires_manual_columns, []);

  const unpriced = exportRows("poshmark", [source(withBrand)]);
  assert.deepEqual(unpriced.requires_manual_columns, ["poshmark_price"]);
});

test("Vinted always reports the package size column", () => {
  const batch = exportRows("vinted", [
    source({ attributes: tee({ brand: "Nike" }), price_cents: 2500 }),
  ]);
  assert.deepEqual(batch.requires_manual_columns, ["vinted_package_size"]);
});

/* -------------------------------------------------------------------------------------------- */
/* Row-width guard                                                                              */
/* -------------------------------------------------------------------------------------------- */

test("a row with the wrong number of cells is refused, naming the row and both counts", () => {
  // The failure this prevents: padding. A padded file is well-formed and every column after the
  // missing cell shifts by one — Size lands in Color, the upload succeeds, and the seller finds
  // out when a buyer receives the wrong item.
  assert.throws(
    () => writeCsv(["a", "b", "c"], [["1", "2", "3"], ["1", "2"]]),
    (error: unknown) => {
      const shape = error as { name?: string; rowIndex?: number; got?: number; expected?: number; message?: string };
      assert.equal(shape.name, "CsvShapeError");
      assert.equal(shape.rowIndex, 1, "the offending row index must be reported");
      assert.equal(shape.got, 2);
      assert.equal(shape.expected, 3);
      assert.match(shape.message ?? "", /row 1 has 2 cells but the header has 3/);
      return true;
    },
  );
});

test("a correct row passes the width guard", () => {
  assert.equal(writeCsv(["a", "b"], [["1", "2"], ["3", "4"]]), "a,b\r\n1,2\r\n3,4\r\n");
});
