/**
 * Local persistence for the draft queue (E6-T5).
 *
 * A captured photo must NEVER be lost (E6-T5 DoD), so the queue is written to
 * durable on-device storage on every change. `expo-file-system` is a real
 * dependency of this app (17.0.1 — the version Expo 51 already resolves) and is
 * imported statically so Metro can resolve it at build time. The previous
 * attempt used a dynamic `require`, reached through `globalThis`, which Metro
 * could not resolve, threw at runtime and was swallowed by a `catch` — that
 * silently degraded persistence to a backend React Native does not have, so the
 * queue looked fine for a session and vanished on force-quit.
 *
 * Backends, in this order:
 *   1. `expo-file-system` — one versioned JSON document in `documentDirectory`,
 *      which is app-private and survives process exit / force-quit. This is the
 *      backend that runs on a device.
 *   2. `localStorage` — web / react-native-web, where the expo-file-system shim
 *      reports `documentDirectory === null`.
 *   3. Nothing — on a platform with neither (a bare Node process), the queue
 *      still works but only for that session. It is deliberately NOT backed by a
 *      module-level cache: a silent pseudo-persistence would hide a broken
 *      backend during manual QA, which is the failure mode this module fixes.
 *
 * Writes are staged: the payload goes to `<file>.tmp` first and is then moved onto
 * the target with `moveAsync`, so a reader never observes a half-written document.
 * What a crash costs depends on how the platform implements that move, and it is
 * **not the same on both**:
 *
 *   - Android implements the move as `renameTo`, a real atomic replace. A crash at
 *     worst loses the newest snapshot; the previous document stays intact.
 *   - iOS deletes the target and then renames the temporary file onto it, as two
 *     steps. A crash between them leaves the target deleted and the new snapshot
 *     still sitting in `<file>.tmp` — so neither the old nor the new document is at
 *     the path a reader looks at. The read path does not promote that leftover yet
 *     (a known gap, tracked as an E6-T5 follow-up), so in that window the queue can
 *     read back empty.
 *
 * Staged, then — not atomic on both platforms, and on iOS the window can cost more
 * than the newest snapshot.
 *
 * Saves are serialized onto a module-level promise chain, so concurrent callers
 * cannot interleave inside the shared `.tmp` path and the last call's payload is
 * the last one on disk.
 *
 * The two backends are mutually exclusive, not complementary: a native build has
 * the file system and no `localStorage`, web has `localStorage` and no
 * `documentDirectory`. So a failed file-system write is not "recovered" by
 * `localStorage` — the previous document simply stays on disk and the next launch
 * reads that older snapshot. The staged write is what keeps it readable.
 *
 * Why a single JSON document and not SQLite: the queue is one ordered array that
 * is read once on mount and rewritten whole on every mutation, so a schema, a
 * migration path and a second native module buy nothing measurable here —
 * `expo-sqlite` is deliberately NOT a dependency. Everything storage-specific
 * stays behind `loadDraftItems` / `saveDraftItems`, so a table-based store can be
 * swapped in later without touching `DraftsProvider`.
 *
 * The API used below is the legacy promise API (`documentDirectory`,
 * `getInfoAsync`, `readAsStringAsync`, `writeAsStringAsync`, `moveAsync`) — that
 * is what expo-file-system 17 / SDK 51 exposes. The `File` / `Directory` classes
 * are SDK 52+ and must not be used here.
 */

import * as FileSystem from "expo-file-system";

import { DraftItem } from "../lib/types";

const STORAGE_KEY = "snaplist.drafts.v1";

/**
 * Tail of the save chain. Every `saveDraftItems` call appends to it, so writes
 * land in call order even though the callers do not await.
 */
let saveQueue: Promise<void> = Promise.resolve();

/**
 * Absolute `file://` URI of the draft document, or `null` when the file system
 * is unavailable (web, or a build where the native module is not linked).
 * `documentDirectory` is typed `string | null` and already ends with a slash.
 */
function getDraftFilePath(): string | null {
  const dir = FileSystem.documentDirectory;
  if (!dir) return null;
  return `${dir}${STORAGE_KEY}.json`;
}

function getLocalStorage(): Storage | null {
  try {
    const ls = (globalThis as { localStorage?: Storage }).localStorage;
    if (ls) return ls;
  } catch {
    // ignore — not available (e.g. bare Node)
  }
  return null;
}

function isDraftArray(value: unknown): value is DraftItem[] {
  return Array.isArray(value);
}

/**
 * Loads the persisted draft queue.
 *
 * Tries the file system, then `localStorage`; the first backend that yields a
 * structurally valid draft array wins. A corrupt or unreadable payload is
 * skipped rather than thrown — a launch must never be blocked by bad bytes.
 *
 * A failed path lookup is treated as "this platform has no file backend": the
 * read continues to `localStorage` and, if that has nothing, returns an empty
 * array. Resolving the path can throw when the native module is not linked, and
 * this runs on the launch path (`DraftsProvider` awaits it on mount), so it must
 * not be able to reject.
 *
 * @returns The persisted drafts, or an empty array when nothing is stored.
 */
export async function loadDraftItems(): Promise<DraftItem[]> {
  // The lookup is guarded on its own, not folded into the read/parse try below:
  // if it throws there is no document to talk about, so that catch's reasoning
  // ("corrupt or unreadable") would not apply. Same shape as persist().
  let path: string | null = null;
  try {
    path = getDraftFilePath();
  } catch {
    // no file backend on this platform
  }
  if (path) {
    try {
      const info = await FileSystem.getInfoAsync(path);
      if (info.exists) {
        const raw = await FileSystem.readAsStringAsync(path);
        const parsed: unknown = JSON.parse(raw);
        if (isDraftArray(parsed)) return parsed;
      }
    } catch {
      // corrupt or unreadable — fall through to other backends
    }
  }

  const ls = getLocalStorage();
  if (ls) {
    const raw = ls.getItem(STORAGE_KEY);
    if (raw) {
      try {
        const parsed: unknown = JSON.parse(raw);
        if (isDraftArray(parsed)) return parsed;
      } catch {
        // corrupt entry — ignore
      }
    }
  }

  return [];
}

/**
 * Writes one snapshot to whichever backend this platform has.
 *
 * Never rejects: every backend failure is caught, because a persistence problem
 * must not surface as an unhandled rejection in a fire-and-forget caller.
 *
 * @param payload Serialized draft queue.
 */
async function persist(payload: string): Promise<void> {
  // The path lookup is guarded separately, not folded into the write/move try below: if it
  // throws there is no document on disk to talk about, so that catch's reasoning ("the previous
  // document is still on disk") would not apply. Catching it here is what makes `never rejects`
  // a structural fact instead of a property of today's platform support.
  let path: string | null = null;
  try {
    path = getDraftFilePath();
  } catch {
    // no file backend on this platform
  }
  if (path) {
    const tmpPath = `${path}.tmp`;
    try {
      await FileSystem.writeAsStringAsync(tmpPath, payload);
      await FileSystem.moveAsync({ from: tmpPath, to: path });
      return;
    } catch {
      // Which document survives here is platform-dependent, and the older version of
      // this comment claimed more than the code delivers. Android: the failed rename
      // leaves the previous document intact, so falling through is right. iOS: if the
      // delete inside `moveAsync` succeeded and the rename did not, the previous
      // document is already gone and only `<path>.tmp` holds anything — and the read
      // path does not promote that leftover yet. What staging does guarantee on both
      // is that the target was never half-written. Fall through.
    }
  }

  const ls = getLocalStorage();
  if (ls) {
    try {
      ls.setItem(STORAGE_KEY, payload);
      return;
    } catch {
      // quota or unavailable — session-only
    }
  }
  // No durable backend on this platform: the queue keeps working for the
  // session, but a process exit loses it. Reached only by a bare Node process,
  // i.e. never on a device build.
}

/**
 * Persists the whole draft queue, overwriting the previous snapshot.
 *
 * The snapshot is written to `<file>.tmp` and then moved onto the target, so an
 * interrupted write cannot leave a truncated document behind. The move replaces
 * an existing target instead of copying it.
 *
 * Saves are serialized against each other through a module-level promise chain:
 * callers (see `DraftsProvider`, which does not await) would otherwise interleave
 * inside the single shared `.tmp` path and an older snapshot could land last. The
 * chain is ordered, so the last call's payload is the last one on disk. The queue
 * survives a rejection of the previous entry, although `persist` never rejects.
 *
 * The two backends are mutually exclusive **by platform**, not by fallback order: a
 * native build has the file system and no `localStorage`, web has `localStorage` and
 * no `documentDirectory`. `persist` still walks them in sequence, so a file-system
 * failure does fall through and attempt `localStorage` — that attempt simply never
 * succeeds on native, where there is no `localStorage` to write to. On a device,
 * therefore, a failed file-system write leaves the previous document as the only
 * readable snapshot, and the next launch reads that older one.
 *
 * Were a platform ever to have both backends, the new payload would land in
 * `localStorage` while the file stayed authoritative for reads — `loadDraftItems`
 * tries the file first and does not compare the two. That split is unreachable
 * today; it is spelled out so the mutual-exclusion claim above is not mistaken for
 * a property of the code.
 *
 * Never rejects once it returns: the path lookup and every backend failure are
 * caught. `JSON.stringify` runs synchronously before the write is enqueued, so an
 * unserialisable snapshot throws at the call site instead of rejecting.
 *
 * Note for callers that update in-memory state before calling this
 * (`DraftsProvider` does): a throw leaves memory ahead of disk.
 *
 * @param items Complete draft queue — this is a snapshot, not a delta.
 * @returns A promise for the queued write (and therefore for every write queued
 *     before it).
 */
export function saveDraftItems(items: DraftItem[]): Promise<void> {
  const payload = JSON.stringify(items);
  const run = (): Promise<void> => persist(payload);
  // Both handlers are the same: a failure earlier in the chain must not drop
  // this snapshot.
  saveQueue = saveQueue.then(run, run);
  return saveQueue;
}
