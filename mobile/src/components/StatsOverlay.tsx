import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
export function StatsOverlay({ lines }: { lines: string }) {
  return (
    <View style={{ position: "absolute", top: 16, left: 18, padding: 13, borderRadius: theme.radius.input,
      backgroundColor: theme.color.glass }}>
      <Text style={{ fontFamily: theme.font.mono, fontSize: 10.5, lineHeight: 19, color: theme.color.text }}>{lines}</Text>
    </View>
  );
}
