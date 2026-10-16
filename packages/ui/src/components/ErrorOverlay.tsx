import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "./Button";
export function ErrorOverlay({ onReconnect, onBack, reconnecting }:
  { onReconnect: () => void; onBack: () => void; reconnecting: boolean }) {
  return (
    <View style={{ position: "absolute", inset: 0 as any, backgroundColor: theme.color.streamBg, alignItems: "center", justifyContent: "center", padding: 24 }}>
      <View style={{ width: 400, maxWidth: "94%", backgroundColor: "#faf8f6", borderRadius: 26, padding: 26, alignItems: "center" }}>
        <View style={{ width: 52, height: 52, borderRadius: theme.radius.pill, backgroundColor: theme.color.errorBg, marginBottom: 16 }} />
        <Text style={{ fontFamily: theme.font.bold, fontSize: 20, color: theme.color.text, marginBottom: 8 }}>Stream unavailable</Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 13, lineHeight: 21, color: theme.color.textMuted, textAlign: "center", marginBottom: 20 }}>
          The WebRTC session dropped. Check that the host is awake and still on the tailnet.</Text>
        <View style={{ flexDirection: "row", gap: 8, alignSelf: "stretch" }}>
          <View style={{ flex: 1 }}>
            <Button label={reconnecting ? "Reconnecting…" : "Reconnect"} onPress={onReconnect} loading={reconnecting} />
          </View>
          <Button label="Windows" variant="neutral" onPress={onBack} />
        </View>
      </View>
    </View>
  );
}
