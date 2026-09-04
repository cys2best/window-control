import React, { useState } from "react";
import { View, Text, TextInput, KeyboardAvoidingView, Platform } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { useServer, signInWithPassword, signUpWithPassword } from "@wc/core";

export function Login({ navigation }: { navigation: any }) {
  const { base, setServer, supabaseUrl, supabaseAnonKey } = useServer() as any;
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy) return;
    setBusy(true); setError("");
    const authFn = mode === "sign-in" ? signInWithPassword : signUpWithPassword;
    const result = await authFn(supabaseUrl, supabaseAnonKey, email, password);
    setBusy(false);
    if ("error" in result) {
      setError(result.error);
      return;
    }
    await setServer(base, result.access_token);
    navigation.replace("InstanceList");
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <View style={{ flex: 1, backgroundColor: theme.color.screen, padding: 24, paddingTop: 80 }}>
        <Text style={{ fontFamily: theme.font.bold, fontSize: 27, color: theme.color.text, marginBottom: 20 }}>
          {mode === "sign-in" ? "Welcome back" : "Create account"}
        </Text>
        <TextInput value={email} onChangeText={(t) => { setEmail(t); setError(""); }}
          placeholder="Email" placeholderTextColor="rgba(28,26,25,0.35)"
          autoCapitalize="none" autoCorrect={false} keyboardType="email-address"
          style={{ height: 56, paddingHorizontal: 18, fontFamily: theme.font.medium, fontSize: 15,
            color: theme.color.text, backgroundColor: theme.color.card,
            borderWidth: 1.5, borderColor: error ? theme.color.error : "rgba(28,26,25,0.12)",
            borderRadius: theme.radius.input, marginBottom: 12 }} />
        <TextInput value={password} onChangeText={(t) => { setPassword(t); setError(""); }}
          placeholder="Password" placeholderTextColor="rgba(28,26,25,0.35)"
          autoCapitalize="none" autoCorrect={false} secureTextEntry
          style={{ height: 56, paddingHorizontal: 18, fontFamily: theme.font.medium, fontSize: 15,
            color: theme.color.text, backgroundColor: theme.color.card,
            borderWidth: 1.5, borderColor: error ? theme.color.error : "rgba(28,26,25,0.12)",
            borderRadius: theme.radius.input }} />
        {error ? (
          <Text style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.error, marginTop: 10 }}>
            {error}
          </Text>
        ) : null}
        <View style={{ marginTop: 16 }}>
          <Button label={busy ? "Please wait…" : (mode === "sign-in" ? "Sign in" : "Create account")}
            onPress={submit} loading={busy} />
        </View>
        <Text
          onPress={() => { setMode(mode === "sign-in" ? "sign-up" : "sign-in"); setError(""); }}
          style={{ fontFamily: theme.font.medium, fontSize: 13, color: theme.color.textMuted, marginTop: 16, textAlign: "center" }}>
          {mode === "sign-in" ? "Need an account? Register" : "Have an account? Sign in"}
        </Text>
      </View>
    </KeyboardAvoidingView>
  );
}
