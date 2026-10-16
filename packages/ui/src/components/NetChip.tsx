import React, { useEffect, useRef } from "react";
import { Animated, View, Text } from "react-native";
import { theme } from "../theme/tokens";
import type { HostReachability } from "@wc/core";

type NetChipProps = {
  state: HostReachability["state"];
  host: string;
};

export function NetChip({ state, host }: NetChipProps) {
  const pulse = useRef(new Animated.Value(1)).current;
  const failed = state === "unreachable";
  const reachable = state === "reachable";
  const color = failed ? theme.color.live : reachable ? theme.color.telemetry : theme.color.textMuted;
  const backgroundColor = failed ? theme.net.disconnected.chipBg
    : reachable ? theme.net.connected.chipBg : theme.color.surfaceRaised;
  const label = `LAN · ${host}`;
  useEffect(() => {
    if (!reachable) { pulse.setValue(1); return undefined; }
    const animation = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: .35, duration: 1000, useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 1, duration: 1000, useNativeDriver: true }),
    ]));
    animation.start();
    return () => animation.stop();
  }, [pulse, reachable]);
  if (!host) return null;
  return (
    <View accessible accessibilityLabel={`${label}, ${state}`}
      style={{ flexDirection: "row", alignItems: "center", gap: 7, paddingHorizontal: 12, paddingVertical: 8,
        backgroundColor, borderRadius: theme.radius.pill, maxWidth: "100%" }}>
      <Animated.View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color, opacity: pulse }} />
      <Text numberOfLines={1} style={{ flexShrink: 1, fontFamily: theme.font.mono, fontSize: 10, color }}>{label}</Text>
    </View>
  );
}
