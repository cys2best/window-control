import React, { useEffect, useRef } from "react";
import { Animated, View, Text } from "react-native";
import { theme } from "../theme/tokens";
import type { HostReachability } from "@wc/core";

type NetChipProps = {
  route: HostReachability["route"];
  state: HostReachability["state"] | "standby";
  host: string;
};

export function NetChip({ route, state, host }: NetChipProps) {
  const pulse = useRef(new Animated.Value(1)).current;
  const failedOrRelay = state === "unreachable" || (route === "relay" && state === "reachable");
  const reachableLan = route === "lan" && state === "reachable";
  const color = failedOrRelay ? theme.color.live : reachableLan ? theme.color.telemetry : theme.color.textMuted;
  const backgroundColor = failedOrRelay ? theme.net.disconnected.chipBg
    : reachableLan ? theme.net.connected.chipBg : theme.color.surfaceRaised;
  const label = `${route === "lan" ? "LAN" : "RELAY"} · ${host}`;
  useEffect(() => {
    if (!reachableLan) { pulse.setValue(1); return undefined; }
    const animation = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: .35, duration: 1000, useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 1, duration: 1000, useNativeDriver: true }),
    ]));
    animation.start();
    return () => animation.stop();
  }, [pulse, reachableLan]);
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

/** The relay has no independently probeable endpoint before sign-in. Keep its
 * idle treatment explicit instead of inventing an address or latency. */
export function RelayIdleChip() {
  return (
    <View accessible accessibilityLabel="Relay idle"
      style={{ flexDirection: "row", alignItems: "center", gap: 7, paddingHorizontal: 12, paddingVertical: 8,
        backgroundColor: theme.color.surfaceRaised, borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radius.pill }}>
      <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: theme.color.textDim }} />
      <Text style={{ fontFamily: theme.font.mono, fontSize: 10, color: theme.color.textMuted }}>RELAY IDLE</Text>
    </View>
  );
}
