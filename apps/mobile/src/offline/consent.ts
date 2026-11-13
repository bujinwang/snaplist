/**
 * On-model consent gate — E6-T4 + E9-T2/T3.
 *
 * The consent TEXT lives in `docs/templates/consent-copy.md` (v1.0, verbatim) and
 * is rendered by `screens/OnModel.tsx`. This module only remembers whether the
 * seller accepted it, keyed to the version string, so a later legal revision
 * re-prompts instead of silently reusing stale consent.
 *
 * Persistence here is `localStorage` (web) plus an in-memory copy, which means the
 * flag does not survive a force-quit on native — acceptable for this mock gate, to
 * be revisited when the real consent flow ships. This module deliberately does not
 * use `expo-file-system`: the app does ship it (17.0.1, imported statically by
 * ./storage.ts for the durable draft queue), but one version string does not need
 * the file-system path.
 */

const CONSENT_KEY = "snaplist.onmodel_consent.v1";

/** Bump ONLY together with docs/templates/consent-copy.md, after legal review. */
export const ON_MODEL_CONSENT_VERSION = "v1.0";

let memoryAccepted: string | null = null;

function getLocalStorage(): Storage | null {
  try {
    const ls = (globalThis as { localStorage?: Storage }).localStorage;
    if (ls) return ls;
  } catch {
    // not available (bare Node / restricted runtime)
  }
  return null;
}

export function hasAcceptedOnModelConsent(): boolean {
  const ls = getLocalStorage();
  if (ls) {
    try {
      return ls.getItem(CONSENT_KEY) === ON_MODEL_CONSENT_VERSION;
    } catch {
      // fall through to memory
    }
  }
  return memoryAccepted === ON_MODEL_CONSENT_VERSION;
}

export function acceptOnModelConsent(): void {
  memoryAccepted = ON_MODEL_CONSENT_VERSION;
  const ls = getLocalStorage();
  if (ls) {
    try {
      ls.setItem(CONSENT_KEY, ON_MODEL_CONSENT_VERSION);
    } catch {
      // memory fallback is already set
    }
  }
}
