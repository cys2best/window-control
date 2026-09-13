import React, { useState } from "react";
import { View, Text, TextInput, KeyboardAvoidingView, Platform, Pressable, ScrollView } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { BrandMark } from "../components/BrandMark";
import { NetChip, RelayIdleChip } from "../components/NetChip";
import { useServer, signInWithPassword, signUpWithPassword } from "@wc/core";

export function Login({ navigation }: { navigation: any }) {
  const { base, setServer, supabaseUrl, supabaseAnonKey, hostReachability } = useServer();
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pairingCode, setPairingCode] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const clearFeedback = () => { setError(""); setNotice(""); };
  const submit = async () => {
    if (busy) return;
    clearFeedback();
    if (!supabaseUrl) {
      setError("Authentication is not configured on this server");
      return;
    }
    if (mode === "sign-up" && !pairingCode.trim()) {
      setError("Enter your host pairing code");
      return;
    }
    setBusy(true);
    try {
      const effectiveBase = base || (typeof window !== "undefined" && window.location?.origin ? window.location.origin : "");
      const result = mode === "sign-in"
        ? await signInWithPassword(supabaseUrl, supabaseAnonKey, email, password)
        : await signUpWithPassword(supabaseUrl, supabaseAnonKey, email, password, {
          redirectTo: effectiveBase ? `${effectiveBase}/login` : undefined,
          metadata: { host_pairing_code: pairingCode.trim() },
        });
      if ("error" in result) {
        setError(result.error);
        return;
      }
      if ("needs_confirmation" in result) {
        setNotice(result.message);
        return;
      }
      const authenticatedClient = await setServer(effectiveBase, result.access_token);
      await authenticatedClient.instances();
      navigation.replace("InstanceList");
    } catch (err: unknown) {
      setError(err && typeof err === "object" && "status" in err && err.status === 403
        ? "This host belongs to another account"
        : err instanceof Error ? err.message : "Unable to sign in. Please try again.");
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
  });
  const inputStyle = {
    paddingHorizontal: 16, fontFamily: theme.font.medium, fontSize: 15, color: theme.color.text,
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.color.screen }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ flexGrow: 1, padding: 24, paddingTop: 64 }}>
        <BrandMark />
        <Text style={{ fontFamily: theme.font.bold, fontSize: 29, letterSpacing: -0.6, color: theme.color.text, marginTop: 26 }}>
          {mode === "sign-in" ? "Welcome back" : "Make it yours"}
        </Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 13.5, lineHeight: 21, color: theme.color.textMuted, marginTop: 9, marginBottom: 22 }}>
          {mode === "sign-in" ? "Sign in to reach the instances on your EmuCtrl host." : "Create an account, then pair it with your EmuCtrl host."}
        </Text>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 22 }}>
          <NetChip route={hostReachability.route} state={hostReachability.state} host={hostReachability.host} />
          {hostReachability.route === "lan" ? <RelayIdleChip /> : null}
        </View>
        <View style={{ flexDirection: "row", padding: 4, backgroundColor: theme.color.surface, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, gap: 4, marginBottom: 20 }}>
          {(["sign-in", "sign-up"] as const).map((value) => (
            <Pressable key={value} accessibilityRole="tab" accessibilityState={{ selected: mode === value, disabled: busy }}
              disabled={busy} onPress={() => { setMode(value); setFocusedField(null); clearFeedback(); }}
              style={{ flex: 1, minHeight: 42, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.pill,
                backgroundColor: mode === value ? theme.color.accent : "transparent" }}>
              <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 10, letterSpacing: 0.55, color: mode === value ? theme.color.bg : theme.color.textMuted }}>
                {value === "sign-in" ? "SIGN IN" : "CREATE ACCOUNT"}
              </Text>
            </Pressable>
          ))}
        </View>
        <Text style={{ fontFamily: theme.font.mono, fontSize: 9.5, letterSpacing: 1.3, color: theme.color.textDim, marginBottom: 7 }}>EMAIL</Text>
        <TextInput value={email} onChangeText={(value) => { setEmail(value); clearFeedback(); }}
          accessibilityLabel="Email" placeholder="Email" placeholderTextColor={theme.color.textDim}
          autoCapitalize="none" autoCorrect={false} keyboardType="email-address" autoComplete="email" editable={!busy}
          onFocus={() => setFocusedField("email")} onBlur={() => setFocusedField(null)}
          style={{ ...fieldStyle("email"), ...inputStyle, marginBottom: 12 }} />
        <Text style={{ fontFamily: theme.font.mono, fontSize: 9.5, letterSpacing: 1.3, color: theme.color.textDim, marginBottom: 7 }}>PASSWORD</Text>
        <View style={{ ...fieldStyle("password"), flexDirection: "row", alignItems: "center" }}>
          <TextInput value={password} onChangeText={(value) => { setPassword(value); clearFeedback(); }}
            accessibilityLabel="Password" placeholder="Password" placeholderTextColor={theme.color.textDim}
            autoCapitalize="none" autoCorrect={false} secureTextEntry={!showPassword} editable={!busy}
            onFocus={() => setFocusedField("password")} onBlur={() => setFocusedField(null)}
            style={{ ...inputStyle, flex: 1, minWidth: 0, height: "100%" }} />
          <Pressable accessibilityRole="button" accessibilityLabel={showPassword ? "Hide password" : "Show password"}
            onPress={() => setShowPassword(!showPassword)} style={{ paddingHorizontal: 16, height: "100%", justifyContent: "center" }}>
            <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 11, color: theme.color.accent }}>{showPassword ? "HIDE" : "SHOW"}</Text>
          </Pressable>
        </View>
        {mode === "sign-up" ? (
          <View style={{ marginTop: 12 }}><Text style={{ fontFamily: theme.font.mono, fontSize: 9.5, letterSpacing: 1.3, color: theme.color.textDim, marginBottom: 7 }}>HOST PAIRING CODE</Text><TextInput value={pairingCode} onChangeText={(value) => { setPairingCode(value); clearFeedback(); }}
            accessibilityLabel="Host pairing code" placeholder="Host pairing code" placeholderTextColor={theme.color.textDim}
            autoCapitalize="characters" autoCorrect={false} editable={!busy}
            onFocus={() => setFocusedField("pairing")} onBlur={() => setFocusedField(null)}
            style={{ ...fieldStyle("pairing"), ...inputStyle, fontFamily: theme.font.mono, letterSpacing: 1.2 }} /></View>
        ) : null}
        <View style={{ marginTop: 18, padding: 13, backgroundColor: theme.color.surfaceRaised, borderWidth: 1, borderColor: theme.color.border, borderLeftWidth: 2, borderLeftColor: theme.color.live, borderRadius: 10 }}>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 12, lineHeight: 18, color: theme.color.textMuted }}>
            The first authenticated connection claims an unowned host. A claimed host only accepts its owner's account.
            {mode === "sign-up" ? " Your pairing code is saved with your account; it does not override host ownership." : ""}
          </Text>
        </View>
        {error ? <Text accessibilityRole="alert" style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.error, marginTop: 12 }}>{error}</Text> : null}
        {notice ? <Text accessibilityLiveRegion="polite" style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.telemetry, marginTop: 12 }}>{notice}</Text> : null}
        <View style={{ marginTop: 20 }}>
          <Button label={busy ? "Please wait…" : mode === "sign-in" ? "Sign in" : "Create account"}
            onPress={() => { void submit(); }} loading={busy} />
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
