/**
 * Static guards for the mobile source tree.
 *
 * These are not behaviour tests — they are the anti-regression fence for the two
 * failures that shipped this milestone, both of which ordinary unit tests could
 * not have caught:
 *
 *   1. E6-T5: `storage.ts` reached expo-file-system through a dynamic require.
 *      Metro could not resolve it, the throw was caught, and persistence
 *      silently degraded to a backend React Native does not have, so a
 *      force-quit lost the draft queue. A mock-based test would have passed,
 *      because a mock makes "is the module actually wired up" disappear.
 *   2. The same shape in prose: a docstring asserted `never rejects` while
 *      `JSON.stringify` threw synchronously at the call site.
 *
 * What is verifiable statically is verified here; what is not is covered
 * elsewhere — `expo export` is the only thing that proves the real module lands
 * in the Metro graph, and that stays a manual release gate (see
 * docs/HANDOFF-mobile.md).
 *
 * Run with the built-in runner, no test framework and no new dependencies:
 *   node --test test/*.test.mjs
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { basename, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

const testDir = fileURLToPath(new URL(".", import.meta.url));
const mobileRoot = join(testDir, "..");
const srcRoot = join(mobileRoot, "src");

/** Every .ts/.tsx under `src/`, depth-first, in a stable order. */
function listSourceFiles(dir) {
  const found = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      found.push(...listSourceFiles(full));
    } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name)) {
      found.push(full);
    }
  }
  return found.sort();
}

const sourceFiles = listSourceFiles(srcRoot);

/** Human-readable path for failure messages. */
function rel(file) {
  return relative(mobileRoot, file);
}

/** 1-based line number of `index` inside `content`. */
function lineOf(content, index) {
  return content.slice(0, index).split("\n").length;
}

/**
 * Every match of `pattern` across the source tree, reported as `file:line`.
 *
 * A guard that matches nothing and a guard that never ran look identical, which
 * is why `scannerFindsTheSourceTree` below asserts the walk itself works before
 * the other guards are trusted.
 */
function offenders(pattern) {
  const hits = [];
  for (const file of sourceFiles) {
    const content = readFileSync(file, "utf8");
    const global = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`);
    for (const match of content.matchAll(global)) {
      hits.push(`${rel(file)}:${lineOf(content, match.index)}  ${match[0]}`);
    }
  }
  return hits;
}

/**
 * Import specifiers of a file: `from "x"`, `import("x")` and `require("x")`.
 * Bare `import "x"` side-effect imports are included too.
 */
function importSpecifiers(content) {
  const specifiers = [];
  const patterns = [
    /\bfrom\s+["']([^"']+)["']/g,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
    /\brequire\s*\(\s*["']([^"']+)["']\s*\)/g,
    /^\s*import\s+["']([^"']+)["']/gm,
  ];
  for (const pattern of patterns) {
    for (const match of content.matchAll(pattern)) {
      specifiers.push({ specifier: match[1], index: match.index });
    }
  }
  return specifiers;
}

/**
 * Directory names that only ever hold test doubles. A production module must not
 * import from any of them — that is how a fake ends up in the shipped path.
 */
const TEST_DOUBLE_SEGMENTS = new Set([
  "test",
  "tests",
  "__tests__",
  "__mocks__",
  "__fakes__",
  "__fixtures__",
  "__stubs__",
  "fixtures",
  "doubles",
  "fakes",
  "stubs",
]);

/**
 * File-name suffixes that mark a test double (`x.double.ts`, `x.stub.ts`, …).
 *
 * Note what is deliberately NOT matched: `src/lib/mockData.ts`. It is production
 * code — the mock-mode data every screen runs on when `API_BASE` is unset — so a
 * rule like "no path containing `mock`" would fail on a healthy tree. Only an
 * exact `.mock`/`.double`/`.fake`/`.stub` suffix counts.
 */
const TEST_DOUBLE_BASENAME = /\.(mock|mocks|double|doubles|fake|fakes|stub|stubs)$/;

/** Reason a specifier is a test double, or null if it is fine. */
function testDoubleReason(specifier) {
  const withoutExtension = specifier.replace(/\.(ts|tsx|js|mjs|cjs)$/, "");
  const segments = withoutExtension.split("/").filter((segment) => segment !== "" && segment !== ".");
  const offendingSegment = segments.find((segment) => TEST_DOUBLE_SEGMENTS.has(segment));
  if (offendingSegment) {
    return `imports from a test-double directory ("${offendingSegment}/")`;
  }
  const name = basename(withoutExtension);
  if (TEST_DOUBLE_BASENAME.test(name)) {
    return `imports a test double ("${name}")`;
  }
  return null;
}

test("scannerFindsTheSourceTree", () => {
  // Without this, every guard below would pass on an empty file list and look
  // green while checking nothing.
  assert.ok(sourceFiles.length > 0, `no .ts/.tsx files found under ${rel(srcRoot)}`);
  assert.ok(
    sourceFiles.some((file) => rel(file) === join("src", "offline", "storage.ts")),
    "src/offline/storage.ts is not in the scanned set",
  );
});

/**
 * Property access named `require`: `x.require`, `x.require(...)`, `x.require?.(...)`.
 *
 * This is deliberately broader than matching `globalThis.require` adjacently. The
 * code that actually shipped the E6-T5 bug read:
 *
 *   const req = (globalThis as { require?: (id: string) => unknown }).require;
 *
 * A type assertion sits between `globalThis` and `.require`, so an adjacency-only
 * pattern would have found nothing and the guard would have been decorative. The
 * `historicalShapesAreCaught` test below pins that exact string as a fixture.
 *
 * `\b` after `require` keeps `obj.requireConsent` out of the net.
 */
const DYNAMIC_REQUIRE_ACCESS = /\.\s*require\b/;
const DYNAMIC_REQUIRE_GLOBAL = /globalThis\s*\[\s*["']require["']\s*\]/;

/** Discovered violations of the "dynamic require" guard, across the tree. */
function dynamicRequireHits() {
  const hits = [];
  for (const file of sourceFiles) {
    const content = readFileSync(file, "utf8");
    for (const pattern of [DYNAMIC_REQUIRE_ACCESS, DYNAMIC_REQUIRE_GLOBAL]) {
      const global = new RegExp(pattern.source, "g");
      for (const match of content.matchAll(global)) {
        hits.push(`${rel(file)}:${lineOf(content, match.index)}  ${match[0].trim()}`);
      }
    }
  }
  return hits;
}

test("noDynamicRequireThroughGlobalThis", () => {
  // The E6-T5 regression: a require reached through `globalThis` is invisible to
  // Metro, throws at runtime, and the catch that follows turns a hard failure
  // into silent degradation. The original shape is pinned in
  // `historicalShapesAreCaught` below, so this guard cannot quietly narrow.
  const hits = dynamicRequireHits();
  assert.deepEqual(hits, [], `dynamic require in app source:\n${hits.join("\n")}`);
});

test("historicalShapesAreCaught", () => {
  // A guard that has never failed is indistinguishable from one that cannot fail.
  // These fixtures are the exact shapes that shipped real defects, asserted
  // against the guard patterns themselves — narrow a pattern and this goes red.
  assert.ok(
    DYNAMIC_REQUIRE_ACCESS.test("const req = (globalThis as { require?: (id: string) => unknown }).require;"),
    "the guard no longer catches the E6-T5 shape (cast between globalThis and .require)",
  );
  assert.ok(
    DYNAMIC_REQUIRE_ACCESS.test('const mod = require_this.require("expo-file-system");'),
    "the guard no longer catches a plain .require() call",
  );
  assert.ok(
    DYNAMIC_REQUIRE_GLOBAL.test('const req = globalThis["require"];'),
    "the guard no longer catches bracket access to require",
  );
  assert.ok(
    !DYNAMIC_REQUIRE_ACCESS.test("const ok = flags.requireConsent;"),
    "the guard over-matches: .requireConsent is a normal property",
  );
});

test("deliberateExemptions", () => {
  // `src/lib/mockData.ts` is production code (the mock-mode dataset every screen
  // runs on when API_BASE is unset). A "no path containing mock" rule would fail
  // on a healthy tree, so the exemption is asserted rather than assumed.
  assert.equal(testDoubleReason("../lib/mockData"), null, "src/lib/mockData.ts is production code");
  assert.match(testDoubleReason("../../test/helper") ?? "", /test-double directory/);
  assert.match(testDoubleReason("../__mocks__/api") ?? "", /test-double directory/);
  assert.match(testDoubleReason("./draft.double") ?? "", /test double/);
});

test("noRequireCalls", () => {
  // Any CommonJS require in the app source is a resolution risk Metro cannot
  // police at build time. There are zero today; keep it that way.
  //
  // This scans raw text, comments included, and that is deliberate: a comment
  // that spells out `require(` teaches the next reader the banned pattern. This
  // team already rewrote storage.ts's header for exactly that reason, so keeping
  // the scan strict is consistent rather than incidental.
  const hits = offenders(/(?:^|[^\w.$])require\s*\(/m);
  assert.deepEqual(hits, [], `require() calls in app source:\n${hits.join("\n")}`);
});

test("noImportsFromTestDoubles", () => {
  const hits = [];
  for (const file of sourceFiles) {
    const content = readFileSync(file, "utf8");
    for (const { specifier, index } of importSpecifiers(content)) {
      const reason = testDoubleReason(specifier);
      if (reason) {
        hits.push(`${rel(file)}:${lineOf(content, index)}  ${reason}: "${specifier}"`);
      }
    }
  }
  assert.deepEqual(hits, [], `production source importing a test double:\n${hits.join("\n")}`);
});

/** A static ESM import of expo-file-system, which is what Metro can follow. */
const STATIC_EXPO_FILE_SYSTEM_IMPORT = /\bfrom\s+["']expo-file-system["']/;

test("storageImportsExpoFileSystemStatically", () => {
  // The fix for E6-T5, pinned at the source level. This does NOT prove the module
  // reaches the Metro graph (only `expo export` can), but it does fail the moment
  // someone turns the static import back into a runtime lookup.
  //
  // Fixtures first: a pattern that matches everything would pass on any file, so
  // assert it discriminates before trusting the assertion below.
  assert.ok(STATIC_EXPO_FILE_SYSTEM_IMPORT.test('import * as FileSystem from "expo-file-system";'));
  assert.ok(!STATIC_EXPO_FILE_SYSTEM_IMPORT.test('const moduleName = "expo-file-system";'));

  const storagePath = join(srcRoot, "offline", "storage.ts");
  const content = readFileSync(storagePath, "utf8");
  assert.match(
    content,
    STATIC_EXPO_FILE_SYSTEM_IMPORT,
    `${rel(storagePath)} must import expo-file-system statically`,
  );
});
