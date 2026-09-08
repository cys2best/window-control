import React, { useEffect, useRef } from "react";
import { View, Text } from "react-native";
import type { StreamTelemetry } from "@wc/core";
import { theme } from "../theme/tokens";
import { SWAP_CONTROL_HALF_HEIGHT } from "./SwapControl";

const value = (n: number | null, suffix: string) => n === null ? "—" : `${n.toFixed(n % 1 ? 1 : 0)}${suffix}`;

export function StatsOverlay({ telemetry }: { telemetry: StreamTelemetry }) {
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

  return <View testID="diagnostic-hud" style={{ position: "absolute", left: 0, bottom: "50%" as any, marginBottom: SWAP_CONTROL_HALF_HEIGHT + 8, width: 68, padding: 8, backgroundColor: theme.color.glass, borderRightWidth: 1, borderColor: theme.color.border }}>
    <View testID="diagnostic-hud-headroom" style={{ height: 3, backgroundColor: headroomColor, marginBottom: 8 }} />
    {rows.map(([label, reading]) => <View key={label} style={{ marginBottom: 7 }}><Text style={{ color: theme.color.textDim, fontFamily: theme.font.mono, fontSize: 7 }}>{label}</Text><Text style={{ color: theme.color.text, fontFamily: theme.font.monoBold, fontSize: 8 }}>{reading}</Text></View>)}
  </View>;
}
