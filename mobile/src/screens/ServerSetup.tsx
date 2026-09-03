import React, { useState } from "react";
import { View, Text, TextInput, ScrollView, KeyboardAvoidingView, Platform, Image } from "react-native";
import Svg, { Rect, Path, Circle } from "react-native-svg";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { useServer } from "../api/ServerContext";
import { normalizeBase } from "../api/urls";

// v3: light warm ground, rounded hero, logo + wordmark, rounded input (coral
// caret, red border on error), rounded coral error card, "Start streaming" pill.
export function ServerSetup({ navigation }: { navigation: any }) {
  const { setServer } = useServer();
  const [url, setUrl] = useState("http://100.77.31.86:8080");
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
      // No token yet — Login collects credentials next.
      await setServer(url, "");
      // Lightweight reachability check that doesn't require auth.
      const r = await fetch(`${normalizeBase(url)}/auth/config`);
      if (!r.ok) throw new Error("unreachable");
      navigation.replace("Login");
    } catch (e: any) {
      setError("Can't reach server");
      setHint("No response. Confirm the host is on the tailnet.");
    } finally {
      setConnecting(false);
    }
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView style={{ flex: 1, backgroundColor: theme.color.screen }}
        contentContainerStyle={{ padding: 24, paddingTop: 60, flexGrow: 1 }}
        keyboardShouldPersistTaps="handled">
        <Image source={require("../../assets/hero.png")} resizeMode="cover"
          style={{ flex: 1, minHeight: 180, borderRadius: 26, backgroundColor: "#e7e3de", marginBottom: 24 }} />
        <View style={{ flexDirection: "row", alignItems: "center", gap: 11, marginBottom: 18 }}>
          <Svg width={40} height={40} viewBox="0 0 40 40" aria-label="EmuCtrl">
            <Rect width={40} height={40} rx={12} fill={theme.color.accent} />
            <Path d="M13.2 10.4 15.4 14M26.8 10.4 24.6 14" stroke={theme.color.text} strokeWidth={2} strokeLinecap="round" />
            <Path d="M11 22.6a9 9 0 0 1 18 0z" fill={theme.color.text} />
            <Circle cx={16.4} cy={18.6} r={1.25} fill={theme.color.accent} />
            <Circle cx={23.6} cy={18.6} r={1.25} fill={theme.color.accent} />
            <Rect x={11} y={24.6} width={18} height={5.6} rx={2.6} fill={theme.color.text} />
            <Path d="M22.6 21.8 32.8 27l-4.2 1.1-1.1 4.2z" fill={theme.color.text} stroke={theme.color.accent} strokeWidth={1.6} strokeLinejoin="round" />
          </Svg>
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
    </KeyboardAvoidingView>
  );
}
