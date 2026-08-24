import React from "react";
import { View, Text, Pressable } from "react-native";
import Svg, { Path } from "react-native-svg";
import { theme } from "../theme/tokens";

type Tab = { key: string; label: string; path: string; hero?: boolean; disabled?: boolean; onPress?: () => void };

export function BottomNav({ active, onWindows, onStream }:
  { active: "windows" | "stats" | "server" | "setup";
    onWindows: () => void; onStream: () => void }) {
  const tabs: Tab[] = [
    { key: "windows", label: "Windows", path: "M4 5h16v11H4zM9 20h6", onPress: onWindows },
    { key: "stats", label: "Stats", path: "M5 20V10M12 20V4M19 20v-7", disabled: true },
    { key: "stream", label: "Stream", path: "M9 7l9 5-9 5z", hero: true, onPress: onStream },
    { key: "server", label: "Server", path: "M4 5h16v6H4zM4 13h16v6H4M8 8h.01M8 16h.01", disabled: true },
    // Manual server entry is gone (auto-discovery only); this tab has no
    // destination screen any more until a real settings screen exists.
    { key: "setup", label: "Setup", path: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6M4 12h2M18 12h2M12 4v2M12 18v2", disabled: true },
  ];
  return (
    <View style={{ position: "absolute", left: 20, right: 20, bottom: 20, height: 74, flexDirection: "row",
      alignItems: "center", paddingHorizontal: 12, backgroundColor: theme.color.card, borderRadius: theme.radius.pill,
      shadowColor: "#1c1a19", shadowOpacity: 0.14, shadowRadius: 22, shadowOffset: { width: 0, height: 6 }, elevation: 8 }}>
      {tabs.map((t) => {
        const cur = t.key === active;
        const color = t.disabled ? "rgba(28,26,25,0.28)" : cur ? theme.color.text : "rgba(28,26,25,0.45)";
        const glyphColor = t.hero ? theme.color.text : color;
        return (
          <Pressable key={t.key} disabled={t.disabled} onPress={t.onPress}
            accessibilityLabel={t.label}
            style={{ flex: 1, height: 74, alignItems: "center", justifyContent: "center", gap: 6 }}>
            <View style={{ width: t.hero ? 50 : 26, height: t.hero ? 50 : 26, borderRadius: 999,
              backgroundColor: t.hero ? theme.color.accent : "transparent", alignItems: "center", justifyContent: "center" }}>
              <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={glyphColor} strokeWidth={2}>
                <Path d={t.path} />
              </Svg>
            </View>
            {!t.hero ? <Text style={{ fontFamily: theme.font.semibold, fontSize: 10.5, color }}>{t.label}</Text> : null}
          </Pressable>
        );
      })}
    </View>
  );
}
