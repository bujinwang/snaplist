/**
 * The mobile analytics vocabulary is a hand-written MIRROR of the frozen schema in
 * `packages/shared/src/analytics.ts`. That mirror's own docstring says the two
 * "must never drift" — and until this file existed, nothing checked it. A rule
 * written in prose and enforced nowhere is the exact failure shape this milestone
 * produced twice already (a docstring promising `never rejects`; a gate that
 * measured nothing).
 *
 * Why the duplication exists at all: the app is built to work without
 * `@snaplist/shared` installed, so the vocabulary is copied rather than imported.
 * The copy is deliberate; what was missing is the thing duplication always needs —
 * a failure the moment one side moves.
 *
 * Why not import both and compare at runtime: the shared module is zod, the mirror
 * is plain interfaces. Comparing them would need a runtime value on the mobile
 * side, which is the dependency the mirror exists to avoid. So this reads both
 * SOURCES and compares the shape they declare.
 *
 * What is compared, and why it is exactly this:
 *
 *   1. the seven event names;
 *   2. per event, every field the mobile emitter can send must exist in the schema
 *      — the schema is `.strict()`, so an unknown key is a rejected event;
 *   3. per event, every schema field that is not optional must be emitted by the
 *      mobile emitter — a missing required field is a rejected event too;
 *   4. the mobile side may not make a schema-required field optional (it would
 *      compile, then fail in production);
 *   5. the shared vocabularies: marketplace ids, paywall variants, paywall triggers.
 *
 * Note what falls out of (2)+(3) without an exemption list: `subscription_started`
 * declares `item_id: z.null().optional()` in the schema and omits it entirely in
 * the mirror. Optional, so (3) does not require it; absent, so (2) is satisfied.
 * Both files enforce the same rule (no real item id) by different means, and this
 * guard does not need to be told that.
 *
 * What is deliberately NOT compared: types, refinements, defaults, regexes. Those
 * belong to the schema, which the server enforces; a mirror that re-implemented
 * them would be a second source of truth, which is the problem this replaces.
 *
 * Scan-nothing protection: both extractors assert they found seven events, so a
 * regex that stops matching reports itself instead of reporting "no drift".
 *
 * Run with the built-in runner, no test framework and no new dependencies:
 *   node --test test/*.test.mjs
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const testDir = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = join(testDir, "..", "..", "..");
const MOBILE_FILE = join(repoRoot, "apps", "mobile", "src", "lib", "analytics.ts");
const SHARED_FILE = join(repoRoot, "packages", "shared", "src", "analytics.ts");

/** The frozen count. `packages/shared/src/analytics.ts` calls it "Seven event names. Frozen." */
const FROZEN_EVENT_COUNT = 7;

function read(path) {
  try {
    return readFileSync(path, "utf8");
  } catch (err) {
    assert.fail(`cannot read ${path}: ${err.message}`);
  }
}

/**
 * The `{ ... }` body that follows `marker`, brace-balanced and string-aware.
 *
 * Returns null rather than throwing when the marker is absent, so the caller can
 * name what it failed to find.
 */
function objectBodyAfter(text, marker) {
  const at = text.indexOf(marker);
  if (at === -1) return null;
  const open = text.indexOf("{", at + marker.length - 1);
  if (open === -1) return null;

  let depth = 0;
  let quote = "";
  let escaped = false;
  for (let i = open; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === quote) quote = "";
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return text.slice(open + 1, i);
    }
  }
  return null;
}

/** `["a", "b"]` / `"a" | "b"` -> ["a", "b"]. */
function quotedStrings(text) {
  return [...text.matchAll(/"([^"]*)"/g)].map((m) => m[1]);
}

/**
 * Declared fields of an object/interface body.
 *
 * Line-based on purpose: neither file nests an object inside an event body (arrays
 * and inline zod calls only), so a declared field is always a line whose first
 * non-space token is `name:`. Indentation is not assumed to be a specific width —
 * the schema's envelope body sits at four spaces because it hangs off `.object(`
 * on its own line, while the `Envelope.extend({...})` bodies sit at two, and
 * pinning either width would make this guard fail for a formatting reason.
 *
 * If nesting is ever introduced this returns nested keys too — which the
 * "found seven events" assertions and the per-event comparisons turn into a loud
 * failure rather than a silent pass.
 */
function fieldsOf(body) {
  const fields = [];
  for (const line of body.split("\n")) {
    const m = /^ +([a-z_][a-z0-9_]*)(\??):(.*)$/.exec(line);
    if (!m) continue;
    const [, name, optionalMark, rest] = m;
    fields.push({
      name,
      optional:
        optionalMark === "?" || /\.optional\(\)/.test(rest) || /\.default\(/.test(rest),
    });
  }
  return fields;
}

function names(fields) {
  return fields.map((f) => f.name).sort();
}

// ---- extraction ---------------------------------------------------------------------

const sharedSrc = read(SHARED_FILE);
const mobileSrc = read(MOBILE_FILE);

/** event name -> { fields } for the shared zod schema. */
function sharedEvents() {
  const out = new Map();
  const re = /export const (\w+) = Envelope\.extend\(/g;
  let m;
  while ((m = re.exec(sharedSrc)) !== null) {
    const body = objectBodyAfter(sharedSrc, m[0]);
    if (!body) continue;
    const eventName = /event:\s*z\.literal\("([^"]+)"\)/.exec(body);
    if (!eventName) continue;
    out.set(eventName[1], { constName: m[1], fields: fieldsOf(body) });
  }
  return out;
}

/** event name -> { fields } for the mobile interfaces. */
function mobileEvents() {
  const out = new Map();
  const re = /export interface (\w+) extends Envelope/g;
  let m;
  while ((m = re.exec(mobileSrc)) !== null) {
    const body = objectBodyAfter(mobileSrc, m[0]);
    if (!body) continue;
    const eventName = /event:\s*"([^"]+)"/.exec(body);
    if (!eventName) continue;
    out.set(eventName[1], { constName: m[1], fields: fieldsOf(body) });
  }
  return out;
}

const SHARED_EVENTS = sharedEvents();
const MOBILE_EVENTS = mobileEvents();

// ---- guards -------------------------------------------------------------------------

test("both sides declare the frozen seven events", () => {
  assert.equal(
    SHARED_EVENTS.size,
    FROZEN_EVENT_COUNT,
    `the schema extractor found ${SHARED_EVENTS.size} events, not ${FROZEN_EVENT_COUNT}. ` +
      `If the extractor broke, fix the extractor — do not lower this number. Found: ` +
      [...SHARED_EVENTS.keys()].join(", "),
  );
  assert.equal(
    MOBILE_EVENTS.size,
    FROZEN_EVENT_COUNT,
    `the mirror extractor found ${MOBILE_EVENTS.size} events, not ${FROZEN_EVENT_COUNT}. ` +
      `Found: ${[...MOBILE_EVENTS.keys()].join(", ")}`,
  );
});

test("event names match the schema", () => {
  assert.deepEqual(
    [...MOBILE_EVENTS.keys()].sort(),
    [...SHARED_EVENTS.keys()].sort(),
    "the mirror and the schema disagree on which events exist",
  );
});

test("the mirror cannot send a field the schema does not know", () => {
  // The schema is `.strict()`: an unknown key means the event is rejected at the
  // server, so this direction fails a payload rather than a metric.
  const problems = [];
  for (const [event, shared] of SHARED_EVENTS) {
    const mobile = MOBILE_EVENTS.get(event);
    if (!mobile) continue;
    const known = new Set(names(shared.fields));
    for (const field of names(mobile.fields)) {
      if (!known.has(field)) problems.push(`${event}.${field}`);
    }
  }
  assert.deepEqual(
    problems,
    [],
    `fields emitted by the mirror but absent from the schema (the server would reject these): ${problems.join(", ")}`,
  );
});

test("every schema-required field is emitted", () => {
  // The other direction: a required field the emitter never sends is also a
  // rejected event, and it fails in production rather than in a test.
  const problems = [];
  for (const [event, shared] of SHARED_EVENTS) {
    const mobile = MOBILE_EVENTS.get(event);
    if (!mobile) continue;
    const emitted = new Set(names(mobile.fields));
    for (const field of shared.fields) {
      if (field.optional) continue;
      if (!emitted.has(field.name)) problems.push(`${event}.${field.name}`);
    }
  }
  assert.deepEqual(problems, [], `required schema fields the mirror never sends: ${problems.join(", ")}`);
});

test("the mirror does not soften a required field", () => {
  // `field: string` -> `field?: string` compiles on the mobile side and then sends
  // an event the schema rejects. Cheap to check, invisible otherwise.
  const problems = [];
  for (const [event, shared] of SHARED_EVENTS) {
    const mobile = MOBILE_EVENTS.get(event);
    if (!mobile) continue;
    const sharedOptional = new Map(shared.fields.map((f) => [f.name, f.optional]));
    for (const field of mobile.fields) {
      if (!field.optional) continue;
      if (sharedOptional.get(field.name) === false) problems.push(`${event}.${field.name}`);
    }
  }
  assert.deepEqual(
    problems,
    [],
    `fields the mirror made optional although the schema requires them: ${problems.join(", ")}`,
  );
});

test("the envelope matches, minus the two fields the mirror places per event", () => {
  // The schema writes this as `const Envelope = z\n  .object({ ... })`, so the marker
  // stops at `z` and `objectBodyAfter` finds the brace that follows.
  const sharedEnvelope = objectBodyAfter(sharedSrc, "const Envelope = z");
  assert.ok(sharedEnvelope, "shared envelope not found — the extractor or the source moved");
  const mobileEnvelope = objectBodyAfter(mobileSrc, "interface Envelope {");
  assert.ok(mobileEnvelope, "mobile envelope not found — the extractor or the source moved");

  // Why these two, and only these two:
  //   `event`    — the mirror puts the discriminant in each event interface, which
  //                is what gives each `track()` overload its literal type.
  //   `item_id`  — the mirror declares it per event, so that an event which must not
  //                carry one cannot.
  const EXCLUDED_FROM_MIRROR = ["event", "item_id"];

  const sharedFields = names(fieldsOf(sharedEnvelope));
  for (const excluded of EXCLUDED_FROM_MIRROR) {
    assert.ok(
      sharedFields.includes(excluded),
      `the schema envelope no longer declares ${excluded} — this exclusion list is stale`,
    );
  }
  const mobileFields = names(fieldsOf(mobileEnvelope));
  for (const excluded of EXCLUDED_FROM_MIRROR) {
    assert.ok(
      !mobileFields.includes(excluded),
      `the mirror envelope now declares ${excluded}; the exclusion above is stale`,
    );
  }

  assert.deepEqual(
    mobileFields,
    sharedFields.filter((f) => !EXCLUDED_FROM_MIRROR.includes(f)),
    "the envelope's shared fields drifted between the schema and the mirror",
  );
});

test("the shared vocabularies match", () => {
  const pairs = [
    { schemaConst: "MARKETPLACE_IDS", mirrorType: "MarketplaceId" },
    { schemaConst: "PAYWALL_VARIANTS", mirrorType: "PaywallVariant" },
    { schemaConst: "PAYWALL_TRIGGERS", mirrorType: "PaywallTrigger" },
  ];

  for (const { schemaConst, mirrorType } of pairs) {
    const start = sharedSrc.indexOf(`export const ${schemaConst} = [`);
    assert.notEqual(start, -1, `${schemaConst} not found — the extractor or the source moved`);
    const end = sharedSrc.indexOf("] as const", start);
    assert.notEqual(end, -1, `${schemaConst} has no \`] as const\` terminator`);
    const schemaValues = quotedStrings(sharedSrc.slice(start, end)).sort();

    const mirrorDecl = new RegExp(`export type ${mirrorType} =([^;]+);`).exec(mobileSrc);
    assert.ok(mirrorDecl, `${mirrorType} not found in the mirror — the extractor or the source moved`);
    const mirrorValues = quotedStrings(mirrorDecl[1]).sort();

    assert.ok(schemaValues.length > 0 && mirrorValues.length > 0, `${schemaConst} extractor found nothing`);
    assert.deepEqual(mirrorValues, schemaValues, `${mirrorType} does not match ${schemaConst}`);
  }
});
