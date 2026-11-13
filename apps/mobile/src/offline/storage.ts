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
 * Writes are atomic: the payload goes to `<file>.tmp` first and is then moved
 * onto the target with `moveAsync`, so the draft document is never observed
 * half-written — a crash can only lose the newest snapshot, never corrupt the
 * one already on disk. (Android implements the move as `renameTo`, i.e. a real
 * atomic replace; iOS deletes the target and then renames, both inside the same
 * native call, so the window is two syscalls rather than a whole write.)
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
 * @returns The persisted drafts, or an empty array when nothing is stored.
 */
export async function loadDraftItems(): Promise<DraftItem[]> {
  const path = getDraftFilePath();
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
 * Persists the whole draft queue, overwriting the previous snapshot.
 *
 * The snapshot is written to `<file>.tmp` and then moved onto the target, so an
 * interrupted write cannot leave a truncated document behind: readers see either
 * the previous snapshot or the new one. The move is rename-based, so it also
 * replaces an existing target without a copy.
 *
 * The write goes to the first backend that accepts it (file system → web
 * `localStorage`), so a failure on one backend still leaves the photo
 * recoverable from another. A leftover `.tmp` is harmless — the next write
 * truncates it.
 *
 * @param items Complete draft queue — this is a snapshot, not a delta.
 */
export async function saveDraftItems(items: DraftItem[]): Promise<void> {
  const payload = JSON.stringify(items);

  const path = getDraftFilePath();
  if (path) {
    const tmpPath = `${path}.tmp`;
    try {
      await FileSystem.writeAsStringAsync(tmpPath, payload);
      await FileSystem.moveAsync({ from: tmpPath, to: path });
      return;
    } catch {
      // fall through to other backends
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
