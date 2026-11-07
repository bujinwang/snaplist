/**
 * E6-T2 (single capture) + E6-T6 (batch capture).
 *
 * A quad-guide overlay frames the garment; a mock camera preview sits behind it
 * (the real `expo-camera` is not installed — see HANDOFF-mobile.md). Torch is a
 * state toggle with a "deferred" note. "Shoot the back" prompts the seller.
 *
 * Capture simulates capture→upload→job→review with MOCK data (no backend). Every
 * shutter emits `upload_started` with `capture_mode` ("single" | "batch") and, for
 * batch, `batch_index`. The 4th capture hits the free-tier wall: the photo is
 * SAVED to the draft queue (never lost) and a paywall is shown (E6-T5).
 *
 * Batch (T6): each tap enqueues one upload with its own progress; a per-item
 * failure is a first-class row — visible, with §6 copy and a Retry — and never
 * aborts the others. Partial success is the normal state.
 */

import React, { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useDrafts } from "../offline/DraftsProvider";
import { useNav } from "../navigation/NavContext";
import { PaywallCard } from "./Paywall";
import { failureCopy } from "../lib/failureCopy";
import { CaptureMode, DraftItem } from "../lib/types";

export function CaptureScreen(): JSX.Element {
  const drafts = useDrafts();
  const nav = useNav();
  const [mode, setMode] = useState<CaptureMode>("single");
  const [torch, setTorch] = useState(false);
  const [quotaItemId, setQuotaItemId] = useState<string | null>(null);

  const visible = useMemo<DraftItem[]>(() => {
    return drafts.items
      .filter((it) => it.capture_mode === mode)
      .sort((a, b) => (a.batch_index ?? 0) - (b.batch_index ?? 0));
  }, [drafts.items, mode]);

  const handleCapture = async (): Promise<void> => {
    if (mode === "single") {
      const res = await drafts.capture("single", undefined);
      if (res.quotaExceeded) setQuotaItemId(res.itemId);
      return;
    }
    const nextIndex = drafts.items.filter((it) => it.capture_mode === "batch").length;
    const res = await drafts.capture("batch", nextIndex);
    if (res.quotaExceeded) setQuotaItemId(res.itemId);
  };

  const quotaItem = quotaItemId
    ? drafts.items.find((it) => it.item_id === quotaItemId) ?? null
    : null;

  return (
    <View style={styles.body}>
      {/* Mode toggle */}
      <View style={styles.modeRow}>
        <ModeButton
          label="Single"
          active={mode === "single"}
          onPress={() => setMode("single")}
        />
        <ModeButton
          label="Batch"
          active={mode === "batch"}
          onPress={() => setMode("batch")}
        />
      </View>

      {/* Mock camera + quad guide */}
      <View style={styles.camera}>
        <View style={styles.cameraTint} />
        <View style={styles.guide}>
          <Corner pos="tl" />
          <Corner pos="tr" />
          <Corner pos="bl" />
          <Corner pos="br" />
          <Text style={styles.shootPrompt}>Shoot the back</Text>
        </View>
      </View>

      {/* Torch toggle */}
      <Pressable
        style={({ pressed }) => [styles.torch, pressed && styles.pressed]}
        onPress={() => setTorch((t) => !t)}
      >
        <Text style={styles.torchText}>
          {torch ? "Torch: ON" : "Torch: OFF"}
        </Text>
      </Pressable>
      <Text style={styles.torchNote}>
        Torch is a toggle in this build (real torch deferred — expo-camera not installed).
      </Text>

      {/* Capture button */}
      <Pressable
        style={({ pressed }) => [styles.shutter, pressed && styles.pressed]}
        onPress={() => void handleCapture()}
      >
        <Text style={styles.shutterText}>
          {mode === "batch" ? "Capture + queue" : "Capture"}
        </Text>
      </Pressable>
      {mode === "batch" ? (
        <Text style={styles.hint}>Keep tapping — each shot queues its own upload.</Text>
      ) : null}

      {/* Quota wall (photo already saved to drafts) */}
      {quotaItem && quotaItem.blocked_by_quota ? (
        <PaywallCard
          trigger="upload_4th"
          onUpgrade={drafts.upgradeToPro}
          onDismiss={() => setQuotaItemId(null)}
        />
      ) : null}

      {/* Capture queue / progress list */}
      <Text style={styles.sectionTitle}>
        {mode === "batch" ? `Batch queue (${visible.length})` : "Captures"}
      </Text>
      <View style={styles.list}>
        {visible.length === 0 ? (
          <Text style={styles.empty}>No captures yet. Tap Capture above.</Text>
        ) : (
          visible.map((it) => (
            <CaptureRow key={it.item_id} item={it} onReview={() => nav.navigate("review")} />
          ))
        )}
      </View>

      <View style={styles.navRow}>
        <Text style={styles.link} onPress={() => nav.navigate("review")}>
          Go to Review →
        </Text>
        <Text style={styles.link} onPress={() => nav.navigate("drafts")}>
          Drafts →
        </Text>
      </View>
    </View>
  );
}

function ModeButton(props: {
  label: string;
  active: boolean;
  onPress: () => void;
}): JSX.Element {
  return (
    <Pressable
      style={({ pressed }) => [
        styles.modeBtn,
        props.active && styles.modeBtnActive,
        pressed && styles.pressed,
      ]}
      onPress={props.onPress}
    >
      <Text style={props.active ? styles.modeBtnTextActive : styles.modeBtnText}>
        {props.label}
      </Text>
    </Pressable>
  );
}

function Corner(props: { pos: "tl" | "tr" | "bl" | "br" }): JSX.Element {
  const base: Record<string, object> = {
    tl: { top: 0, left: 0 },
    tr: { top: 0, right: 0 },
    bl: { bottom: 0, left: 0 },
    br: { bottom: 0, right: 0 },
  };
  const p = base[props.pos] ?? {};
  return <View style={[styles.corner, p]} />;
}

function CaptureRow(props: {
  item: DraftItem;
  onReview: () => void;
}): JSX.Element {
  const { item, onReview } = props;
  const drafts = useDrafts();
  const label =
    item.capture_mode === "batch" && item.batch_index !== undefined
      ? `#${item.batch_index + 1}`
      : "";

  if (item.status === "done") {
    return (
      <View style={styles.row}>
        <Text style={styles.rowLabel}>
          {label} Ready
        </Text>
        <Text style={styles.link} onPress={onReview}>
          Review →
        </Text>
      </View>
    );
  }

  if (item.status === "failed" && !item.blocked_by_quota) {
    const copy = failureCopy(item.reason_code);
    return (
      <View style={styles.rowFail}>
        <Text style={styles.failTitle}>{copy.title}</Text>
        <Text style={styles.failBody}>{copy.body}</Text>
        <Pressable
          style={({ pressed }) => [styles.retry, pressed && styles.pressed]}
          onPress={() => drafts.retry(item.item_id)}
        >
          <Text style={styles.retryText}>{copy.action}</Text>
        </Pressable>
      </View>
    );
  }

  if (item.blocked_by_quota) {
    return (
      <View style={styles.row}>
        <Text style={styles.rowLabel}>{label} Saved — free limit reached</Text>
        <Text style={styles.muted}>Upgrade to process →</Text>
      </View>
    );
  }

  return (
    <View style={styles.row}>
      <ActivityIndicator size="small" color="#151515" />
      <Text style={styles.rowLabel}>
        {label} {item.status === "running" ? "Processing…" : "Uploading…"}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  body: {
    flex: 1,
    padding: 16,
  },
  modeRow: {
    flexDirection: "row",
    gap: 10,
  },
  modeBtn: {
    paddingVertical: 8,
    paddingHorizontal: 18,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: "#D8D6D1",
    backgroundColor: "#FFFFFF",
  },
  modeBtnActive: {
    backgroundColor: "#151515",
    borderColor: "#151515",
  },
  modeBtnText: {
    fontSize: 14,
    color: "#151515",
  },
  modeBtnTextActive: {
    fontSize: 14,
    color: "#FFFFFF",
    fontWeight: "700",
  },
  camera: {
    marginTop: 14,
    height: 300,
    borderRadius: 14,
    backgroundColor: "#2B2B2B",
    overflow: "hidden",
    justifyContent: "center",
    alignItems: "center",
  },
  cameraTint: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.15)",
  },
  guide: {
    width: 220,
    height: 220,
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.85)",
    borderRadius: 8,
  },
  corner: {
    position: "absolute",
    width: 22,
    height: 22,
    borderColor: "#FFD479",
    borderWidth: 3,
  },
  shootPrompt: {
    position: "absolute",
    bottom: -34,
    color: "#FFFFFF",
    fontSize: 14,
    fontWeight: "600",
  },
  torch: {
    marginTop: 40,
    alignSelf: "flex-start",
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 8,
    backgroundColor: "#EDEBE6",
  },
  torchText: {
    fontSize: 14,
    fontWeight: "600",
    color: "#151515",
  },
  torchNote: {
    marginTop: 6,
    fontSize: 12,
    color: "#6B6B6B",
  },
  shutter: {
    marginTop: 16,
    backgroundColor: "#151515",
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
  },
  shutterText: {
    color: "#FFFFFF",
    fontSize: 16,
    fontWeight: "700",
  },
  hint: {
    marginTop: 8,
    fontSize: 12,
    color: "#6B6B6B",
  },
  sectionTitle: {
    marginTop: 20,
    fontSize: 15,
    fontWeight: "700",
    color: "#151515",
  },
  list: {
    marginTop: 8,
  },
  empty: {
    fontSize: 13,
    color: "#6B6B6B",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#E6E3DD",
  },
  rowLabel: {
    fontSize: 14,
    color: "#151515",
  },
  muted: {
    fontSize: 13,
    color: "#9A9A9A",
  },
  rowFail: {
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#E6E3DD",
  },
  failTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: "#B00020",
  },
  failBody: {
    fontSize: 13,
    color: "#6B6B6B",
    marginTop: 2,
  },
  retry: {
    marginTop: 8,
    alignSelf: "flex-start",
    backgroundColor: "#151515",
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 14,
  },
  retryText: {
    color: "#FFFFFF",
    fontSize: 13,
    fontWeight: "600",
  },
  link: {
    fontSize: 14,
    color: "#151515",
    fontWeight: "600",
    textDecorationLine: "underline",
  },
  navRow: {
    marginTop: 16,
    flexDirection: "row",
    justifyContent: "space-between",
  },
  pressed: {
    opacity: 0.85,
  },
});
