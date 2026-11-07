/**
 * E6-T4 — Export.
 *
 * Marketplace picker (ebay / poshmark / vinted) with an honest copy-fallback note
 * for the two that have no verified bulk CSV in v1 (E7-T2). Shows a preview of the
 * CSV rows that will be written (mock rows) and a share/copy action.
 *
 * Emits `export_clicked` (export_latency_ms) on tap and, once the file is "written",
 * `export_delivered` (bytes, delivery_latency_ms). The two are deliberately separate
 * — a click that fails to write is the most interesting row in the funnel. A real
 * `export_limit` wall (after 3 exports on the free tier, in mock) emits
 * `paywall_impression` with `trigger: export_limit`.
 */

import React, { useMemo, useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useDrafts } from "../offline/DraftsProvider";
import { PaywallCard } from "./Paywall";
import { buildCsvPreview, csvBytes } from "../lib/csv";
import { MarketplaceId, track } from "../lib/analytics";

const MARKETPLACES: ReadonlyArray<{
  id: MarketplaceId;
  label: string;
  note?: string;
}> = [
  { id: "ebay", label: "eBay" },
  {
    id: "poshmark",
    label: "Poshmark",
    note: "No official bulk CSV in v1 — copy/paste assist (E7-T2).",
  },
  {
    id: "vinted",
    label: "Vinted",
    note: "Bulk path varies by country — copy/paste assist (E7-T2).",
  },
];

const MOCK_EXPORT_LIMIT = 3;

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function ExportScreen(): JSX.Element {
  const drafts = useDrafts();

  const reviewable = useMemo(
    () =>
      drafts.items.filter((it) => it.status === "done" && !it.blocked_by_quota),
    [drafts.items],
  );

  const [marketplace, setMarketplace] = useState<MarketplaceId>("ebay");
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showLimitWall, setShowLimitWall] = useState(false);
  const [exportCount, setExportCount] = useState(0);

  const selectedItem =
    reviewable.find((it) => it.item_id === selectedItemId) ?? reviewable[0] ?? null;

  const csv = useMemo<string>(() => {
    if (!selectedItem) return "";
    return buildCsvPreview([selectedItem], marketplace);
  }, [selectedItem, marketplace]);

  const note = MARKETPLACES.find((m) => m.id === marketplace)?.note;

  const handleExport = async (): Promise<void> => {
    if (!selectedItem) return;
    if (!drafts.isPro && exportCount >= MOCK_EXPORT_LIMIT) {
      setShowLimitWall(true);
      return;
    }
    setCopied(false);
    const start = Date.now();
    const preview = buildCsvPreview([selectedItem], marketplace);
    const exportLatency = Date.now() - start;

    track("export_clicked", {
      item_id: selectedItem.item_id,
      marketplace,
      row_count: 1,
      export_latency_ms: exportLatency,
    });

    await delay(300); // simulate writing/downloading the file
    const bytes = csvBytes(preview);
    track("export_delivered", {
      item_id: selectedItem.item_id,
      marketplace,
      row_count: 1,
      bytes,
      delivery_latency_ms: Date.now() - start,
    });

    setCopied(true);
    setExportCount((c) => c + 1);
  };

  return (
    <View style={styles.body}>
      <Text style={styles.h}>Export</Text>

      {showLimitWall ? (
        <PaywallCard
          trigger="export_limit"
          onUpgrade={drafts.upgradeToPro}
          onDismiss={() => setShowLimitWall(false)}
        />
      ) : null}

      {reviewable.length === 0 ? (
        <Text style={styles.empty}>
          Nothing to export yet. Capture, review and approve an item first.
        </Text>
      ) : (
        <ScrollView style={styles.scroll}>
          {/* Marketplace picker */}
          <Text style={styles.sectionTitle}>Marketplace</Text>
          <View style={styles.optionRow}>
            {MARKETPLACES.map((m) => (
              <Pressable
                key={m.id}
                style={({ pressed }) => [
                  styles.opt,
                  marketplace === m.id && styles.optSelected,
                  pressed && styles.pressed,
                ]}
                onPress={() => setMarketplace(m.id)}
              >
                <Text
                  style={
                    marketplace === m.id ? styles.optTextSelected : styles.optText
                  }
                >
                  {m.label}
                </Text>
              </Pressable>
            ))}
          </View>
          {note ? <Text style={styles.note}>{note}</Text> : null}

          {/* Item selector */}
          <Text style={styles.sectionTitle}>Item</Text>
          <View style={styles.optionRow}>
            {reviewable.map((it, idx) => (
              <Pressable
                key={it.item_id}
                style={({ pressed }) => [
                  styles.opt,
                  selectedItem?.item_id === it.item_id && styles.optSelected,
                  pressed && styles.pressed,
                ]}
                onPress={() => setSelectedItemId(it.item_id)}
              >
                <Text
                  style={
                    selectedItem?.item_id === it.item_id
                      ? styles.optTextSelected
                      : styles.optText
                  }
                >
                  #{idx + 1}
                </Text>
              </Pressable>
            ))}
          </View>

          {/* CSV preview */}
          <Text style={styles.sectionTitle}>CSV preview</Text>
          <ScrollView style={styles.csvBox} horizontal>
            <Text style={styles.csv}>{csv || "(no item selected)"}</Text>
          </ScrollView>

          <Pressable
            style={({ pressed }) => [styles.exportBtn, pressed && styles.pressed]}
            onPress={() => void handleExport()}
          >
            <Text style={styles.exportBtnText}>
              {copied ? "Copied! Export again" : "Copy / share CSV"}
            </Text>
          </Pressable>
          {copied ? (
            <Text style={styles.copied}>
              Mock write complete ({csvBytes(csv)} bytes). Real clipboard/share is
              deferred (no expo-sharing; see HANDOFF-mobile.md).
            </Text>
          ) : null}
        </ScrollView>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  body: {
    flex: 1,
    padding: 16,
  },
  h: {
    fontSize: 24,
    fontWeight: "700",
    color: "#151515",
  },
  empty: {
    marginTop: 16,
    fontSize: 14,
    color: "#6B6B6B",
  },
  scroll: {
    marginTop: 8,
  },
  sectionTitle: {
    marginTop: 16,
    fontSize: 15,
    fontWeight: "700",
    color: "#151515",
  },
  optionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 8,
  },
  opt: {
    paddingVertical: 6,
    paddingHorizontal: 14,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#D8D6D1",
    backgroundColor: "#FFFFFF",
  },
  optSelected: {
    backgroundColor: "#151515",
    borderColor: "#151515",
  },
  optText: {
    fontSize: 13,
    color: "#151515",
  },
  optTextSelected: {
    fontSize: 13,
    color: "#FFFFFF",
    fontWeight: "700",
  },
  note: {
    marginTop: 6,
    fontSize: 12,
    color: "#9A6B00",
  },
  csvBox: {
    marginTop: 8,
    maxHeight: 160,
    backgroundColor: "#1E1E1E",
    borderRadius: 8,
    padding: 10,
  },
  csv: {
    color: "#9EFFB0",
    fontSize: 12,
    fontFamily: "Courier",
  },
  exportBtn: {
    marginTop: 16,
    backgroundColor: "#151515",
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
  },
  exportBtnText: {
    color: "#FFFFFF",
    fontSize: 15,
    fontWeight: "700",
  },
  copied: {
    marginTop: 8,
    fontSize: 12,
    color: "#6B6B6B",
  },
  pressed: {
    opacity: 0.85,
  },
});
