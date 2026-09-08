import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
import type { HostReachability } from "@wc/core";

type NetChipProps = {
  route: HostReachability["route"];
  state: HostReachability["state"] | "standby";
  host: string;
};

export function NetChip({ route, state, host }: NetChipProps) {
  if (!host) return null;
  const failedOrRelay = state === "unreachable" || (route === "relay" && state === "reachable");
  const reachableLan = route === "lan" && state === "reachable";
  const color = failedOrRelay ? theme.color.live : reachableLan ? theme.color.telemetry : theme.color.textMuted;
  const backgroundColor = failedOrRelay ? theme.net.disconnected.chipBg
    : reachableLan ? theme.net.connected.chipBg : theme.color.surfaceRaised;
  const label = `${route === "lan" ? "LAN" : "RELAY"} · ${host}`;
  return (
    <View accessible accessibilityLabel={`${label}, ${state}`}
      style={{ flexDirection: "row", alignItems: "center", gap: 7, paddingHorizontal: 12, paddingVertical: 8,
        backgroundColor, borderRadius: theme.radius.pill, maxWidth: "100%" }}>
      <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color }} />
      <Text numberOfLines={1} style={{ flexShrink: 1, fontFamily: theme.font.mono, fontSize: 10, color }}>{label}</Text>
    </View>
  );
}
