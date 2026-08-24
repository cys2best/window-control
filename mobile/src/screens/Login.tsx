import React, { useState } from "react";
import { View, Text, TextInput, KeyboardAvoidingView, Platform } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { useServer } from "../api/ServerContext";

export function Login({ navigation }: { navigation: any }) {
  const { client } = useServer();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  const login = async () => {
    if (loggingIn || !client) return;
    setLoggingIn(true); setError("");
    try {
      const ok = await client.login(token);
      if (!ok) throw new Error("rejected");
      navigation.replace("InstanceList");
    } catch {
      setError("Invalid token");
    } finally {
      setLoggingIn(false);
    }
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <View style={{ flex: 1, backgroundColor: theme.color.screen, padding: 24,
        paddingTop: 100, justifyContent: "flex-start" }}>
        <Text style={{ fontFamily: theme.font.bold, fontSize: 27, color: theme.color.text,
          marginBottom: 8 }}>
          Sign in
        </Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 14, lineHeight: 22,
          color: theme.color.textMuted, marginBottom: 24 }}>
          This server requires a token to connect.
        </Text>
        <Text style={{ fontFamily: theme.font.semibold, fontSize: 12, color: theme.color.textMuted,
          marginBottom: 8 }}>
          Access token
        </Text>
        <TextInput value={token} onChangeText={(t) => { setToken(t); setError(""); }}
          placeholder="Enter access token" placeholderTextColor="rgba(28,26,25,0.35)"
          secureTextEntry autoCapitalize="none" autoCorrect={false} spellCheck={false}
          style={{ height: 56, paddingHorizontal: 18, fontFamily: theme.font.medium, fontSize: 15,
            color: theme.color.text, backgroundColor: theme.color.card,
            borderWidth: 1.5, borderColor: error ? theme.color.error : "rgba(28,26,25,0.12)",
            borderRadius: theme.radius.input }} />
        {error ? (
          <View style={{ marginTop: 10, padding: 12, backgroundColor: theme.color.errorBg,
            borderRadius: theme.radius.sm }}>
            <Text style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.error }}>
              {error}
            </Text>
          </View>
        ) : null}
        <View style={{ marginTop: 16 }}>
          <Button label={loggingIn ? "Signing in…" : "Sign in"} onPress={login} loading={loggingIn} />
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}
