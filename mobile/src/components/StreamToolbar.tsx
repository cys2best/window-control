import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
import { NetDot } from "./NetDot";
import { IconButton } from "./IconButton";

type Net = "connected" | "connecting" | "disconnected";
// v3: white-glass panel on the right edge; active button = coral fill, ink glyphs.
export function StreamToolbar({ net, active, onSettings, onSwitch, onKeyboard, onStats, onBack }:
  { net: Net; active: { settings: boolean; drawer: boolean; keyboard: boolean; stats: boolean };
    onSettings: () => void; onSwitch: () => void; onKeyboard: () => void; onStats: () => void; onBack: () => void }) {
  const glyph = (on: boolean) => ({ fontFamily: theme.font.semibold, fontSize: 18, color: theme.color.text, opacity: on ? 1 : 0.85 });
  return (
    <View style={{ position: "absolute", top: 0, right: 0, bottom: 0, justifyContent: "center" }}>
      <View style={{ backgroundColor: theme.color.glass, padding: 6, alignItems: "center", gap: 2 }}>
        <View style={{ width: 48, height: 48, alignItems: "center", justifyContent: "center", gap: 4 }}>
          <NetDot state={net} />
          <Text style={{ fontFamily: theme.font.semibold, fontSize: 7.5, letterSpacing: 0.4, color: theme.color.textMuted }}>
            {net === "connected" ? "LIVE" : net === "connecting" ? "SYNC" : "DOWN"}</Text>
        </View>
        <IconButton label="Settings" active={active.settings} onPress={onSettings}><Text style={glyph(active.settings)}>⚙</Text></IconButton>
        <IconButton label="Switch instance" active={active.drawer} onPress={onSwitch}><Text style={glyph(active.drawer)}>≡</Text></IconButton>
        <IconButton label="Keyboard" active={active.keyboard} onPress={onKeyboard}><Text style={glyph(active.keyboard)}>⌨</Text></IconButton>
        <IconButton label="Live stats" active={active.stats} onPress={onStats}><Text style={glyph(active.stats)}>◔</Text></IconButton>
        <IconButton label="Back" onPress={onBack}><Text style={glyph(false)}>←</Text></IconButton>
      </View>
    </View>
  );
}
