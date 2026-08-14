import React, { useState } from "react";
import { View, Text, TextInput, ScrollView } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { useServer } from "../api/ServerContext";

// v3: light warm ground, rounded hero, logo + wordmark, rounded input (coral
// caret, red border on error), rounded coral error card, "Start streaming" pill.
export function ServerSetup({ navigation }: { navigation: any }) {
  const { setBase } = useServer();
  const [url, setUrl] = useState("http://100.86.14.2:8080");
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [connecting, setConnecting] = useState(false);

  const connect = async () => {
    if (connecting) return;
    if (!/^https?:\/\/\S+/.test(url.trim())) {
      setError("Enter a full URL");
      setHint("Include the scheme and port, e.g. http://100.86.14.2:8080");
      return;
    }
    setConnecting(true); setError("");
    try {
      await setBase(url);
      // Probe reachability so an unreachable host surfaces here, not on the list.
      const r = await fetch(url.trim().replace(/\/+$/, "") + "/instances");
      if (!r.ok) throw new Error("bad status");
      navigation.replace("InstanceList");
    } catch {
      setError("Can't reach server");
      setHint("No response. Confirm the host is on the tailnet.");
    } finally {
      setConnecting(false);
    }
  };

  return (
    <ScrollView style={{ flex: 1, backgroundColor: theme.color.screen }}
      contentContainerStyle={{ padding: 24, paddingTop: 60, flexGrow: 1 }}>
      {/* Hero: a rounded warm placeholder. Swap for a real asset (assets/hero.png). */}
      <View style={{ flex: 1, minHeight: 180, borderRadius: 26, backgroundColor: "#e7e3de", marginBottom: 24 }} />
      <View style={{ flexDirection: "row", alignItems: "center", gap: 11, marginBottom: 18 }}>
        {/* Logo: a coral rounded square placeholder; replace with the v3 SVG mark (react-native-svg). */}
        <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: theme.color.accent }} />
        <Text style={{ fontFamily: theme.font.bold, fontSize: 19, letterSpacing: -0.4, color: theme.color.text }}>EmuCtrl</Text>
      </View>
      <Text style={{ fontFamily: theme.font.bold, fontSize: 27, color: theme.color.text, marginBottom: 8 }}>Control every window, from anywhere</Text>
      <Text style={{ fontFamily: theme.font.regular, fontSize: 14, lineHeight: 22, color: theme.color.textMuted, marginBottom: 20 }}>Stream and control your LDPlayer instances from anywhere on your private network.</Text>
      <Text style={{ fontFamily: theme.font.semibold, fontSize: 12, color: theme.color.textMuted, marginBottom: 8 }}>Server base URL</Text>
      <TextInput value={url} onChangeText={(t) => { setUrl(t); setError(""); }}
        placeholder="http://100.86.14.2:8080" placeholderTextColor="rgba(28,26,25,0.35)"
        autoCapitalize="none" autoCorrect={false} spellCheck={false}
        style={{ height: 56, paddingHorizontal: 18, fontFamily: theme.font.medium, fontSize: 15,
          color: theme.color.text, backgroundColor: theme.color.card,
          borderWidth: 1.5, borderColor: error ? theme.color.error : "rgba(28,26,25,0.12)",
          borderRadius: theme.radius.input }} />
      {error ? (
        <View style={{ flexDirection: "row", gap: 10, marginTop: 10, padding: 12, backgroundColor: theme.color.errorBg, borderRadius: theme.radius.sm }}>
          <View style={{ flex: 1 }}>
            <Text style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.error }}>{error}</Text>
            <Text style={{ fontFamily: theme.font.regular, fontSize: 12, color: theme.color.textMuted, marginTop: 3 }}>{hint}</Text>
          </View>
        </View>
      ) : null}
      <View style={{ marginTop: 16 }}>
        <Button label={connecting ? "Connecting…" : "Start streaming"} onPress={connect} loading={connecting} />
      </View>
    </ScrollView>
  );
}
