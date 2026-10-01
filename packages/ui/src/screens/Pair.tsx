import React, { useState } from "react";
import { View, Text, TextInput, KeyboardAvoidingView, Platform, ScrollView } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { BrandMark } from "../components/BrandMark";
import { NetChip } from "../components/NetChip";
import { normalizeBase, pairDevice, useServer } from "@wc/core";

function deviceName(): string {
  if (Platform.OS === "ios") return "iPhone";
  if (Platform.OS === "android") return "Android";
  return "Browser";
}

export function Pair({ navigation }: { navigation: any }) {
  const { base, setServer, hostReachability } = useServer();
  // The web app is served by the host it talks to; a native app has to be
  // told where the host is.
  const needsHost = Platform.OS !== "web";
  const [host, setHost] = useState(base ?? "");
  const [code, setCode] = useState("");
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy) return;
    setError("");
    const origin = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";
    const entered = needsHost ? host.trim() : (base || origin);
    if (!entered) {
      setError("Enter your host address");
      return;
    }
    const digits = code.replace(/\s+/g, "");
    if (!digits) {
      setError("Enter the pairing code shown on your PC");
      return;
    }
    const target = normalizeBase(/^https?:\/\//.test(entered) ? entered : `http://${entered}`);
    setBusy(true);
    try {
      const result = await pairDevice(target, digits, deviceName());
      if ("error" in result) {
        setError(result.error);
        return;
      }
      await setServer(target, result.token);
      navigation.replace("InstanceList");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Pairing failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const fieldStyle = (field: string) => ({
    height: 50, backgroundColor: theme.color.surface, borderWidth: 1,
    borderColor: focusedField === field ? theme.color.accent : theme.color.border,
    borderRadius: theme.radius.input,
    shadowColor: theme.color.accent, shadowOpacity: focusedField === field ? 0.25 : 0,
    shadowRadius: 8, shadowOffset: { width: 0, height: 0 }, elevation: focusedField === field ? 4 : 0,
    paddingHorizontal: 16, fontSize: 15, color: theme.color.text,
  });
  const labelStyle = {
    fontFamily: theme.font.mono, fontSize: 9.5, letterSpacing: 1.3, color: theme.color.textDim, marginBottom: 7,
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.color.screen }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ flexGrow: 1, padding: 24, paddingTop: 64 }}>
        <BrandMark />
        <Text style={{ fontFamily: theme.font.bold, fontSize: 29, letterSpacing: -0.6, color: theme.color.text, marginTop: 26 }}>
          Pair this device
        </Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 13.5, lineHeight: 21, color: theme.color.textMuted, marginTop: 9, marginBottom: 22 }}>
          On your PC, open EmuCtrl Host and click Pair device. Enter the code it shows. You only do this once per device.
        </Text>
        {needsHost ? null : (
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 22 }}>
            <NetChip state={hostReachability.state} host={hostReachability.host} />
          </View>
        )}
        {needsHost ? (
          <View style={{ marginBottom: 12 }}>
            <Text style={labelStyle}>HOST ADDRESS</Text>
            <TextInput value={host} onChangeText={(value) => { setHost(value); setError(""); }}
              accessibilityLabel="Host address" placeholder="Host address" placeholderTextColor={theme.color.textDim}
              autoCapitalize="none" autoCorrect={false} keyboardType="url" editable={!busy}
              onFocus={() => setFocusedField("host")} onBlur={() => setFocusedField(null)}
              style={{ ...fieldStyle("host"), fontFamily: theme.font.mono }} />
          </View>
        ) : null}
        <Text style={labelStyle}>PAIRING CODE</Text>
        <TextInput value={code} onChangeText={(value) => { setCode(value); setError(""); }}
          accessibilityLabel="Pairing code" placeholder="Pairing code" placeholderTextColor={theme.color.textDim}
          autoCapitalize="none" autoCorrect={false} keyboardType="number-pad" maxLength={12} editable={!busy}
          onFocus={() => setFocusedField("code")} onBlur={() => setFocusedField(null)}
          onSubmitEditing={() => { void submit(); }}
          style={{ ...fieldStyle("code"), fontFamily: theme.font.mono, letterSpacing: 2 }} />
        {error ? <Text accessibilityRole="alert" style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.error, marginTop: 12 }}>{error}</Text> : null}
        <View style={{ marginTop: 20 }}>
          <Button label={busy ? "Please wait…" : "Pair"} onPress={() => { void submit(); }} loading={busy} />
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
