import React from "react";
import { Pressable } from "react-native";
import { theme } from "../theme/tokens";

export function IconButton({ children, onPress, active, label }:
  { children: React.ReactNode; onPress: () => void; active?: boolean; label: string }) {
  return (
    <Pressable onPress={onPress} accessibilityLabel={label}
      style={{ width: 48, height: 48, alignItems: "center", justifyContent: "center",
        backgroundColor: active ? theme.color.accent : "transparent", borderRadius: theme.radius.sm }}>
      {children}
    </Pressable>
  );
}
