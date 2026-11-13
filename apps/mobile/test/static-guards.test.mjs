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
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    // Missing or unreadable `src/`. Return nothing so `scannerFindsTheSourceTree`
    // reports it as a named failure, instead of the whole file dying with an ENOENT
    // stack trace that names no guard at all.
    return found;
  }
  for (const entry of entries) {
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

/** The full text of the line containing `index`. */
function lineAt(content, index) {
  const start = content.lastIndexOf("\n", index) + 1;
  const end = content.indexOf("\n", index);
  return content.slice(start, end === -1 ? content.length : end);
}

/**
 * Whether `index` sits inside a comment.
 *
 * This matters only for how a hit is *reported*, never for whether it counts: the
 * guards scan raw text on purpose (see `noRequireCalls`), so a comment can trip
 * them. Without a label, a maintainer reading `storage.ts:8  globalThis.require`
 * goes to line 8, finds prose, and the cheapest way to get CI green is to loosen
 * the guard — the exact outcome the guard exists to prevent. So a comment hit is
 * reported as `[comment]` with the wording that would keep everyone honest.
 */
function isInComment(content, index) {
  const lineStart = content.lastIndexOf("\n", index) + 1;
  const beforeOnLine = content.slice(lineStart, index);
  // A `//` earlier on the same line comments out everything after it.
  if (/\/\//.test(beforeOnLine)) return true;
  // Inside a `/* … */` block that has not been closed yet.
  const upToHere = content.slice(0, index);
  const opens = (upToHere.match(/\/\*/g) ?? []).length;
  const closes = (upToHere.match(/\*\//g) ?? []).length;
  if (opens > closes) return true;
  // Leading `*` is a JSDoc body line, `//` or `/*` starts a comment line.
  return /^\s*(\/\/|\*|\/\*)/.test(lineAt(content, index));
}

/**
 * Every match of `pattern` across the source tree, reported as `file:line`.
 *
 * A guard that matches nothing and a guard that never ran look identical, which
 * is why `scannerFindsTheSourceTree` below asserts the walk itself works before
 * the other guards are trusted.
 *
 * Hits inside comments are prefixed `[comment]` and carry `commentHint`, so the
 * failure text says "this is prose, reword it" instead of looking like offending
 * code. That label is a reporting detail — it does not exempt the hit.
 */
function offenders(pattern, commentHint) {
  const hits = [];
  for (const file of sourceFiles) {
    const content = readFileSync(file, "utf8");
    const global = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`);
    for (const match of content.matchAll(global)) {
      hits.push(formatHit({ file, content, index: match.index, matched: match[0], commentHint }));
    }
  }
  return hits;
}

/**
 * One line of failure text for a hit.
 *
 * Split out of `offenders` so it can be asserted against synthetic content: a
 * formatting test that only runs over the real tree would go vacuous the moment the
 * tree is clean, which it currently is.
 */
function formatHit({ file, content, index, matched, commentHint }) {
  const inComment = isInComment(content, index);
  const where = `${rel(file)}:${lineOf(content, index)}`;
  const label = inComment ? "[comment] " : "";
  const hint = inComment && commentHint ? `\n    ${commentHint}` : "";
  return `${where}  ${label}${matched.trim()}${hint}`;
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

/**
 * A file name that *suggests* a test double, matched as a substring.
 *
 * `fake`, `double` and `stub` are matched loosely on purpose — a production module
 * called `fakeApi` or `stubClient` would be a smell worth failing on. `mock` is
 * deliberately NOT in this list: `src/lib/mockData.ts` is production code (the
 * mock-mode dataset every screen runs on when `API_BASE` is unset) and is imported
 * by `DraftsProvider`. Matching `mock` loosely would make the guard red on a
 * healthy tree, and a guard that cries wolf gets switched off — which is worse than
 * no guard. `mock` therefore only trips the exact `.mock` suffix or a
 * test-double directory.
 */
const TEST_DOUBLE_HINT = /(fake|double|stub)/i;

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
  if (TEST_DOUBLE_HINT.test(name)) {
    return `imports a module whose name suggests a test double ("${name}")`;
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

/**
 * Shown next to a `[comment]` hit so the fix is "reword the prose", not "widen the
 * guard". This sentence is itself written to avoid the banned adjacency.
 */
const DYNAMIC_REQUIRE_COMMENT_HINT =
  'comment mentions the banned pattern; write it as "a dynamic require reached through globalThis"';

/** Discovered violations of the "dynamic require" guard, across the tree. */
function dynamicRequireHits() {
  return [
    ...offenders(DYNAMIC_REQUIRE_ACCESS, DYNAMIC_REQUIRE_COMMENT_HINT),
    ...offenders(DYNAMIC_REQUIRE_GLOBAL, DYNAMIC_REQUIRE_COMMENT_HINT),
  ];
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
  assert.equal(testDoubleReason("../../lib/mockData"), null, "src/lib/mockData.ts is production code");
  assert.match(testDoubleReason("../../test/helper") ?? "", /test-double directory/);
  assert.match(testDoubleReason("../__mocks__/api") ?? "", /test-double directory/);
  assert.match(testDoubleReason("./draft.double") ?? "", /test double/);
  // fake/double/stub ARE matched loosely — a production module named like a double
  // is a smell worth failing on. Only `mock` gets the narrow treatment above.
  assert.match(testDoubleReason("./fakeApi") ?? "", /suggests a test double/);
  assert.match(testDoubleReason("./stubClient") ?? "", /suggests a test double/);

  // The natural-language traps QA found in the real tree: none of the guards may
  // treat these as a hit. `mock://` is a URL scheme, `MOCK_*` a constant, and the
  // comments talk about mock mode.
  for (const benign of ["mock://onmodel/item-1/model", "MOCK_FREE_TIER_ITEM_LIMIT", "mock mode", "mockData"]) {
    assert.ok(!DYNAMIC_REQUIRE_ACCESS.test(benign));
    assert.ok(!DYNAMIC_REQUIRE_GLOBAL.test(benign));
    assert.ok(!/(?:^|[^\w.$])require\s*\(/m.test(benign));
    assert.ok(!/\bjest\s*\.\s*mock\s*\(/.test(benign));
  }
});

test("noFrameworkMockCalls", () => {
  // There is no test framework in this app (the runner is `node --test`), so a
  // `jest.mock`/`vi.mock` in `src/` can only be a leftover — and auto-hoisted mocks
  // are exactly what would make a wiring problem invisible to a test.
  const hits = [
    ...offenders(/\bjest\s*\.\s*mock\s*\(/m),
    ...offenders(/\bvi\s*\.\s*mock\s*\(/m),
  ];
  assert.deepEqual(hits, [], `framework mocking in app source:\n${hits.join("\n")}`);
});

test("noRequireCalls", () => {
  // Any CommonJS require in the app source is a resolution risk Metro cannot
  // police at build time. There are zero today; keep it that way.
  //
  // Raw-text scan, so comments and string literals can trip it. That is a decision,
  // not an oversight: telling comments apart from code needs a parser, and the
  // failure mode of a parser we get subtly wrong is a *missed* violation. Prose that
  // spells out the banned pattern also teaches it to the next reader — storage.ts's
  // own header was reworded for exactly that reason. The cost is that a comment hit
  // is reported as such (with a reword hint) instead of being silently exempted; a
  // string literal hit has to be reworded too, which no message in this app needs to
  // do today.
  const hits = offenders(
    /(?:^|[^\w.$])require\s*\(/m,
    'comment or string mentions the banned call; reword it rather than widening the guard',
  );
  assert.deepEqual(hits, [], `require() calls in app source:\n${hits.join("\n")}`);
});

test("commentHitsAreLabelledNotSilentlyExempt", () => {
  // The point of the label: someone who rewrites storage.ts's header to the accurate
  // `globalThis.require` must see "this is prose, reword it" — not a hit that reads
  // like offending code at line 8, whose cheapest fix is to loosen the guard.
  const comment = " * The old code reached through globalThis.require and Metro failed.\n";
  const commentIndex = comment.indexOf("globalThis");
  assert.equal(isInComment(comment, commentIndex), true, "a JSDoc line is a comment");

  const inline = "const x = 1; // never globalThis.require here\n";
  assert.equal(isInComment(inline, inline.indexOf("globalThis")), true, "// starts a comment mid-line");

  const block = "/*\n * globalThis.require\n */\n";
  assert.equal(isInComment(block, block.indexOf("globalThis")), true, "inside a block comment");

  const code = "const req = (globalThis as { require?: unknown }).require;\n";
  assert.equal(isInComment(code, code.indexOf("globalThis")), false, "real code is not a comment");

  // The formatted message is what a maintainer actually reads, so assert on it
  // directly rather than hoping the tree contains a comment hit today.
  const commentHit = formatHit({
    file: join(srcRoot, "offline", "storage.ts"),
    content: comment,
    index: commentIndex,
    matched: ".require",
    commentHint: DYNAMIC_REQUIRE_COMMENT_HINT,
  });
  assert.match(commentHit, /storage\.ts:1 {2}\[comment\] \.require/);
  assert.match(commentHit, /write it as/);

  const codeHit = formatHit({
    file: join(srcRoot, "offline", "storage.ts"),
    content: code,
    index: code.indexOf(".require"),
    matched: ".require",
    commentHint: DYNAMIC_REQUIRE_COMMENT_HINT,
  });
  assert.ok(!codeHit.includes("[comment]"), "code hits must not be labelled as comments");
  assert.ok(!codeHit.includes("write it as"), "code hits get no reword hint");
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
