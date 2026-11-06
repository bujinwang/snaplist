/**
 * E6-T3 — Review + attribute editing.
 *
 * Side-by-side original vs prettified (mock colored panels, clearly labeled),
 * Approve / Retry / Remove-bg-waitlist, and editable attribute chips with a
 * subtle confidence bar and one-tap correction.
 *
 * Analytics duty (the three numbers E0-T5 gates on):
 * - `prettify_approved` emitted on EVERY decision (including each retry) carrying
 *   `job_latency_ms`, `prettify_latency_ms`, `attrs_latency_ms`.
 * - `attributes_edited` emitted when the item's visit ends, carrying
 *   `fields_touched` (reading counts as touching — 0 is allowed, the blind-accept
 *   bucket), `edited_field_count`, and `field_names` WITHOUT values (PII ban).
 *
 * Editing does an optimistic PATCH via `patchAttributes`; a simulated failure
 * (flip "Simulate offline") rolls the UI back and shows §6 copy — never a
 * `reason_code` string.
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Dimensions,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";

import { useDrafts } from "../offline/DraftsProvider";
import { useNav } from "../navigation/NavContext";
import { failureCopy } from "../lib/failureCopy";
import {
  CONDITIONS,
  DraftItem,
  FITS,
  GARMENT_CATEGORIES,
  ItemAttributes,
  PATTERNS,
} from "../lib/types";
import {
  SAMPLE_COLORS,
  SAMPLE_MATERIALS,
  defaultAttributes,
} from "../lib/mockData";
import { track } from "../lib/analytics";

type EnumKey = "category" | "pattern" | "fit" | "condition";
type MultiKey = "colors" | "material";
type ActiveField = EnumKey | MultiKey | null;

const ENUM_FIELDS: ReadonlyArray<{
  key: EnumKey;
  label: string;
  options: readonly string[];
}> = [
  { key: "category", label: "Category", options: GARMENT_CATEGORIES },
  { key: "pattern", label: "Pattern", options: PATTERNS },
  { key: "fit", label: "Fit", options: FITS },
  { key: "condition", label: "Condition", options: CONDITIONS },
];
const MULTI_FIELDS: ReadonlyArray<{
  key: MultiKey;
  label: string;
  options: readonly string[];
}> = [
  { key: "colors", label: "Color", options: SAMPLE_COLORS },
  { key: "material", label: "Material", options: SAMPLE_MATERIALS },
];

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** Count fields whose value differs from the original (edited, not just read). */
function countEdited(orig: ItemAttributes, cur: ItemAttributes): number {
  let n = 0;
  if (JSON.stringify(orig.category) !== JSON.stringify(cur.category)) n++;
  if (JSON.stringify(orig.colors) !== JSON.stringify(cur.colors)) n++;
  if (JSON.stringify(orig.pattern) !== JSON.stringify(cur.pattern)) n++;
  if (JSON.stringify(orig.material) !== JSON.stringify(cur.material)) n++;
  if (JSON.stringify(orig.fit) !== JSON.stringify(cur.fit)) n++;
  if (JSON.stringify(orig.condition) !== JSON.stringify(cur.condition)) n++;
  return n;
}

function buildPatch(
  field: ActiveField,
  value: string | string[],
): Partial<ItemAttributes> {
  const patch: Partial<ItemAttributes> = {};
  if (field === "colors") patch.colors = value as string[];
  else if (field === "material") patch.material = value as string[];
  else if (field === "category") patch.category = value as ItemAttributes["category"];
  else if (field === "pattern") patch.pattern = value as ItemAttributes["pattern"];
  else if (field === "fit") patch.fit = value as ItemAttributes["fit"];
  else if (field === "condition") patch.condition = value as ItemAttributes["condition"];
  return patch;
}

// ---------------------------------------------------------------------------

export function ReviewScreen(): JSX.Element {
  const drafts = useDrafts();
  const reviewable = useMemo<DraftItem[]>(
    () =>
      drafts.items.filter((it) => it.status === "done" && !it.blocked_by_quota),
    [drafts.items],
  );
  const { width } = Dimensions.get("window");

  return (
    <View style={styles.body}>
      <View style={styles.controls}>
        <Text style={styles.simLabel}>Simulate offline (next edit fails)</Text>
        <Switch
          value={drafts.simulatePatchFailure}
          onValueChange={() => drafts.toggleSimulatePatchFailure()}
        />
      </View>

      {reviewable.length === 0 ? (
        <View style={styles.emptyWrap}>
          <Text style={styles.empty}>
            Nothing to review yet. Capture an item first.
          </Text>
        </View>
      ) : (
        <ScrollView
          horizontal
          pagingEnabled
          showsHorizontalScrollIndicator={false}
        >
          {reviewable.map((it) => (
            <View key={it.item_id} style={[styles.page, { width }]}>
              <ReviewItem item={it} />
            </View>
          ))}
        </ScrollView>
      )}
    </View>
  );
}

function ReviewItem(props: { item: DraftItem }): JSX.Element {
  const { item } = props;
  const drafts = useDrafts();
  const nav = useNav();

  const [draftAttrs, setDraftAttrs] = useState<ItemAttributes>(() =>
    item.attributes ? clone(item.attributes) : defaultAttributes(),
  );
  const draftAttrsRef = useRef<ItemAttributes>(draftAttrs);
  draftAttrsRef.current = draftAttrs;

  const origRef = useRef<ItemAttributes | null>(null);
  if (origRef.current === null) {
    origRef.current = item.attributes
      ? clone(item.attributes)
      : defaultAttributes();
  }

  const touchedRef = useRef<Set<string>>(new Set());
  const [touched, setTouched] = useState<Set<string>>(new Set());

  const [activeField, setActiveField] = useState<ActiveField>(null);
  const [patchError, setPatchError] = useState<string | null>(null);
  const [decided, setDecided] = useState<
    "approve" | "retry" | "remove_bg_waitlist" | null
  >(null);

  // Emit attributes_edited once when this item's visit ends (leaving review).
  useEffect(() => {
    return () => {
      const orig = origRef.current;
      const cur = draftAttrsRef.current;
      if (orig && cur) {
        const edited = countEdited(orig, cur);
        track("attributes_edited", {
          item_id: item.item_id,
          edited_field_count: edited,
          fields_touched: touchedRef.current.size,
          field_names: [...touchedRef.current],
        });
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.item_id]);

  const markTouched = (field: string): void => {
    if (touchedRef.current.has(field)) return;
    const next = new Set(touchedRef.current);
    next.add(field);
    touchedRef.current = next;
    setTouched(next);
  };

  const applyEdit = async (
    field: ActiveField,
    value: string | string[],
  ): Promise<void> => {
    if (!field) return;
    markTouched(field);
    const patch = buildPatch(field, value);
    const prev = draftAttrsRef.current;
    const nextAttrs = { ...prev, ...patch };
    setDraftAttrs(nextAttrs);
    draftAttrsRef.current = nextAttrs;
    setPatchError(null);
    try {
      await drafts.patchAttributes(item.item_id, patch);
    } catch {
      // Optimistic update rolled back (provider reverted + we revert locally).
      setDraftAttrs(prev);
      draftAttrsRef.current = prev;
      setPatchError(failureCopy("internal").body);
    }
  };

  const decide = (
    decision: "approve" | "retry" | "remove_bg_waitlist",
  ): void => {
    track("prettify_approved", {
      item_id: item.item_id,
      decision,
      job_latency_ms: item.job_latency_ms,
      prettify_latency_ms: item.prettify_latency_ms,
      attrs_latency_ms: item.attrs_latency_ms,
    });
    setDecided(decision);
  };

  const openField = (field: ActiveField): void => {
    if (!field) return;
    markTouched(field);
    setActiveField(field);
  };

  const confidencePct = (field: string): number => {
    const c = draftAttrs.confidence[field];
    return typeof c === "number" ? Math.round(c * 100) : 0;
  };

  return (
    <ScrollView style={styles.itemScroll}>
      {/* Side-by-side images */}
      <View style={styles.images}>
        <View style={styles.imageCol}>
          <View
            style={[styles.imageBox, { backgroundColor: item.originalColor }]}
          />
          <Text style={styles.imageLabel}>Original</Text>
        </View>
        <View style={styles.imageCol}>
          <View
            style={[styles.imageBox, { backgroundColor: item.prettifiedColor }]}
          />
          <Text style={styles.imageLabel}>Prettified</Text>
        </View>
      </View>

      {/* Decision buttons */}
      <View style={styles.decisions}>
        <DecisionButton
          label="Approve"
          active={decided === "approve"}
          onPress={() => decide("approve")}
        />
        <DecisionButton
          label="Retry"
          active={decided === "retry"}
          onPress={() => decide("retry")}
        />
        <DecisionButton
          label="Remove-bg waitlist"
          active={decided === "remove_bg_waitlist"}
          onPress={() => decide("remove_bg_waitlist")}
        />
      </View>
      {decided ? (
        <Text style={styles.decidedNote}>
          Logged: {decided} (you can still tweak attributes below).
        </Text>
      ) : null}

      {/* Attribute chips */}
      <Text style={styles.sectionTitle}>Attributes</Text>
      <View style={styles.chips}>
        {ENUM_FIELDS.map((f) => (
          <Chip
            key={f.key}
            label={f.label}
            value={String(draftAttrs[f.key])}
            pct={confidencePct(f.key)}
            needsReview={draftAttrs.needs_review.includes(f.key)}
            active={activeField === f.key}
            onPress={() => openField(f.key)}
          />
        ))}
        {MULTI_FIELDS.map((f) => (
          <Chip
            key={f.key}
            label={f.label}
            value={
              (draftAttrs[f.key] as string[]).length > 0
                ? (draftAttrs[f.key] as string[]).join(", ")
                : "—"
            }
            pct={confidencePct(f.key)}
            needsReview={draftAttrs.needs_review.includes(f.key)}
            active={activeField === f.key}
            onPress={() => openField(f.key)}
          />
        ))}
      </View>

      {/* Editor for the active field */}
      {activeField ? (
        <View style={styles.editor}>
          <Text style={styles.editorTitle}>
            {labelFor(activeField)} — tap to correct
          </Text>
          {isMulti(activeField) ? (
            <View style={styles.optionRow}>
              {optionsFor(activeField).map((opt) => {
                const selected = (draftAttrs[activeField] as string[]).includes(
                  opt,
                );
                return (
                  <Pressable
                    key={opt}
                    style={({ pressed }) => [
                      styles.opt,
                      selected && styles.optSelected,
                      pressed && styles.pressed,
                    ]}
                    onPress={() => {
                      const cur = draftAttrs[activeField] as string[];
                      const next = selected
                        ? cur.filter((c) => c !== opt)
                        : [...cur, opt];
                      void applyEdit(activeField, next);
                    }}
                  >
                    <Text
                      style={
                        selected ? styles.optTextSelected : styles.optText
                      }
                    >
                      {opt}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          ) : (
            <View style={styles.optionRow}>
              {optionsFor(activeField).map((opt) => {
                const selected = String(draftAttrs[activeField]) === opt;
                return (
                  <Pressable
                    key={opt}
                    style={({ pressed }) => [
                      styles.opt,
                      selected && styles.optSelected,
                      pressed && styles.pressed,
                    ]}
                    onPress={() => void applyEdit(activeField, opt)}
                  >
                    <Text
                      style={
                        selected ? styles.optTextSelected : styles.optText
                      }
                    >
                      {opt}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          )}
        </View>
      ) : null}

      {patchError ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorTitle}>Something broke on our side</Text>
          <Text style={styles.errorBody}>{patchError}</Text>
          <Text style={styles.errorBody}>Your edit was rolled back.</Text>
        </View>
      ) : null}

      <Pressable
        style={({ pressed }) => [styles.exportBtn, pressed && styles.pressed]}
        onPress={() => nav.navigate("export")}
      >
        <Text style={styles.exportBtnText}>Export this item →</Text>
      </Pressable>
    </ScrollView>
  );
}

function labelFor(field: ActiveField): string {
  const e = ENUM_FIELDS.find((f) => f.key === field);
  if (e) return e.label;
  const m = MULTI_FIELDS.find((f) => f.key === field);
  return m ? m.label : "";
}

function isMulti(field: ActiveField): field is MultiKey {
  return field === "colors" || field === "material";
}

function optionsFor(field: ActiveField): readonly string[] {
  const e = ENUM_FIELDS.find((f) => f.key === field);
  if (e) return e.options;
  const m = MULTI_FIELDS.find((f) => f.key === field);
  return m ? m.options : [];
}

function Chip(props: {
  label: string;
  value: string;
  pct: number;
  needsReview: boolean;
  active: boolean;
  onPress: () => void;
}): JSX.Element {
  return (
    <Pressable
      style={({ pressed }) => [
        styles.chip,
        props.active && styles.chipActive,
        pressed && styles.pressed,
      ]}
      onPress={props.onPress}
    >
      <View style={styles.chipTop}>
        <Text style={styles.chipLabel}>{props.label}</Text>
        {props.needsReview ? (
          <Text style={styles.reviewTag}>review</Text>
        ) : null}
      </View>
      <Text style={styles.chipValue}>{props.value}</Text>
      <View style={styles.confBar}>
        <View style={[styles.confFill, { width: `${props.pct}%` }]} />
      </View>
    </Pressable>
  );
}

function DecisionButton(props: {
  label: string;
  active: boolean;
  onPress: () => void;
}): JSX.Element {
  return (
    <Pressable
      style={({ pressed }) => [
        styles.decision,
        props.active && styles.decisionActive,
        pressed && styles.pressed,
      ]}
      onPress={props.onPress}
    >
      <Text
        style={props.active ? styles.decisionTextActive : styles.decisionText}
      >
        {props.label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  body: {
    flex: 1,
  },
  controls: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#E6E3DD",
  },
  simLabel: {
    fontSize: 13,
    color: "#151515",
  },
  page: {
    flex: 1,
  },
  itemScroll: {
    flex: 1,
    padding: 16,
  },
  emptyWrap: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  empty: {
    fontSize: 15,
    color: "#6B6B6B",
    textAlign: "center",
  },
  images: {
    flexDirection: "row",
    gap: 12,
  },
  imageCol: {
    flex: 1,
  },
  imageBox: {
    height: 150,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#D8D6D1",
  },
  imageLabel: {
    marginTop: 4,
    fontSize: 12,
    color: "#6B6B6B",
    textAlign: "center",
  },
  decisions: {
    flexDirection: "row",
    gap: 8,
    marginTop: 16,
  },
  decision: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#D8D6D1",
    backgroundColor: "#FFFFFF",
    alignItems: "center",
  },
  decisionActive: {
    backgroundColor: "#151515",
    borderColor: "#151515",
  },
  decisionText: {
    fontSize: 13,
    color: "#151515",
  },
  decisionTextActive: {
    fontSize: 13,
    color: "#FFFFFF",
    fontWeight: "700",
  },
  decidedNote: {
    marginTop: 8,
    fontSize: 12,
    color: "#6B6B6B",
  },
  sectionTitle: {
    marginTop: 18,
    fontSize: 15,
    fontWeight: "700",
    color: "#151515",
  },
  chips: {
    marginTop: 8,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  chip: {
    width: "47%",
    backgroundColor: "#FFFFFF",
    borderWidth: 1,
    borderColor: "#E6E3DD",
    borderRadius: 10,
    padding: 10,
  },
  chipActive: {
    borderColor: "#151515",
  },
  chipTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  chipLabel: {
    fontSize: 12,
    color: "#6B6B6B",
  },
  reviewTag: {
    fontSize: 10,
    color: "#B00020",
    fontWeight: "700",
  },
  chipValue: {
    fontSize: 14,
    color: "#151515",
    fontWeight: "600",
    marginTop: 2,
    textTransform: "capitalize",
  },
  confBar: {
    marginTop: 6,
    height: 4,
    borderRadius: 2,
    backgroundColor: "#E6E3DD",
    overflow: "hidden",
  },
  confFill: {
    height: 4,
    backgroundColor: "#3FA66A",
  },
  editor: {
    marginTop: 12,
    padding: 12,
    backgroundColor: "#F7F5F1",
    borderRadius: 10,
  },
  editorTitle: {
    fontSize: 13,
    fontWeight: "700",
    color: "#151515",
    marginBottom: 8,
  },
  optionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
  },
  opt: {
    paddingVertical: 6,
    paddingHorizontal: 12,
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
    textTransform: "capitalize",
  },
  optTextSelected: {
    fontSize: 13,
    color: "#FFFFFF",
    fontWeight: "700",
    textTransform: "capitalize",
  },
  errorBox: {
    marginTop: 12,
    padding: 12,
    backgroundColor: "#FBE9EC",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#F0C2C9",
  },
  errorTitle: {
    fontSize: 14,
    fontWeight: "700",
    color: "#B00020",
  },
  errorBody: {
    fontSize: 13,
    color: "#6B6B6B",
    marginTop: 2,
  },
  exportBtn: {
    marginTop: 18,
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
  pressed: {
    opacity: 0.85,
  },
});
