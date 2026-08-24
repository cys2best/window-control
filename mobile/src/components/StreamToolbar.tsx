import React from "react";
import { View, Text } from "react-native";
import Svg, { Path, Circle, Rect } from "react-native-svg";
import { theme } from "../theme/tokens";
import { NetDot } from "./NetDot";
import { IconButton } from "./IconButton";

type Net = "connected" | "connecting" | "disconnected";
// Solid black, edge-to-edge full-height panel on the right; active button = coral fill.
export function StreamToolbar({ net, active, onSettings, onSwitch, onKeyboard, onStats, onBack }:
  { net: Net; active: { settings: boolean; drawer: boolean; keyboard: boolean; stats: boolean };
    onSettings: () => void; onSwitch: () => void; onKeyboard: () => void; onStats: () => void; onBack: () => void }) {
  const stroke = (on: boolean) => (on ? theme.color.text : "rgba(250,248,246,0.65)");
  return (
    <View collapsable={false} style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 64,
      backgroundColor: "#000", alignItems: "center", justifyContent: "space-between", paddingVertical: 18 }}>
      <View style={{ width: 48, height: 48, alignItems: "center", justifyContent: "center", gap: 4 }}>
        <NetDot state={net} />
        <Text style={{ fontFamily: theme.font.semibold, fontSize: 7.5, letterSpacing: 0.4, color: "rgba(250,248,246,0.5)" }}>
          {net === "connected" ? "LIVE" : net === "connecting" ? "SYNC" : "DOWN"}</Text>
      </View>
      <View style={{ alignItems: "center", gap: 6 }}>
        <IconButton label="Settings" active={active.settings} onPress={onSettings}>
          <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={stroke(active.settings)} strokeWidth={2}>
            <Circle cx={12} cy={12} r={3} />
            <Path d="M12 2.5a1.9 1.9 0 0 1 1.9 1.9v.4a1.6 1.6 0 0 0 2.4 1.4l.3-.2a1.9 1.9 0 0 1 1.9 3.3l-.3.2a1.6 1.6 0 0 0 0 2.8l.3.2a1.9 1.9 0 0 1-1.9 3.3l-.3-.2a1.6 1.6 0 0 0-2.4 1.4v.4a1.9 1.9 0 0 1-3.8 0v-.4a1.6 1.6 0 0 0-2.4-1.4l-.3.2a1.9 1.9 0 0 1-1.9-3.3l.3-.2a1.6 1.6 0 0 0 0-2.8l-.3-.2a1.9 1.9 0 0 1 1.9-3.3l.3.2A1.6 1.6 0 0 0 10.1 4.8v-.4A1.9 1.9 0 0 1 12 2.5z" />
          </Svg>
        </IconButton>
        <IconButton label="Switch instance" active={active.drawer} onPress={onSwitch}>
          <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={stroke(active.drawer)} strokeWidth={2}>
            <Path d="M4 6h16M4 12h16M4 18h16" />
          </Svg>
        </IconButton>
        <IconButton label="Keyboard" active={active.keyboard} onPress={onKeyboard}>
          <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={stroke(active.keyboard)} strokeWidth={2}>
            <Rect x={2} y={6} width={20} height={12} rx={3} />
            <Path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8" />
          </Svg>
        </IconButton>
        <IconButton label="Live stats" active={active.stats} onPress={onStats}>
          <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={stroke(active.stats)} strokeWidth={2}>
            <Path d="M3 12h4l2.5-6 4 12 2.5-6h5" />
          </Svg>
        </IconButton>
      </View>
      <IconButton label="Back" onPress={onBack}>
        <Svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="rgba(250,248,246,0.65)" strokeWidth={2}>
          <Path d="M19 12H5M11 18l-6-6 6-6" />
        </Svg>
      </IconButton>
    </View>
  );
}
