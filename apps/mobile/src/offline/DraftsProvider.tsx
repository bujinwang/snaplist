/**
 * DraftsProvider — the local capture queue + offline store (E6-T5) and the
 * billing/paywall state shared by OnModel/Export (E6-T4).
 *
 * Single source of truth for every screen: capture enqueues a DraftItem, review
 * reads `attributes`, on-model reads `on_model_state`, export reads `attributes`,
 * and the draft list persists everything. A captured photo is NEVER lost — it is
 * persisted on every change and, on relaunch, any item left in `queued`/`running`
 * is resumed. The free-tier wall interacts with drafts (not the camera): a 4th
 * capture is saved as `blocked_by_quota`, not discarded.
 *
 * Native modules (`expo-file-system`, `expo-sqlite`) are NOT installed; the
 * persistence layer lazy-requires them and falls back to `localStorage`/in-memory
 * (see ./storage.ts). All job work here is MOCK: no backend is contacted.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  createDraftItem,
  makeMockAttributes,
  mockLatencies,
  newItemId,
  simulateJob,
  simulateVtonFail,
} from "../lib/mockData";
import { loadDraftItems, saveDraftItems } from "./storage";
import { track } from "../lib/analytics";
import { DraftItem, ItemAttributes, ReasonCode } from "../lib/types";

// NOTE: in live mode this MUST come from GET /v1/usage (CONTRACTS §8) — never a
// hardcoded constant. This mock value (3, the free tier) is used only when there
// is no backend, so design review needs no server. Documented in HANDOFF-mobile.md.
const MOCK_FREE_TIER_ITEM_LIMIT = 3;

export interface CaptureResult {
  quotaExceeded: boolean;
  itemId: string;
}

interface DraftsState {
  items: DraftItem[];
  isPro: boolean;
  online: boolean;
  /** When true, the NEXT attribute PATCH fails (rollback demo, E6-T3). */
  simulatePatchFailure: boolean;
  capture: (mode: "single" | "batch", batchIndex?: number) => Promise<CaptureResult>;
  retry: (itemId: string) => void;
  patchAttributes: (itemId: string, patch: Partial<ItemAttributes>) => Promise<void>;
  requestOnModel: (itemId: string, modelCard: string) => Promise<void>;
  upgradeToPro: (variant: "a" | "b" | "c") => void;
  toggleSimulatePatchFailure: () => void;
  setOnline: (next: boolean) => void;
  removeItem: (itemId: string) => void;
}

const DraftsContext = createContext<DraftsState | null>(null);

export function useDrafts(): DraftsState {
  const ctx = useContext(DraftsContext);
  if (!ctx) throw new Error("useDrafts must be used within <DraftsProvider>");
  return ctx;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function DraftsProvider(props: { children: React.ReactNode }): JSX.Element {
  const { children } = props;
  const [items, setItemsState] = useState<DraftItem[]>([]);
  const [isPro, setIsPro] = useState(false);
  const [online, setOnlineState] = useState(true);
  const [simulatePatchFailure, setSimulatePatchFailureState] = useState(false);

  const itemsRef = useRef<DraftItem[]>([]);
  const onlineRef = useRef(true);
  const simulatePatchFailureRef = useRef(false);

  const commit = useCallback((next: DraftItem[]): void => {
    itemsRef.current = next;
    setItemsState(next);
    void saveDraftItems(next);
  }, []);

  const updateItem = useCallback(
    (itemId: string, fn: (it: DraftItem) => DraftItem): void => {
      commit(
        itemsRef.current.map((it) => (it.item_id === itemId ? fn(it) : it)),
      );
    },
    [commit],
  );

  // ---- job simulation (mock pipeline) ---------------------------------------
  const process = useCallback(
    async (itemId: string): Promise<void> => {
      updateItem(itemId, (it) => ({ ...it, status: "running" }));
      await delay(700); // upload + enqueue
      const result = simulateJob(itemId);
      if (result.status === "failed") {
        const reason: ReasonCode = result.reason_code ?? "internal";
        updateItem(itemId, (it) => ({
          ...it,
          status: "failed",
          reason_code: reason,
        }));
        return;
      }
      const lat = mockLatencies(itemId);
      updateItem(itemId, (it) => ({
        ...it,
        status: "done",
        job_latency_ms: lat.job_latency_ms,
        prettify_latency_ms: lat.prettify_latency_ms,
        attrs_latency_ms: lat.attrs_latency_ms,
        // attributes are attached by the caller (mockData) via a second pass to
        // keep this module free of attribute generation details:
      }));
    },
    [updateItem],
  );

  // attach mock attributes when a job finishes — done here so DraftsProvider owns
  // the full item transition without re-importing the attribute generator twice.
  const processWithAttributes = useCallback(
    async (itemId: string): Promise<void> => {
      await process(itemId);
      const it = itemsRef.current.find((d) => d.item_id === itemId);
      if (it && it.status === "done" && !it.attributes) {
        // Attach attributes from the same generator used at capture time.
        updateItem(itemId, (d) => ({ ...d, attributes: makeMockAttributes(itemId) }));
      }
    },
    [process, updateItem],
  );

  // ---- capture --------------------------------------------------------------
  const capture = useCallback(
    async (mode: "single" | "batch", batchIndex?: number): Promise<CaptureResult> => {
      const id = newItemId();
      track("upload_started", {
        item_id: id,
        source: "camera",
        capture_mode: mode,
        ...(mode === "batch" && batchIndex !== undefined
          ? { batch_index: batchIndex }
          : {}),
      });

      const completedCount = itemsRef.current.filter(
        (it) => it.status === "done" && !it.blocked_by_quota,
      ).length;

      if (!isPro && completedCount >= MOCK_FREE_TIER_ITEM_LIMIT) {
        // Hard wall interacts with the draft, NOT the camera: the photo is saved.
        const item = createDraftItem(mode, batchIndex, id);
        item.blocked_by_quota = true;
        commit([item, ...itemsRef.current]);
        return { quotaExceeded: true, itemId: id };
      }

      const item = createDraftItem(mode, batchIndex, id);
      commit([item, ...itemsRef.current]);
      if (onlineRef.current) void processWithAttributes(id);
      return { quotaExceeded: false, itemId: id };
    },
    [isPro, commit, processWithAttributes],
  );

  const retry = useCallback(
    (itemId: string): void => {
      updateItem(itemId, (it) => ({
        ...it,
        status: "queued",
        reason_code: undefined,
        retry_count: it.retry_count + 1,
        blocked_by_quota: false,
      }));
      if (onlineRef.current) void processWithAttributes(itemId);
    },
    [updateItem, processWithAttributes],
  );

  const patchAttributes = useCallback(
    async (itemId: string, patch: Partial<ItemAttributes>): Promise<void> => {
      const current = itemsRef.current.find((it) => it.item_id === itemId);
      if (!current || !current.attributes) {
        throw new Error("item has no attributes to patch");
      }
      const prevAttrs = current.attributes;
      const nextAttrs: ItemAttributes = { ...prevAttrs, ...patch };
      updateItem(itemId, (it) => ({ ...it, attributes: nextAttrs }));

      await delay(250); // simulated PATCH round-trip

      if (simulatePatchFailureRef.current) {
        // Rollback the optimistic update and surface the failure to the caller.
        simulatePatchFailureRef.current = false;
        setSimulatePatchFailureState(false);
        updateItem(itemId, (it) => ({ ...it, attributes: prevAttrs }));
        throw new Error("simulated_offline_patch_failure");
      }
    },
    [updateItem],
  );

  const requestOnModel = useCallback(
    async (itemId: string, modelCard: string): Promise<void> => {
      updateItem(itemId, (it) => ({
        ...it,
        on_model_state: "rendering",
        vton_status: "running",
      }));
      await delay(1500);
      if (simulateVtonFail(itemId)) {
        updateItem(itemId, (it) => ({
          ...it,
          on_model_state: "not_requested",
          vton_status: "failed",
          vton_reason_code: "vton_rejected",
        }));
        return;
      }
      updateItem(itemId, (it) => ({
        ...it,
        on_model_state: "ready",
        vton_status: "done",
        on_model_urls: [`mock://onmodel/${itemId}/${modelCard}`],
      }));
    },
    [updateItem],
  );

  const upgradeToPro = useCallback((variant: "a" | "b" | "c"): void => {
    setIsPro(true);
    track("subscription_started", {
      plan: "pro",
      paywall_variant: variant,
      amount_cents: 4900,
      currency: "usd",
    });
    // A successful upgrade clears any quota-blocked drafts so they can process.
    itemsRef.current
      .filter((it) => it.blocked_by_quota)
      .forEach((it) => {
        updateItem(it.item_id, (d) => ({ ...d, blocked_by_quota: false }));
        void processWithAttributes(it.item_id);
      });
  }, [processWithAttributes, updateItem]);

  const toggleSimulatePatchFailure = useCallback((): void => {
    const next = !simulatePatchFailureRef.current;
    simulatePatchFailureRef.current = next;
    setSimulatePatchFailureState(next);
  }, []);

  const setOnline = useCallback(
    (next: boolean): void => {
      onlineRef.current = next;
      setOnlineState(next);
      if (next) {
        // Reconnect: resume anything left in queued/running and retry failures.
        itemsRef.current.forEach((it) => {
          if (
            (it.status === "queued" || it.status === "running" || it.status === "failed") &&
            !it.blocked_by_quota
          ) {
            void processWithAttributes(it.item_id);
          }
        });
      }
    },
    [processWithAttributes],
  );

  const removeItem = useCallback(
    (itemId: string): void => {
      commit(itemsRef.current.filter((it) => it.item_id !== itemId));
    },
    [commit],
  );

  // ---- load + resume on mount ----------------------------------------------
  useEffect(() => {
    let active = true;
    void (async () => {
      const loaded = await loadDraftItems();
      if (!active) return;
      commit(loaded);
      // Resume any job interrupted by a force-quit (never lose a photo).
      loaded.forEach((it) => {
        if (
          (it.status === "queued" || it.status === "running") &&
          !it.blocked_by_quota
        ) {
          void processWithAttributes(it.item_id);
        }
      });
    })();
    return () => {
      active = false;
    };
  }, [commit, processWithAttributes]);

  const value: DraftsState = {
    items,
    isPro,
    online,
    simulatePatchFailure,
    capture,
    retry,
    patchAttributes,
    requestOnModel,
    upgradeToPro,
    toggleSimulatePatchFailure,
    setOnline,
    removeItem,
  };

  return (
    <DraftsContext.Provider value={value}>{children}</DraftsContext.Provider>
  );
}
