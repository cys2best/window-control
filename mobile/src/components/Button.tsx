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
        paddingHorizontal: 22, borderRadius: theme.radius.pill,
        backgroundColor: primary ? theme.color.accent : "#f2f0ed",
        opacity: disabled ? 0.45 : 1,
      }}>
      {loading ? <ActivityIndicator color={theme.color.text} /> : null}
      <Text style={{ fontFamily: theme.font.semibold, fontSize: 15, color: theme.color.text }}>{label}</Text>
    </Pressable>
  );
}
