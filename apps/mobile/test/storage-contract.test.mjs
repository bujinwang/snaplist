/**
 * Contract tests for `src/offline/storage.ts` — the draft queue's persistence layer.
 *
 * These run the REAL compiled module against a controllable `expo-file-system` double. This
 * file was migrated from an out-of-repo harness (`WorkBuddy/…/qa-harness/e6t5-storage/`), whose
 * README is the authority on why each case exists; what follows is the part a reader needs in
 * order not to have to go there.
 *
 * WHY A MODULE DOUBLE AND NOT AN INJECTED BACKEND
 * The defect this layer was written to catch was a module-resolution failure: the old code did
 * `globalThis.require("expo-file-system")`, Metro could not resolve it, the throw was swallowed,
 * and persistence silently degraded to a backend React Native does not have. An injection port
 * would remove "is that module actually resolvable" from the test — the exact thing that broke.
 * So the module is imported the way the app imports it, and only the module underneath is
 * replaced, by a package sitting in a scratch `node_modules` (see HOW IT RUNS).
 * `static-guards.test.mjs` guards the other half: that the import in `src/` stays static.
 *
 * WHAT IS NOT HERE, ON PURPOSE
 * The out-of-repo harness also runs an A/B: the same cases against a pre-fix build, with
 * `EXPECT=buggy`, to prove each defect still reproduces. That needs a compiled copy of the
 * pre-fix module checked into the repo, which would be one more file that looks authoritative
 * and drifts. The defect-reproduction proof stays there; this file pins today's contract only.
 *
 * HOW IT RUNS
 *   1. compile `src/offline/storage.ts` with the repo's own `typescript`, using the same flags
 *      the harness used to build its anchored artefacts;
 *   2. write the double into `<scratch>/node_modules/expo-file-system/`, so ordinary Node
 *      resolution from `<scratch>/offline/storage.js` finds it — no NODE_PATH, no loader hooks,
 *      no `Module._resolveFilename` patching;
 *   3. require the compiled module per test with a cleared cache entry, because `storage.ts`
 *      holds a module-level `saveQueue` and a shared queue between tests would be a lie.
 *
 * TIMEOUTS ARE PART OF THE CONTRACT
 * A case that abandons an await (e.g. awaiting a save a serialized build has queued behind a
 * stalled one) drains the event loop: under a bare node process that exits 0 with no output,
 * which reads as green. Every test here carries an explicit timeout so that failure mode
 * becomes a named red instead of a hung CI job.
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { after, test } from "node:test";
import { fileURLToPath } from "node:url";

const mobileRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const nodeRequire = createRequire(import.meta.url);

/**
 * Controllable `expo-file-system` 17 double (legacy promise API): only what `storage.ts` calls,
 * plus the switches the interesting cases need.
 *
 * `chunkedMode` is not decoration. On a real filesystem a write is not one indivisible instant,
 * it lands in pieces; two concurrent writes to the same path therefore interleave and leave a
 * mixed payload. A double that wrote atomically could never expose that, and the D1b case would
 * pass while testing nothing.
 *
 * `throwOnDocumentDirectory` models the first thing that can go wrong on a build where the
 * native module is not linked: reading the property itself throws, before any await, so a
 * caller that reads it outside a guard cannot fall back.
 */
const DOUBLE_SOURCE = `// Written by apps/mobile/test/storage-contract.test.mjs into a scratch node_modules.
const state = {
  documentDirectory: "file:///documents/",
  files: new Map(),
  calls: [],
  failWrite: false,
  failMove: false,
  failGetInfo: false,
  failRead: false,
  throwOnDocumentDirectory: false,
  writeGate: null,
  chunkedMode: false,
  chunkDelayMs: 5,
  activeWrites: 0,
  maxConcurrentWrites: 0,
};

async function getInfoAsync(path) {
  state.calls.push(["getInfoAsync", path]);
  if (state.failGetInfo) throw new Error("getInfoAsync: forced failure");
  return { exists: state.files.has(path), uri: path, isDirectory: false };
}

async function readAsStringAsync(path) {
  state.calls.push(["readAsStringAsync", path]);
  if (state.failRead) throw new Error("readAsStringAsync: forced failure");
  if (!state.files.has(path)) throw new Error("readAsStringAsync: ENOENT " + path);
  return state.files.get(path);
}

async function writeAsStringAsync(path, content) {
  state.calls.push(["writeAsStringAsync", path, content]);
  state.activeWrites += 1;
  state.maxConcurrentWrites = Math.max(state.maxConcurrentWrites, state.activeWrites);
  try {
    if (state.failWrite) throw new Error("writeAsStringAsync: forced failure");
    if (state.writeGate && state.writeGate.match(content)) {
      await state.writeGate.promise;
    }
    if (!state.chunkedMode) {
      state.files.set(path, content);
      return;
    }
    const mid = Math.ceil(content.length / 2);
    const chunks = [content.slice(0, mid), content.slice(mid)];
    state.files.set(path, "");
    for (const chunk of chunks) {
      await new Promise((r) => setTimeout(r, state.chunkDelayMs));
      state.files.set(path, (state.files.get(path) || "") + chunk);
    }
  } finally {
    state.activeWrites -= 1;
  }
}

async function moveAsync(opts) {
  state.calls.push(["moveAsync", opts.from, opts.to]);
  if (state.failMove) throw new Error("moveAsync: forced failure");
  if (!state.files.has(opts.from)) throw new Error("moveAsync: ENOENT " + opts.from);
  state.files.set(opts.to, state.files.get(opts.from));
  state.files.delete(opts.from);
}

module.exports = {
  getInfoAsync,
  readAsStringAsync,
  writeAsStringAsync,
  moveAsync,
  get documentDirectory() {
    if (state.throwOnDocumentDirectory) {
      throw new Error("documentDirectory: native module unavailable (forced)");
    }
    return state.documentDirectory;
  },
  __state: state,
};
`;

const TS_FLAGS = [
  "--module",
  "commonjs",
  "--target",
  "es2020",
  "--skipLibCheck",
  "--esModuleInterop",
  "--moduleResolution",
  "node",
];

const scratch = mkdtempSync(join(tmpdir(), "snaplist-storage-contract-"));
const compiledStoragePath = join(scratch, "offline", "storage.js");
const doubleDir = join(scratch, "node_modules", "expo-file-system");

execFileSync(process.execPath, [nodeRequire.resolve("typescript/bin/tsc"), join("src", "offline", "storage.ts"), "--outDir", scratch, ...TS_FLAGS], {
  cwd: mobileRoot,
  stdio: "pipe",
});
mkdirSync(doubleDir, { recursive: true });
writeFileSync(
  join(doubleDir, "package.json"),
  `${JSON.stringify({ name: "expo-file-system", version: "17.0.1", main: "index.js" }, null, 2)}\n`,
);
writeFileSync(join(doubleDir, "index.js"), DOUBLE_SOURCE);

const fileSystem = nodeRequire(join(doubleDir, "index.js"));
const state = fileSystem.__state;

after(() => {
  rmSync(scratch, { recursive: true, force: true });
});

const PATH = "file:///documents/snaplist.drafts.v1.json";
const TMP = `${PATH}.tmp`;
const KEY = "snaplist.drafts.v1";

const item = (id) => ({ id, status: "queued" });
const tick = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class FakeStorage {
  constructor(seed) {
    this.map = new Map(Object.entries(seed ?? {}));
  }
  getItem(key) {
    return this.map.has(key) ? this.map.get(key) : null;
  }
  setItem(key, value) {
    this.map.set(key, String(value));
  }
  removeItem(key) {
    this.map.delete(key);
  }
}

function installLocalStorage(seed) {
  const storage = new FakeStorage(seed);
  Object.defineProperty(globalThis, "localStorage", {
    value: storage,
    writable: true,
    configurable: true,
  });
  return storage;
}

function removeLocalStorage() {
  Object.defineProperty(globalThis, "localStorage", {
    value: undefined,
    writable: true,
    configurable: true,
  });
}

function resetDouble() {
  state.files.clear();
  state.calls.length = 0;
  state.failWrite = false;
  state.failMove = false;
  state.failGetInfo = false;
  state.failRead = false;
  state.writeGate = null;
  state.throwOnDocumentDirectory = false;
  state.chunkedMode = false;
  state.activeWrites = 0;
  state.maxConcurrentWrites = 0;
  state.documentDirectory = "file:///documents/";
}

/**
 * A freshly required module per test: `storage.ts` keeps `saveQueue` at module scope, so reusing
 * an instance would let one case's stalled save leak into the next.
 */
function freshStorage() {
  resetDouble();
  delete nodeRequire.cache[nodeRequire.resolve(compiledStoragePath)];
  return nodeRequire(compiledStoragePath);
}

function tryParse(text) {
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (error) {
    return { ok: false, error: String(error && error.message) };
  }
}

const TIMEOUT = 5000;

// ---------------------------------------------------------------------------------------------
// Round trips and the happy path
// ---------------------------------------------------------------------------------------------

test("empty_array_roundtrip", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  const ls = installLocalStorage();
  await storage.saveDraftItems([]);
  assert.equal(state.files.get(PATH), "[]", "expected the literal [] on disk");
  assert.deepEqual(await storage.loadDraftItems(), []);
  // The short-circuit matters: a successful file write must not also write localStorage.
  assert.equal(ls.map.size, 0, "localStorage must not be used when the file system works");
});

test("normal_roundtrip", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  const ls = installLocalStorage();
  const items = [item("a"), item("b")];
  await storage.saveDraftItems(items);

  assert.deepEqual(JSON.parse(state.files.get(PATH)), items);
  assert.equal(state.files.has(TMP), false, ".tmp must be gone after a successful move");

  const writeIndex = state.calls.findIndex((call) => call[0] === "writeAsStringAsync");
  const moveIndex = state.calls.findIndex((call) => call[0] === "moveAsync");
  assert.ok(writeIndex >= 0 && moveIndex > writeIndex, "the write must precede the move");
  assert.deepEqual(state.calls[moveIndex], ["moveAsync", TMP, PATH], "the move must be tmp -> target");

  assert.deepEqual(await storage.loadDraftItems(), items);
  assert.equal(ls.map.size, 0, "no localStorage use on the file-system path");
});

// ---------------------------------------------------------------------------------------------
// The fallback chain — structural, not a product promise. Read the notes before weakening these.
// ---------------------------------------------------------------------------------------------

test("write_fails_falls_back_to_localstorage", { timeout: TIMEOUT }, async () => {
  // NOT PRODUCTION-REACHABLE: this needs a filesystem AND localStorage at once, and no shipped
  // platform has both (native has no localStorage; web has documentDirectory === null).
  // What it pins is the STRUCTURE of the chain: a failed write must not throw and must not stop
  // the next backend from being attempted. It must never be read as "a failed write is recovered
  // by localStorage" — that claim was deleted from the docstring, and a green test implying it
  // would quietly put it back.
  const storage = freshStorage();
  const ls = installLocalStorage();
  state.failWrite = true;
  const items = [item("c")];

  await storage.saveDraftItems(items);

  assert.equal(state.files.has(PATH), false, "nothing should be at the target when the write failed");
  assert.equal(ls.getItem(KEY), JSON.stringify(items), "the chain must reach localStorage");
  assert.deepEqual(await storage.loadDraftItems(), items);
});

test("move_fails_reaches_localstorage", { timeout: TIMEOUT }, async () => {
  // Also NOT PRODUCTION-REACHABLE for the same reason. The structural point: the catch spans the
  // MOVE, not only the write, so a failed move cannot escape.
  const storage = freshStorage();
  const ls = installLocalStorage();
  state.failMove = true;
  const items = [item("d")];

  await storage.saveDraftItems(items);

  assert.equal(ls.getItem(KEY), JSON.stringify(items), "a move failure must still reach localStorage");
  assert.equal(state.files.has(PATH), false, "the target must not exist");
  assert.equal(state.files.has(TMP), true, "the orphaned .tmp is expected to be left behind");
});

test("move_fails_then_stale_file_wins", { timeout: TIMEOUT }, async () => {
  // ACCEPTED DIVERGENCE (D2), not a defect. The team decided to correct the docstring and leave
  // the behaviour alone, so this asserts the accepted outcome. It needs both backends at once and
  // is therefore not reachable in production either.
  const storage = freshStorage();
  const ls = installLocalStorage();
  state.files.set(PATH, JSON.stringify([item("old")]));
  state.failMove = true;

  await storage.saveDraftItems([item("new")]);

  assert.equal(ls.getItem(KEY), JSON.stringify([item("new")]), "the new payload lands in localStorage");
  assert.deepEqual(await storage.loadDraftItems(), [item("old")], "accepted: the stale file still wins");
});

test("web_documentdirectory_null", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  state.documentDirectory = null; // what the expo-file-system web shim reports
  const ls = installLocalStorage();
  const items = [item("e")];

  await storage.saveDraftItems(items);

  assert.equal(state.calls.length, 0, `no file-system call at all on web, got ${JSON.stringify(state.calls)}`);
  assert.equal(ls.getItem(KEY), JSON.stringify(items));
  assert.deepEqual(await storage.loadDraftItems(), items);
});

test("corrupt_file_falls_through", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  installLocalStorage({ [KEY]: JSON.stringify([item("from-ls")]) });
  state.files.set(PATH, "{not json");

  assert.deepEqual(await storage.loadDraftItems(), [item("from-ls")], "corrupt file must fall through");
});

test("corrupt_everything_returns_empty", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  installLocalStorage({ [KEY]: "]]]" });
  state.files.set(PATH, "[[[");

  assert.deepEqual(await storage.loadDraftItems(), [], "nothing readable anywhere means an empty queue, not a throw");
});

test("fresh_install_returns_empty", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  installLocalStorage();

  assert.deepEqual(await storage.loadDraftItems(), []);
});

test("no_backend_at_all_does_not_throw", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  state.documentDirectory = null;
  removeLocalStorage();

  await storage.saveDraftItems([item("f")]);

  assert.deepEqual(await storage.loadDraftItems(), [], "no backend means nothing persisted, but no throw either");
});

test("non_array_payload_rejected", { timeout: TIMEOUT }, async () => {
  const storage = freshStorage();
  installLocalStorage({ [KEY]: JSON.stringify([item("ls-wins")]) });
  state.files.set(PATH, JSON.stringify({ drafts: [item("x")] }));

  assert.deepEqual(await storage.loadDraftItems(), [item("ls-wins")], "an object is not a draft array");
});

// ---------------------------------------------------------------------------------------------
// The `.tmp` boundary: a leftover is recovered only when there is no document at the target
// ---------------------------------------------------------------------------------------------

test("invalid_tmp_is_never_promoted", { timeout: TIMEOUT }, async () => {
  // Standing invariant, true before and after read-side promotion. Two cases, each aimed at one
  // half of the validation:
  //   (a) parseable but not a draft array -> catches a dropped `isDraftArray`;
  //   (b) the real D1b fragment, unparseable -> catches a dropped `JSON.parse` guard.
  // One fails silently (a value comes back), the other loudly (a SyntaxError escapes), so BOTH
  // halves are asserted: the return value and that nothing throws.
  const storage = freshStorage();
  const cases = [
    { label: "parseable-non-array", payload: JSON.stringify({ drafts: [item("x")] }) },
    { label: "unparseable-fragment", payload: 'atus":"queued"}]' },
  ];

  for (const testCase of cases) {
    resetDouble();
    removeLocalStorage();
    state.files.set(TMP, testCase.payload); // target deliberately absent, so promotion is attempted

    let loaded;
    try {
      loaded = await storage.loadDraftItems();
    } catch (error) {
      assert.fail(
        `loadDraftItems must not throw on an invalid tmp (${testCase.label}): threw ${error && error.name}: ${error && error.message}`,
      );
    }
    assert.deepEqual(loaded, [], `an invalid tmp must not be promoted (${testCase.label})`);
  }
});

test("stale_tmp_is_ignored", { timeout: TIMEOUT }, async () => {
  // A leftover must not SHADOW a document that exists. Promotion applies only when nothing is at
  // the target, so a present target stays authoritative and a read neither consumes nor promotes.
  const storage = freshStorage();
  installLocalStorage();
  state.files.set(PATH, JSON.stringify([item("authoritative")]));
  state.files.set(TMP, JSON.stringify([item("ghost")]));

  assert.deepEqual(await storage.loadDraftItems(), [item("authoritative")]);
  assert.equal(state.files.has(TMP), true, "reading must not consume or promote the tmp");

  await storage.saveDraftItems([item("fresh")]);
  assert.deepEqual(JSON.parse(state.files.get(PATH)), [item("fresh")]);
  assert.equal(state.files.has(TMP), false, "only the next successful write consumes it");
});

// ---------------------------------------------------------------------------------------------
// Concurrency
// ---------------------------------------------------------------------------------------------

test("concurrent_saves_can_invert_order", { timeout: TIMEOUT }, async () => {
  // Two fire-and-forget saves share one `.tmp` path, so without serialization the last write to
  // COMPLETE wins rather than the last call.
  //
  // The second save is raced against a timeout instead of awaited. Awaiting it deadlocks on a
  // serialized build, where it is legitimately queued behind the stalled save; a bare fixed delay
  // would instead make the case depend on the save finishing inside an arbitrary window. The race
  // satisfies both without either hazard.
  const storage = freshStorage();
  installLocalStorage();
  const older = [item("older")];
  const newer = [item("newer")];

  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  state.writeGate = { match: (content) => content === JSON.stringify(older), promise: gate };

  const olderSave = storage.saveDraftItems(older);
  await tick(10); // let the older save enter its write and stall on the gate

  state.writeGate = null;
  const newerSave = storage.saveDraftItems(newer);
  await Promise.race([newerSave, tick(100)]);
  const duringWindow = state.files.get(PATH);

  release();
  await Promise.allSettled([olderSave, newerSave]);

  const finalTarget = state.files.get(PATH);
  const finalLoaded = await storage.loadDraftItems();
  console.log(
    JSON.stringify(
      {
        diskWhileOlderStillStalled: duringWindow,
        diskAfterOlderSaveFinishes: finalTarget,
        loadedNow: JSON.stringify(finalLoaded),
        maxConcurrentWritesObserved: state.maxConcurrentWrites,
      },
      null,
      2,
    ),
  );

  assert.equal(finalTarget, JSON.stringify(newer), `the durable snapshot must be the newest, got ${finalTarget}`);
  assert.deepEqual(finalLoaded, newer, "load must return the newest snapshot");
});

test("d1b_concurrent_writes_share_tmp_and_hybridize", { timeout: TIMEOUT }, async () => {
  // Two saves suspended in the WRITE stage at once. With a shared, non-unique `.tmp` path the two
  // writes interleave and leave a MIXED payload — worse than an inversion, because neither
  // snapshot is intact (invalid JSON, so load returns [] and, with no localStorage on native,
  // everything is gone).
  const storage = freshStorage();
  installLocalStorage();
  const a = [item("AAAA")];
  const b = [item("BBBB")];
  state.chunkedMode = true; // one write lands in pieces, as on a real filesystem

  await Promise.allSettled([storage.saveDraftItems(a), storage.saveDraftItems(b)]);

  const disk = state.files.get(PATH);
  const parsed = disk === undefined ? { ok: false, error: "no target written" } : tryParse(disk);
  const loadReturns = await storage.loadDraftItems();
  console.log(
    JSON.stringify(
      {
        maxConcurrentWritesObserved: state.maxConcurrentWrites,
        diskContent: disk,
        diskParsesAsJSON: parsed.ok,
        loadReturns: JSON.stringify(loadReturns),
        interleavingEngaged: state.maxConcurrentWrites >= 2,
      },
      null,
      2,
    ),
  );

  // If the double never observed a write, this case proved nothing.
  assert.ok(state.maxConcurrentWrites >= 1, "the double never observed a write; instrumentation is broken");
  assert.ok([JSON.stringify(a), JSON.stringify(b)].includes(disk), `the durable payload must be one complete snapshot, got ${disk}`);
  assert.equal(parsed.ok, true, `the durable payload must be valid JSON, got ${disk}`);
  assert.ok(
    [JSON.stringify(a), JSON.stringify(b)].includes(JSON.stringify(loadReturns)),
    `load must return a complete snapshot, got ${JSON.stringify(loadReturns)}`,
  );
});

// ---------------------------------------------------------------------------------------------
// Targeted proofs kept out of the matrix names in the harness; here they are ordinary tests
// ---------------------------------------------------------------------------------------------

test("getter_throws_is_handled", { timeout: TIMEOUT }, async () => {
  // Reading `documentDirectory` throws — the first thing that goes wrong on web or an unlinked
  // native module, before any await. Whether the module survives depends on `getDraftFilePath()`
  // sitting INSIDE a try. This runs on the launch path, so a rejection here blocks startup.
  const storage = freshStorage();
  const ls = installLocalStorage();
  state.throwOnDocumentDirectory = true;

  let loadOutcome;
  try {
    loadOutcome = `resolved:${JSON.stringify(await storage.loadDraftItems())}`;
  } catch (error) {
    loadOutcome = `REJECTED(${error && error.message})`;
  }

  let saveOutcome;
  try {
    await storage.saveDraftItems([item("g")]);
    saveOutcome = "resolved";
  } catch (error) {
    saveOutcome = `REJECTED(${error && error.message})`;
  }

  assert.equal(loadOutcome, "resolved:[]", "load must fall back rather than reject");
  assert.equal(saveOutcome, "resolved", "save must fall back rather than reject");
  assert.equal(ls.getItem(KEY), JSON.stringify([item("g")]), "save must fall back to localStorage");
});

test("snapshot_survives_failed_move", { timeout: TIMEOUT }, async () => {
  // `moveAsync` on iOS deletes the target and then renames (verified in expo-file-system's
  // ios/FileSystemModule.swift), so dying between the two leaves no target and an un-renamed tmp.
  // With no localStorage — a real device — the tmp is the only copy of the snapshot.
  //
  // Deliberately CONVENTION-INDEPENDENT: it never names the tmp file. It drives one into
  // existence with a real failed save, so a change to the naming scheme (task #8) still exercises
  // this, and a lookup that stops matching the writer shows up as a lost snapshot rather than a
  // silent [].
  const storage = freshStorage();
  removeLocalStorage();
  const items = [item("survivor")];
  state.failMove = true; // the write lands, the move dies after the target was removed

  await storage.saveDraftItems(items);
  state.failMove = false;

  const filesLeftBehind = [...state.files.keys()];
  const loaded = await storage.loadDraftItems();

  assert.deepEqual(loaded, items, "a failed move must not lose the snapshot: the tmp holds the only copy");
  assert.ok(filesLeftBehind.length > 0, "recovery is memory-only and must not consume the tmp");
});
