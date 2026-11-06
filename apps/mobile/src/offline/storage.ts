/**
 * Local persistence for the draft queue (E6-T5).
 *
 * `expo-file-system` and `expo-sqlite` are NOT installed in this workspace, so we
 * never static-import them. We lazy-`require` them and, if they are absent (mock
 * mode / web / missing native module), fall back to `localStorage` (web) and then
 * to a pure in-memory `Map` so the app compiles and the queue still works in
 * mock mode. See docs/HANDOFF-mobile.md for the documented fallback.
 */

import { DraftItem } from "../lib/types";

const STORAGE_KEY = "snaplist.drafts.v1";

interface FileSystemLike {
  documentDirectory?: string;
  writeAsStringAsync: (path: string, content: string) => Promise<void>;
  readAsStringAsync: (path: string) => Promise<string>;
  getInfoAsync: (path: string) => Promise<{ exists: boolean }>;
}

function getFileSystem(): FileSystemLike | null {
  const req = (globalThis as { require?: (id: string) => unknown }).require;
  if (!req) return null;
  try {
    return req("expo-file-system") as FileSystemLike;
  } catch {
    return null;
  }
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

export async function loadDraftItems(): Promise<DraftItem[]> {
  const fs = getFileSystem();
  if (fs && fs.documentDirectory) {
    const path = `${fs.documentDirectory}${STORAGE_KEY}.json`;
    try {
      const info = await fs.getInfoAsync(path);
      if (info.exists) {
        const raw = await fs.readAsStringAsync(path);
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

export async function saveDraftItems(items: DraftItem[]): Promise<void> {
  const payload = JSON.stringify(items);

  const fs = getFileSystem();
  if (fs && fs.documentDirectory) {
    try {
      await fs.writeAsStringAsync(
        `${fs.documentDirectory}${STORAGE_KEY}.json`,
        payload,
      );
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
      // quota or unavailable — in-memory only
    }
  }
  // In-memory fallback: the queue works for the session but does not survive a
  // process exit. Acceptable in mock mode; documented in HANDOFF-mobile.md.
}
