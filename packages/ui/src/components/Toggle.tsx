import React from "react";
import { Pressable, Text, View } from "react-native";
import { theme } from "../theme/tokens";

export function Toggle({ value, onChange, label }: { value: boolean; onChange: (value: boolean) => void; label: string }) {
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityState={{ checked: value }}
      accessibilityLabel={label}
      onPress={() => onChange(!value)}
      style={{ flexDirection: "row", alignItems: "center", gap: 10 }}
    >
      <View style={{
        width: 44,
        height: 26,
        padding: 3,
        borderRadius: 13,
        justifyContent: "center",
        alignItems: value ? "flex-end" : "flex-start",
        backgroundColor: value ? theme.color.accent : theme.color.surfaceRaised,
        borderWidth: value ? 0 : 1,
        borderColor: theme.color.border,
      }}>
        <View style={{ width: 20, height: 20, borderRadius: 10, backgroundColor: value ? theme.color.bg : theme.color.textMuted }} />
      </View>
      <Text style={{ color: theme.color.text, fontFamily: theme.font.medium, fontSize: 14 }}>{label}</Text>
    </Pressable>
  );
}
