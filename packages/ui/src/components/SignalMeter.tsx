import React, { useEffect, useRef } from "react";
import { Animated, Pressable, Text, View } from "react-native";
import { signalLevel, type StreamTelemetry } from "@wc/core";
import { theme } from "../theme/tokens";

type SignalMeterProps = {
  telemetry: StreamTelemetry;
  connected: boolean;
  onPress: () => void;
};

const barHeights = [9, 12.5, 16, 19];
const toneColor = {
  mint: theme.color.telemetry,
  amber: theme.color.warning,
  tangerine: theme.color.live,
} as const;

export function SignalMeter({ telemetry, connected, onPress }: SignalMeterProps) {
  const level = signalLevel(telemetry, connected);
  const disconnectedOpacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (!connected) {
      const pulse = Animated.loop(Animated.sequence([
        Animated.timing(disconnectedOpacity, { toValue: 0.25, duration: 700, useNativeDriver: false }),
        Animated.timing(disconnectedOpacity, { toValue: 1, duration: 700, useNativeDriver: false }),
      ]));
      pulse.start();
      return () => pulse.stop();
    }
    disconnectedOpacity.setValue(1);
    return undefined;
  }, [connected, disconnectedOpacity]);

  return (
    <Pressable accessible accessibilityRole="button" accessibilityLabel="Network diagnostics" onPress={onPress}
      style={{ width: 68, paddingTop: 11, paddingBottom: 10, alignItems: "center", gap: 4,
        borderBottomWidth: 1, borderBottomColor: "rgba(255,255,255,0.10)" }}>
      <View style={{ height: 19, flexDirection: "row", alignItems: "flex-end", gap: 2 }}>
        {barHeights.map((height, index) => {
          const filled = index < level.bars;
          const style = {
            width: 3, height, borderRadius: 1,
            backgroundColor: filled ? toneColor[level.tone] : "rgba(230,234,242,0.18)",
          };
          return !connected && index === 0
            ? <Animated.View key={height} testID="signal-bar" style={[style, { opacity: disconnectedOpacity }]} />
            : <View key={height} testID="signal-bar" style={style} />;
        })}
      </View>
      <Text style={{ fontFamily: theme.font.mono, fontSize: 7.5, letterSpacing: 0.6, color: theme.color.textDim }}>
        {telemetry.rttMs === null ? "—" : `${Math.round(telemetry.rttMs)}ms`}
      </Text>
    </Pressable>
  );
}
