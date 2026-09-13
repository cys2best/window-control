import React from "react";
import { Pressable, Text, View } from "react-native";
import Svg, { Circle, Path, Rect } from "react-native-svg";
import type { StreamTelemetry } from "@wc/core";
import { theme } from "../theme/tokens";
import { SignalMeter } from "./SignalMeter";

export const STREAM_RAIL_WIDTH = 68;

export type StreamRailProps = {
  visible: boolean;
  telemetry: StreamTelemetry;
  connected: boolean;
  keyboardOn: boolean;
  settingsOn: boolean;
  onDiagnostics: () => void;
  onKeyboard: () => void;
  onSystemKey: (key: "Home" | "AppSwitch") => void;
  onSettings: () => void;
  onExit: () => void;
  onWake: () => void;
  tick: () => void;
};

function RailKey({ label, active, onPress, children, testID = "rail-key" }: { label: string; active?: boolean; onPress: () => void; children: React.ReactNode; testID?: string }) {
  return <Pressable testID={testID} accessibilityRole="button" accessibilityLabel={label}
    onPress={() => { onPress(); }}
    style={({ pressed }) => ({ width: 52, height: 52, alignItems: "center", justifyContent: "center",
      backgroundColor: pressed ? "rgba(255,87,34,0.18)" : active ? "rgba(0,229,255,0.13)" : "transparent",
      borderWidth: active ? 1 : 0, borderColor: theme.color.accent })}>
    {children}
  </Pressable>;
}

const iconStroke = "rgba(230,234,242,0.75)";

export function StreamRail(props: StreamRailProps) {
  const action = (fn: () => void) => () => { props.onWake(); props.tick(); fn(); };
  if (!props.visible) return null;
  return <View testID="stream-rail" style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: STREAM_RAIL_WIDTH,
    backgroundColor: theme.color.glass, borderLeftWidth: 1, borderLeftColor: "rgba(255,255,255,0.10)", alignItems: "center" }}>
    <SignalMeter telemetry={props.telemetry} connected={props.connected} onPress={action(props.onDiagnostics)} />
    <View style={{ flex: 1, justifyContent: "center", gap: 8 }}>
      <RailKey label="Virtual keyboard" active={props.keyboardOn} onPress={action(props.onKeyboard)}>
        <Svg width={21} height={21} viewBox="0 0 24 24" fill="none" stroke={iconStroke} strokeWidth={1.7}><Rect x={2} y={6} width={20} height={12} rx={1}/><Path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8" /></Svg>
      </RailKey>
      <RailKey label="Android home" onPress={action(() => props.onSystemKey("Home"))}>
        <Svg width={21} height={21} viewBox="0 0 24 24" fill="none" stroke={iconStroke} strokeWidth={1.7}><Path d="M4 11.5 12 5l8 6.5v8H4z" /><Path d="M9 19v-5h6v5" /></Svg>
      </RailKey>
      <RailKey label="Recent apps" onPress={action(() => props.onSystemKey("AppSwitch"))}>
        <Svg width={21} height={21} viewBox="0 0 24 24" fill="none" stroke={iconStroke} strokeWidth={1.7}><Rect x={5} y={4} width={14} height={16} /><Path d="M9 8h6M9 12h6" /></Svg>
      </RailKey>
      <RailKey label="Stream settings" active={props.settingsOn} onPress={action(props.onSettings)}>
        <Svg width={21} height={21} viewBox="0 0 24 24" fill="none" stroke={iconStroke} strokeWidth={1.7}><Circle cx={12} cy={12} r={3}/><Path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /></Svg>
      </RailKey>
    </View>
    <View style={{ borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.10)", paddingVertical: 8 }}>
      <RailKey testID="rail-exit" label="Exit stream" onPress={action(props.onExit)}>
        <View style={{ alignItems: "center", gap: 2 }}><Text style={{ color: iconStroke, fontSize: 16, lineHeight: 16 }}>×</Text><Text style={{ color: iconStroke, fontFamily: theme.font.monoBold, fontSize: 7, letterSpacing: 0.8 }}>EXIT</Text></View>
      </RailKey>
    </View>
  </View>;
}
