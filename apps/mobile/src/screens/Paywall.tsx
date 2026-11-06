/**
 * Pro paywall card (E6-T4), reused for the `upload_4th`, `on_model` and
 * `export_limit` triggers.
 *
 * Hard rules honoured here:
 * - The paywall states the plan AND the limit in the seller's own words; it never
 *   claims a feature the API would 402 on. (For v1, Pro = 400 items/month.)
 * - `paywall_variant` is SERVER-ASSIGNED and sticky in prod. There is no server in
 *   mock mode, so we derive a *deterministic* variant from a hash of the subject
 *   and document that honestly: the real variant must come from E8-T4, not here.
 * - Emits `paywall_impression` exactly once on show, with the trigger + variant.
 */

import React, { useEffect, useMemo } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useAuth } from "../auth/AuthProvider";
import { hashString } from "../lib/mockData";
import { PaywallTrigger, PaywallVariant, track } from "../lib/analytics";

interface PaywallCardProps {
  trigger: PaywallTrigger;
  planOffered?: "starter" | "pro";
  onUpgrade: (variant: PaywallVariant) => void;
  onDismiss?: () => void;
}

const COPY: Record<PaywallTrigger, { title: string; body: string }> = {
  upload_4th: {
    title: "You've shot your 3 free items",
    body: "SnapList Pro gives you 400 items a month — shoot your whole rack, not just a sample. Upgrade to keep capturing.",
  },
  on_model: {
    title: "On-model is a Pro feature",
    body: "SnapList Pro (400 items/month) puts this item on a model so buyers can see the fit. Upgrade to try it.",
  },
  export_limit: {
    title: "You've hit your free export limit",
    body: "SnapList Pro (400 items/month) lets you export your whole closet to every marketplace. Upgrade to keep exporting.",
  },
};

export function PaywallCard(props: PaywallCardProps): JSX.Element {
  const { trigger, planOffered = "pro", onUpgrade, onDismiss } = props;
  const auth = useAuth();

  // Deterministic, server-sticky-style variant derived from the subject. In prod
  // this MUST be replaced by the variant returned from the API (E8-T4). Documented.
  const variant: PaywallVariant = useMemo<PaywallVariant>(() => {
    const variants: readonly PaywallVariant[] = ["a", "b", "c"];
    const v = variants[hashString(auth.subject) % variants.length];
    return v ?? "a";
  }, [auth.subject]);

  useEffect(() => {
    track("paywall_impression", {
      paywall_variant: variant,
      trigger,
      plan_offered: planOffered,
    });
    // emit once per mount (one impression per show)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const copy = COPY[trigger];

  return (
    <View style={styles.card}>
      <Text style={styles.kicker}>Snaplist Pro</Text>
      <Text style={styles.title}>{copy.title}</Text>
      <Text style={styles.body}>{copy.body}</Text>
      <Text style={styles.plan}>
        Pro · 400 items/month · $49/mo
      </Text>
      <Pressable
        style={({ pressed }) => [styles.upgrade, pressed && styles.pressed]}
        onPress={() => onUpgrade(variant)}
      >
        <Text style={styles.upgradeText}>Upgrade to Pro</Text>
      </Pressable>
      {onDismiss ? (
        <Pressable style={styles.dismiss} onPress={onDismiss}>
          <Text style={styles.dismissText}>Maybe later</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#151515",
    borderRadius: 16,
    padding: 24,
    margin: 16,
  },
  kicker: {
    color: "#FFD479",
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  title: {
    color: "#FFFFFF",
    fontSize: 20,
    fontWeight: "700",
    marginTop: 8,
  },
  body: {
    color: "#E6E6E6",
    fontSize: 14,
    lineHeight: 20,
    marginTop: 8,
  },
  plan: {
    color: "#BDBDBD",
    fontSize: 13,
    marginTop: 12,
  },
  upgrade: {
    backgroundColor: "#FFD479",
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    marginTop: 16,
  },
  pressed: {
    opacity: 0.85,
  },
  upgradeText: {
    color: "#151515",
    fontSize: 15,
    fontWeight: "700",
  },
  dismiss: {
    alignItems: "center",
    marginTop: 10,
  },
  dismissText: {
    color: "#9A9A9A",
    fontSize: 13,
  },
});
