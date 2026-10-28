/**
 * SnapList — SCAFFOLD SCREEN ONLY.
 *
 * One screen that renders "SnapList" so `expo start` and `tsc --noEmit` both have something
 * real to chew on. The capture / review / attributes / on-model / export screens are the
 * mobile role's job (E6 in docs/PLAN.md). No API calls here: per prompts/05-mobile.md every
 * screen must render with mock data when API_BASE is unset.
 */
import { StatusBar } from "expo-status-bar";
import { StyleSheet, Text, View } from "react-native";

export default function App() {
  return (
    <View style={styles.screen}>
      <StatusBar style="dark" />
      <Text style={styles.wordmark} accessibilityRole="header">
        SnapList
      </Text>
      <Text style={styles.tagline}>Photo in, listing out.</Text>
      <Text style={styles.note}>
        Scaffold. Capture flow lands in E6 — see docs/PLAN.md.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#F2F1EE",
    padding: 24,
  },
  wordmark: {
    fontSize: 40,
    fontWeight: "700",
    letterSpacing: -1,
    color: "#151515",
  },
  tagline: {
    marginTop: 8,
    fontSize: 16,
    color: "#3A3A3A",
  },
  note: {
    marginTop: 24,
    fontSize: 13,
    color: "#6B6B6B",
    textAlign: "center",
  },
});
