import React from "react";
import { View, Text, Pressable } from "react-native";
import { theme } from "../theme/tokens";

type Tab = { key: string; label: string; hero?: boolean; disabled?: boolean; onPress?: () => void };

export function BottomNav({ active, onWindows, onStream, onSetup }:
  { active: "windows" | "stats" | "server" | "setup";
    onWindows: () => void; onStream: () => void; onSetup: () => void }) {
  const tabs: Tab[] = [
    { key: "windows", label: "Windows", onPress: onWindows },
    { key: "stats", label: "Stats", disabled: true },
    { key: "stream", label: "Stream", hero: true, onPress: onStream },
    { key: "server", label: "Server", disabled: true },
    { key: "setup", label: "Setup", onPress: onSetup },
  ];
  return (
    <View style={{ position: "absolute", left: 20, right: 20, bottom: 20, height: 74, flexDirection: "row",
      alignItems: "center", paddingHorizontal: 12, backgroundColor: theme.color.card, borderRadius: theme.radius.pill,
      shadowColor: "#1c1a19", shadowOpacity: 0.14, shadowRadius: 22, shadowOffset: { width: 0, height: 6 }, elevation: 8 }}>
      {tabs.map((t) => {
        const cur = t.key === active;
        const color = t.disabled ? "rgba(28,26,25,0.28)" : cur ? theme.color.text : "rgba(28,26,25,0.45)";
        return (
          <Pressable key={t.key} disabled={t.disabled} onPress={t.onPress}
            accessibilityLabel={t.label}
            style={{ flex: 1, height: 74, alignItems: "center", justifyContent: "center", gap: 6 }}>
            <View style={{ width: t.hero ? 50 : 26, height: t.hero ? 50 : 26, borderRadius: 999,
              backgroundColor: t.hero ? theme.color.accent : "transparent", alignItems: "center", justifyContent: "center" }}>
              {/* Icon placeholder dot; swap for the v3 react-native-svg tab glyph. */}
              <View style={{ width: 6, height: 6, borderRadius: 999, backgroundColor: t.hero ? theme.color.text : color }} />
            </View>
            {!t.hero ? <Text style={{ fontFamily: theme.font.semibold, fontSize: 10.5, color }}>{t.label}</Text> : null}
          </Pressable>
        );
      })}
    </View>
  );
}
