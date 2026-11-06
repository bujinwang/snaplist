/**
 * SnapList — mobile app shell.
 *
 * Routes across the full seller flow built in E6:
 *   Capture (T2/T6) → Review + Attributes (T3) → On-model (T4) → Export (T4)
 *   and a Drafts recovery surface (T5).
 *
 * Provider order matters: auth first (anonymous session + analytics subject),
 * then the drafts queue (the single source of truth for items), then navigation
 * (screens read both). Every screen renders fully with mock data when API_BASE
 * is unset, so design review needs no backend (PLAN.md E6, prompts/05-mobile.md).
 */
import { StatusBar } from "expo-status-bar";
import { Pressable, SafeAreaView, StyleSheet, Text, View } from "react-native";

import { AuthProvider, useAuth } from "./src/auth/AuthProvider";
import { DraftsProvider, useDrafts } from "./src/offline/DraftsProvider";
import { NavProvider, useNav, type Route } from "./src/navigation/NavContext";
import { CaptureScreen } from "./src/screens/Capture";
import { DraftsScreen } from "./src/screens/Drafts";
import { ExportScreen } from "./src/screens/Export";
import { OnModelScreen } from "./src/screens/OnModel";
import { ReviewScreen } from "./src/screens/Review";
import { hasApi } from "./src/lib/env";

const ROUTES: ReadonlyArray<{ route: Route; label: string }> = [
  { route: "capture", label: "Capture" },
  { route: "review", label: "Review" },
  { route: "onmodel", label: "On-model" },
  { route: "export", label: "Export" },
  { route: "drafts", label: "Drafts" },
];

function Header(): JSX.Element {
  const nav = useNav();
  const auth = useAuth();
  const drafts = useDrafts();

  return (
    <View style={styles.header}>
      <View style={styles.headerTop}>
        <Text style={styles.wordmark}>SnapList</Text>
        <Text style={styles.mode}>
          {hasApi ? "live" : "mock"} · {auth.isAnon ? "anon" : "signed in"} ·{" "}
          {drafts.isPro ? "Pro" : "Free"}
        </Text>
      </View>

      <View style={styles.nav}>
        {ROUTES.map((item) => {
          const active = nav.route === item.route;
          return (
            <Text
              key={item.route}
              style={active ? styles.navActive : styles.navItem}
              onPress={() => nav.navigate(item.route)}
            >
              {item.label}
            </Text>
          );
        })}
      </View>

      <View style={styles.toggles}>
        <Pressable
          onPress={() => drafts.setOnline(!drafts.online)}
          style={({ pressed }) => [styles.chip, pressed && styles.pressed]}
        >
          <Text style={styles.chipText}>{drafts.online ? "● Online" : "○ Offline"}</Text>
        </Pressable>
        <Pressable
          onPress={drafts.toggleSimulatePatchFailure}
          style={({ pressed }) => [
            styles.chip,
            drafts.simulatePatchFailure && styles.chipWarn,
            pressed && styles.pressed,
          ]}
        >
          <Text style={styles.chipText}>
            {drafts.simulatePatchFailure ? "Next PATCH fails: ON" : "Next PATCH fails: OFF"}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

function CurrentScreen(): JSX.Element {
  const nav = useNav();
  switch (nav.route) {
    case "capture":
      return <CaptureScreen />;
    case "review":
      return <ReviewScreen />;
    case "onmodel":
      return <OnModelScreen />;
    case "export":
      return <ExportScreen />;
    case "drafts":
      return <DraftsScreen />;
    default:
      return <CaptureScreen />;
  }
}

function Shell(): JSX.Element {
  const auth = useAuth();

  if (!auth.ready) {
    return (
      <View style={styles.center}>
        <Text style={styles.mode}>Signing in…</Text>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <Header />
      <View style={styles.content}>
        <CurrentScreen />
      </View>
    </View>
  );
}

export default function App(): JSX.Element {
  return (
    <AuthProvider>
      <DraftsProvider>
        <NavProvider>
          <SafeAreaView style={styles.screen}>
            <StatusBar style="dark" />
            <Shell />
          </SafeAreaView>
        </NavProvider>
      </DraftsProvider>
    </AuthProvider>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: "#F2F1EE",
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 8,
  },
  headerTop: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
  },
  wordmark: {
    fontSize: 24,
    fontWeight: "700",
    letterSpacing: -0.5,
    color: "#151515",
  },
  mode: {
    fontSize: 12,
    color: "#6B6B6B",
  },
  nav: {
    marginTop: 10,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 16,
  },
  navItem: {
    fontSize: 14,
    color: "#6B6B6B",
  },
  navActive: {
    fontSize: 14,
    fontWeight: "700",
    color: "#151515",
  },
  toggles: {
    marginTop: 10,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  chip: {
    backgroundColor: "#EDEBE6",
    borderRadius: 999,
    paddingVertical: 4,
    paddingHorizontal: 10,
  },
  chipWarn: {
    backgroundColor: "#FBE9E7",
  },
  chipText: {
    fontSize: 12,
    color: "#151515",
  },
  content: {
    flex: 1,
  },
  pressed: {
    opacity: 0.85,
  },
});
