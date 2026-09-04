import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
import { NetDot, NetState } from "./NetDot";

const LABEL: Record<NetState, string> = { connected: "Online", connecting: "Connecting", disconnected: "Offline" };

export function NetChip({ state }: { state: NetState }) {
  const c = theme.net[state];
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 7, paddingHorizontal: 13, paddingVertical: 8,
      backgroundColor: c.chipBg, borderRadius: theme.radius.pill }}>
      <NetDot state={state} />
      <Text style={{ fontFamily: theme.font.semibold, fontSize: 12, color: c.chipFg }}>{LABEL[state]}</Text>
    </View>
  );
}
