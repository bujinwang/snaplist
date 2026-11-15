/**
 * Human confirmation must clear the publish gate (E6-T3).
 *
 * Two things are tested, and they fail in different ways, which is why both are here:
 *
 *   (a) `applyHumanConfirmation` — the pure function. A confirmation raises that field's
 *       confidence to `ATTR_CONFIRMED_CONFIDENCE` and takes it off `needs_review`, WITHOUT
 *       disturbing any other field's score. That last clause is the one that matters: the
 *       draft provider merges patches shallowly, so a function that returned only the
 *       confirmed entry would look right in isolation and silently wipe every other
 *       attribute's confidence on the way to storage.
 *
 *   (b) the wiring — `Review.tsx` must actually call it and must send the WHOLE attribute
 *       set to `patchAttributes`. A correct function nobody calls is the failure this
 *       project has already paid for once.
 *
 * `node --test` cannot import TypeScript, so the sources are compiled with the app's own
 * `typescript` into a temp directory and required from there — the same approach
 * `storage-contract.test.mjs` uses. Nothing generated is committed.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

const mobileRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const nodeRequire = createRequire(import.meta.url);

const scratch = mkdtempSync(join(tmpdir(), "snaplist-attributes-confirmation-"));

execFileSync(
  process.execPath,
  [
    nodeRequire.resolve("typescript/bin/tsc"),
    join("src", "lib", "attributes.ts"),
    "--outDir",
    scratch,
    // Explicit, not inferred: tsc derives the root from the inputs, and with every input under
    // `src/lib` it would emit `<scratch>/attributes.js` instead of `<scratch>/lib/attributes.js`.
    "--rootDir",
    "src",
    "--module",
    "commonjs",
    "--target",
    "es2020",
    "--skipLibCheck",
    "--esModuleInterop",
    "--moduleResolution",
    "node",
  ],
  { cwd: mobileRoot, stdio: "pipe" },
);

const { applyHumanConfirmation } = nodeRequire(join(scratch, "lib", "attributes.js"));
const confidenceConstants = nodeRequire(join(scratch, "lib", "confidence.js"));

after(() => {
  rmSync(scratch, { recursive: true, force: true });
});

/** A complete attribute set with mixed confidence, as the engine produces for an inference. */
function attributeFixture() {
  return {
    category: "top",
    colors: ["black"],
    pattern: "solid",
    material: ["silk"], // inferred: below the gate on purpose
    fit: "regular",
    condition: "good",
    defects: [],
    keywords: [],
    confidence: { category: 0.93, colors: 0.88, pattern: 0.81, material: 0.55, fit: 0.9, condition: 0.77 },
    needs_review: ["material", "condition"],
    model_version: "attrs-v1",
  };
}

test("confirmation_raises_the_confirmed_field_to_full_confidence", () => {
  const attrs = attributeFixture();
  const next = applyHumanConfirmation(attrs, "material", ["cotton"]);

  assert.deepEqual(next.material, ["cotton"], "the confirmed value must be applied");
  assert.equal(
    next.confidence.material,
    confidenceConstants.ATTR_CONFIRMED_CONFIDENCE,
    "a confirmed field must carry the confirmation confidence",
  );
  assert.ok(
    next.confidence.material > confidenceConstants.ATTR_CONFIDENCE_FLOOR,
    "a confirmed field must clear the publish gate, or the listing still withholds it",
  );
});

test("confirmation_leaves_every_other_confidence_untouched", () => {
  // The shallow-merge accident: a function that returns `{ [field]: 1 }` passes every
  // single-field assertion above and destroys the rest of the map on the way to storage.
  const attrs = attributeFixture();
  const next = applyHumanConfirmation(attrs, "material", ["cotton"]);

  for (const field of ["category", "colors", "pattern", "fit", "condition"]) {
    assert.equal(
      next.confidence[field],
      attrs.confidence[field],
      `confirming material must not change ${field}'s confidence`,
    );
  }
  assert.equal(next.confidence.material, confidenceConstants.ATTR_CONFIRMED_CONFIDENCE);
  assert.deepEqual(Object.keys(next.confidence).sort(), Object.keys(attrs.confidence).sort(), "no key may be dropped");
});

test("confirmation_removes_only_that_field_from_needs_review", () => {
  const attrs = attributeFixture();
  const next = applyHumanConfirmation(attrs, "material", ["cotton"]);

  assert.deepEqual(next.needs_review, ["condition"], "the confirmed field leaves needs_review, the rest stay");
});

test("confirmation_does_not_mutate_the_input", () => {
  const attrs = attributeFixture();
  const before = JSON.parse(JSON.stringify(attrs));

  const next = applyHumanConfirmation(attrs, "material", ["cotton"]);

  assert.deepEqual(attrs, before, "the argument must not be mutated");
  assert.notEqual(next, attrs, "a new object must come back");
  assert.notEqual(next.confidence, attrs.confidence, "the confidence map must be a new object");
  assert.notEqual(next.needs_review, attrs.needs_review, "the needs_review array must be a new one");
});

// ---------------------------------------------------------------------------------------------
// (b) Wiring: a correct function nobody calls proves nothing
// ---------------------------------------------------------------------------------------------

/** The body of `applyEdit`, so the wiring assertions are about that function and not the file. */
function applyEditBody() {
  const source = readFileSync(join(mobileRoot, "src", "screens", "Review.tsx"), "utf8");
  const start = source.indexOf("const applyEdit = async");
  assert.notEqual(start, -1, "applyEdit is gone from Review.tsx — did it get renamed?");
  const end = source.indexOf("\n  };", start);
  assert.notEqual(end, -1, "could not find the end of applyEdit");
  return source.slice(start, end);
}

test("review_screen_actually_confirms_the_edit", () => {
  const body = applyEditBody();

  assert.match(
    body,
    /applyHumanConfirmation\(\s*prev\s*,\s*field\s*,\s*value\s*\)/,
    "applyEdit must compute the next attributes with applyHumanConfirmation",
  );
  assert.match(
    body,
    /const nextAttrs = applyHumanConfirmation\(/,
    "the confirmed attributes must be what the optimistic update and the patch both use",
  );
  assert.match(
    body,
    /patchAttributes\(\s*item\.item_id\s*,\s*nextAttrs\s*\)/,
    "patchAttributes must receive the whole attribute set, not a partial patch",
  );
  assert.doesNotMatch(
    body,
    /patchAttributes\(\s*item\.item_id\s*,\s*\{/,
    "a partial patch would be shallow-merged and wipe the other confidences",
  );
  assert.doesNotMatch(body, /buildPatch/, "buildPatch was removed; a partial-patch builder must not come back");
});
