import React from "react";
import { View } from "react-native";
import { theme } from "../theme/tokens";

export type NetState = "connected" | "connecting" | "disconnected";

export function NetDot({ state }: { state: NetState }) {
  return <View testID="net-dot"
    accessibilityValue={{ text: state }}
    style={{ width: 9, height: 9, borderRadius: 999, backgroundColor: theme.net[state].dot }} />;
}
