import React from "react";
import { Pressable, Text, ActivityIndicator } from "react-native";
import { theme } from "../theme/tokens";

export function Button({ label, onPress, variant = "primary", loading, disabled }:
  { label: string; onPress: () => void; variant?: "primary" | "neutral"; loading?: boolean; disabled?: boolean }) {
  const primary = variant === "primary";
  return (
    <Pressable onPress={onPress} disabled={disabled || loading}
      style={{
        height: 54, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10,
        paddingHorizontal: 22, borderRadius: theme.radius.input,
        backgroundColor: primary ? theme.color.accent : theme.color.surfaceRaised,
        borderWidth: primary ? 0 : 1,
        borderColor: primary ? "transparent" : theme.color.border,
        shadowColor: primary ? theme.color.accent : "transparent",
        shadowOpacity: primary ? 0.36 : 0,
        shadowRadius: primary ? 12 : 0,
        shadowOffset: { width: 0, height: 4 },
        elevation: primary ? 6 : 0,
        opacity: disabled ? 0.45 : 1,
      }}>
      {loading ? <ActivityIndicator color={primary ? theme.color.bg : theme.color.text} /> : null}
      <Text style={{ fontFamily: theme.font.semibold, fontSize: 15, color: primary ? theme.color.bg : theme.color.text }}>{label}</Text>
    </Pressable>
  );
}
