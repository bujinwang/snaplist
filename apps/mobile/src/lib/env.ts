/**
 * Runtime configuration. PLAN.md E6-T1 mandates that API_BASE and the Supabase creds come from
 * `expo-constants` (the `extra` block in app.json / EAS env), never a hardcoded string.
 *
 * We read it through a lazy global `require` so the app still *compiles and runs in mock mode*
 * when `expo-constants` is not installed in this workspace (the registry mirror is currently
 * refusing tarball downloads). When the package is present, its values win; otherwise we fall
 * back to the committed defaults below, which are all empty -> mock mode.
 */

interface ExtraConfig {
  apiBase?: string;
  supabaseUrl?: string;
  supabaseAnonKey?: string;
}
interface ExpoConfigLike {
  extra?: ExtraConfig;
  version?: string;
}
interface ConstantsLike {
  expoConfig?: ExpoConfigLike | null;
}

function readConfig(): { extra: ExtraConfig; version: string } {
  const fallback: { extra: ExtraConfig; version: string } = {
    extra: { apiBase: "", supabaseUrl: "", supabaseAnonKey: "" },
    version: "0.0.0",
  };
  const req = (globalThis as { require?: (id: string) => unknown }).require;
  if (!req) return fallback;
  try {
    const Constants = req("expo-constants") as ConstantsLike;
    const ec = Constants.expoConfig ?? null;
    if (ec) {
      fallback.extra = (ec.extra ?? {}) as ExtraConfig;
      if (typeof ec.version === "string" && ec.version.length > 0) {
        fallback.version = ec.version;
      }
    }
  } catch {
    // expo-constants not installed — use committed defaults (mock mode).
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
