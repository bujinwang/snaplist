/**
 * E6-T5 — local drafts + offline-queue recovery.
 *
 * Acceptance for T5: capture 3 photos, force-quit, relaunch, and the photos are
 * still there. This screen is that proof surface: it lists every persisted
 * DraftItem with its job state, shows how many are in flight, and lets the
 * seller retry a failed row or remove one. The provider resumes any
 * `queued`/`running` item on mount, so a force-quit mid-job recovers here.
 *
 * The free-tier wall (E6-T5) is a draft state, not a camera state: a
 * `blocked_by_quota` row is a SAVED photo that simply has not been processed —
 * it was never discarded.
 */

import React from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { useDrafts } from "../offline/DraftsProvider";
import { useNav } from "../navigation/NavContext";
import { failureCopy } from "../lib/failureCopy";
import { DraftItem } from "../lib/types";

export function DraftsScreen(): JSX.Element {
  const drafts = useDrafts();
  const nav = useNav();

  const inFlight = drafts.items.filter(
    (it) => it.status === "queued" || it.status === "running",
  ).length;
  const blocked = drafts.items.filter((it) => it.blocked_by_quota === true).length;

  return (
    <View style={styles.body}>
      <Text style={styles.h}>Drafts</Text>
      <Text style={styles.note}>
        Saved on this device. A force-quit or a dropped connection never loses a captured photo —
        on relaunch every queued job resumes. {drafts.items.length} draft
        {drafts.items.length === 1 ? "" : "s"} stored.
      </Text>

      <View style={styles.badges}>
        <Badge label={`${inFlight} in flight`} />
        <Badge label={`${blocked} quota-blocked`} />
        <Badge label={drafts.online ? "online" : "offline"} />
      </View>

      {drafts.items.length === 0 ? (
        <Text style={styles.empty}>No drafts yet. Capture an item first.</Text>
      ) : (
        <View style={styles.list}>
          {drafts.items.map((it) => (
            <DraftRow key={it.item_id} item={it} />
          ))}
        </View>
      )}

      <Text style={styles.link} onPress={() => nav.navigate("capture")}>
        ← Back to Capture
      </Text>
    </View>
  );
}

function Badge(props: { label: string }): JSX.Element {
  return (
    <View style={styles.badge}>
      <Text style={styles.badgeText}>{props.label}</Text>
    </View>
  );
}

function statusLabel(item: DraftItem): string {
  if (item.blocked_by_quota === true) return "Saved — free limit reached";
  if (item.status === "done") return "Ready";
  if (item.status === "failed") return "Failed";
  if (item.status === "running") return "Processing…";
  return "Uploading…";
}

function DraftRow(props: { item: DraftItem }): JSX.Element {
  const drafts = useDrafts();
  const nav = useNav();
  const { item } = props;
  const copy = item.status === "failed" ? failureCopy(item.reason_code) : null;
  const batch =
    item.capture_mode === "batch" && item.batch_index !== undefined
      ? ` · #${item.batch_index + 1}`
      : "";

  return (
    <View style={styles.row}>
      <View style={styles.rowHead}>
        <Text style={styles.rowId}>
          {item.item_id.slice(0, 8)}…{batch}
        </Text>
        <Text style={item.status === "failed" ? styles.statusFail : styles.status}>
          {statusLabel(item)}
        </Text>
      </View>

      {item.status === "running" || item.status === "queued" ? (
        <ActivityIndicator size="small" color="#151515" style={styles.spinner} />
      ) : null}

      {copy ? (
        <View style={styles.failBox}>
          <Text style={styles.failTitle}>{copy.title}</Text>
          <Text style={styles.failBody}>{copy.body}</Text>
        </View>
      ) : null}

      <View style={styles.actions}>
        {item.status === "failed" && item.blocked_by_quota !== true ? (
          <Pressable
            onPress={() => drafts.retry(item.item_id)}
            style={({ pressed }) => [styles.btn, pressed && styles.pressed]}
          >
            <Text style={styles.btnText}>{copy ? copy.action : "Try again"}</Text>
          </Pressable>
        ) : null}
        {item.status === "done" ? (
          <Pressable
            onPress={() => nav.navigate("review")}
            style={({ pressed }) => [styles.btn, pressed && styles.pressed]}
          >
            <Text style={styles.btnText}>Review</Text>
          </Pressable>
        ) : null}
        <Pressable
          onPress={() => drafts.removeItem(item.item_id)}
          style={({ pressed }) => [styles.btnGhost, pressed && styles.pressed]}
        >
          <Text style={styles.btnGhostText}>Remove</Text>
        </Pressable>
      </View>
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
    letterSpacing: -0.5,
    color: "#151515",
  },
  note: {
    marginTop: 8,
    fontSize: 13,
    lineHeight: 19,
    color: "#6B6B6B",
  },
  badges: {
    marginTop: 12,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  badge: {
    backgroundColor: "#EDEBE6",
    borderRadius: 999,
    paddingVertical: 4,
    paddingHorizontal: 10,
  },
  badgeText: {
    fontSize: 12,
    color: "#151515",
  },
  empty: {
    marginTop: 20,
    fontSize: 13,
    color: "#6B6B6B",
  },
  list: {
    marginTop: 12,
  },
  row: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#E6E3DD",
  },
  rowHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  rowId: {
    fontSize: 14,
    fontWeight: "600",
    color: "#151515",
  },
  status: {
    fontSize: 13,
    color: "#6B6B6B",
  },
  statusFail: {
    fontSize: 13,
    color: "#B00020",
  },
  spinner: {
    marginTop: 8,
    alignSelf: "flex-start",
  },
  failBox: {
    marginTop: 6,
  },
  failTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: "#B00020",
  },
  failBody: {
    marginTop: 2,
    fontSize: 13,
    color: "#6B6B6B",
  },
  actions: {
    marginTop: 8,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  btn: {
    backgroundColor: "#151515",
    borderRadius: 8,
    paddingVertical: 7,
    paddingHorizontal: 12,
  },
  btnText: {
    color: "#FFFFFF",
    fontSize: 13,
    fontWeight: "600",
  },
  btnGhost: {
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#D8D6D1",
    paddingVertical: 7,
    paddingHorizontal: 12,
  },
  btnGhostText: {
    color: "#151515",
    fontSize: 13,
  },
  link: {
    marginTop: 20,
    fontSize: 14,
    fontWeight: "600",
    color: "#151515",
    textDecorationLine: "underline",
  },
  pressed: {
    opacity: 0.85,
  },
});
