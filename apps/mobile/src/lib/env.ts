/**
 * Runtime configuration. PLAN.md E6-T1 mandates that API_BASE and the Supabase creds come from
 * `expo-constants` (the `extra` block in app.json / EAS env), never a hardcoded string.
 *
 * `expo-constants` is a real dependency now (the registry is reachable again), so this is a
 * static import. When `extra` is absent the defaults below are all empty -> mock mode, which
 * keeps every screen renderable without a backend.
 */

import Constants from "expo-constants";

interface ExtraConfig {
  apiBase?: string;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
}

function readConfig(): { extra: ExtraConfig; version: string } {
  const fallback: { extra: ExtraConfig; version: string } = {
    extra: { apiBase: "", supabaseUrl: "", supabaseAnonKey: "" },
    version: "0.0.0",
  };
  const ec = Constants.expoConfig ?? null;
  if (!ec) return fallback;
  fallback.extra = (ec.extra ?? {}) as ExtraConfig;
  if (typeof ec.version === "string" && ec.version.length > 0) {
    fallback.version = ec.version;
  }
  return fallback;
}

const { extra, version } = readConfig();

function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export const config = {
  apiBase: str(extra.apiBase),
  supabaseUrl: str(extra.supabaseUrl),
  supabaseAnonKey: str(extra.supabaseAnonKey),
};

/** False in mock mode — every screen must still render fully. */
export const hasApi: boolean = config.apiBase.length > 0;

/** False when Supabase env is absent — we fall back to a mock anonymous session. */
export const hasSupabase: boolean =
  config.supabaseUrl.length > 0 && config.supabaseAnonKey.length > 0;

export const appVersion: string = version;

export type PlatformName = "ios" | "android" | "web";
