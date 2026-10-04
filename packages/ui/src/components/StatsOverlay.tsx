import React, { useEffect, useRef } from "react";
import { View, Text, Pressable, TextInput, ScrollView } from "react-native";
import type { StreamTelemetry } from "@wc/core";
import { theme } from "../theme/tokens";

const value = (n: number | null, suffix: string) => n === null ? "—" : `${n.toFixed(n % 1 ? 1 : 0)}${suffix}`;

export function StatsOverlay({ telemetry, onHeight, onExport, exportState = "idle", capacityText = "", onCapacityText, expectedRoute = "unknown", onExpectedRoute }: {
  telemetry: StreamTelemetry; onHeight?: (height: number) => void;
  onExport?: () => void; exportState?: "idle" | "sharing" | "failed";
  capacityText?: string; onCapacityText?: (text: string) => void;
  expectedRoute?: string; onExpectedRoute?: (route: string) => void;
}) {
  const previousDroppedFrames = useRef<number | null>(null);
  const droppedFramesIncreasing = previousDroppedFrames.current !== null
    && telemetry.droppedFrames !== null
    && telemetry.droppedFrames > previousDroppedFrames.current;
  const hasNetworkHealth = telemetry.loss !== null && telemetry.rttMs !== null;

  useEffect(() => {
    previousDroppedFrames.current = telemetry.droppedFrames;
  }, [telemetry.droppedFrames]);

  const unhealthy = telemetry.loss !== null && telemetry.loss > .08
    || telemetry.rttMs !== null && telemetry.rttMs > 60
    || droppedFramesIncreasing;
  const headroomColor = !hasNetworkHealth
    ? theme.color.textDim
    : unhealthy ? theme.color.live : theme.color.telemetry;
  const rows = [["DECODE", value(telemetry.decodeMs, " ms")], ["NETWORK", value(telemetry.networkMs, " ms")], ["INPUT→HOST", value(telemetry.inputMs, " ms")], ["JITTER", value(telemetry.jitterMs, " ms")], ["BITRATE", value(telemetry.bitrateMbps, " Mb/s")], ["DROPPED", telemetry.droppedFrames === null ? "—" : String(telemetry.droppedFrames)]];

  const dimensions = (w: number | null, h: number | null) => w === null || h === null ? "—" : `${w}×${h}`;
  rows.push(["ROUTE", telemetry.route === "direct" ? "Direct" : telemetry.route === "relay" ? "Relay" : "Unknown"],
    ["PATH", `${telemetry.addressFamily} / ${telemetry.relayProtocol}`],
    ["SOURCE", dimensions(telemetry.sourceWidth, telemetry.sourceHeight)],
    ["DECODED", dimensions(telemetry.decodedWidth, telemetry.decodedHeight)],
    ["FPS", value(telemetry.decodedFps, " fps")], ["FREEZE TOTAL", value(telemetry.totalFreezeSeconds, " s")], ["MAX FREEZE", value(telemetry.maxFreezeSeconds, " s")]);
  const expectedRoutes = ["unknown", "direct", "turn_udp", "turn_tcp", "turn_tls"];
  return <ScrollView testID="diagnostic-hud" contentContainerStyle={{ padding: 8 }} onLayout={(event) => onHeight?.(event.nativeEvent.layout.height)} style={{ position: "absolute", left: 0, top: 0, width: 68, maxHeight: "100%", backgroundColor: theme.color.glass, borderRightWidth: 1, borderBottomWidth: 1, borderColor: theme.color.border }}>
    <View testID="diagnostic-hud-headroom" style={{ height: 3, backgroundColor: headroomColor, marginBottom: 8 }} />
    {rows.map(([label, reading]) => <View key={label} style={{ marginBottom: 7 }}><Text style={{ color: theme.color.textDim, fontFamily: theme.font.mono, fontSize: 7 }}>{label}</Text><Text style={{ color: theme.color.text, fontFamily: theme.font.monoBold, fontSize: 8 }}>{reading}</Text></View>)}
    {onExport ? <View>
      <Text style={{ color: theme.color.textDim, fontSize: 7 }}>CAPACITY Mbps</Text>
      <TextInput accessibilityLabel="Independent capacity Mbps" keyboardType="decimal-pad" value={capacityText} onChangeText={onCapacityText} placeholder="Unknown" placeholderTextColor={theme.color.textDim} style={{ color: theme.color.text, fontSize: 9 }} />
      <Pressable accessibilityLabel="Expected media route" onPress={() => onExpectedRoute?.(expectedRoutes[(expectedRoutes.indexOf(expectedRoute) + 1) % expectedRoutes.length])}>
        <Text style={{ color: theme.color.textDim, fontSize: 7 }}>EXPECTED</Text><Text style={{ color: theme.color.text, fontSize: 9 }}>{expectedRoute}</Text>
      </Pressable>
      <Pressable accessibilityLabel="Export measurement JSON" disabled={exportState === "sharing"} onPress={onExport} style={{ paddingVertical: 8 }}>
        <Text style={{ color: theme.color.telemetry, fontSize: 9 }}>{exportState === "sharing" ? "Exporting…" : "Export JSON"}</Text>
      </Pressable>
      <Text style={{ color: theme.color.textDim, fontSize: 7 }}>{exportState === "failed" ? "Export failed" : "Missing data stays inconclusive"}</Text>
    </View> : null}
  </ScrollView>;
}
