import React from "react";
import { View, Text, Pressable, ScrollView } from "react-native";
import { theme } from "../theme/tokens";
import type { Instance } from "../api/client";
// v3: floating rounded white card (not a full-height ink drawer).
export function SwitchDrawer({ instances, activeSerial, onPick, onClose }:
  { instances: Instance[]; activeSerial: string; onPick: (i: Instance) => void; onClose: () => void }) {
  return (
    <View style={{ position: "absolute", inset: 0 as any }}>
      <Pressable onPress={onClose} style={{ position: "absolute", inset: 0 as any, backgroundColor: "rgba(20,17,16,0.55)" }} />
      <View style={{ position: "absolute", top: 14, left: 14, bottom: 14, width: 300, backgroundColor: "#faf8f6", borderRadius: 24, overflow: "hidden" }}>
        <View style={{ flexDirection: "row", alignItems: "center", padding: 16 }}>
          <Text style={{ flex: 1, fontFamily: theme.font.bold, fontSize: 17, color: theme.color.text }}>Instances</Text>
          <Pressable onPress={onClose} accessibilityLabel="Close"
            style={{ width: 34, height: 34, borderRadius: theme.radius.pill, backgroundColor: "#f2f0ed", alignItems: "center", justifyContent: "center" }}>
            <Text style={{ fontFamily: theme.font.semibold, color: "rgba(28,26,25,0.6)" }}>✕</Text>
          </Pressable>
        </View>
        <ScrollView contentContainerStyle={{ padding: 12, paddingTop: 0, gap: 4 }}>
          {instances.map((i) => {
            const act = i.serial === activeSerial;
            return (
              <Pressable key={i.id} onPress={() => onPick(i)}
                style={{ flexDirection: "row", alignItems: "center", gap: 12, padding: 12, borderRadius: theme.radius.sm,
                  backgroundColor: act ? theme.color.cardActive : "transparent" }}>
                <View style={{ width: 34, height: 34, borderRadius: 12, backgroundColor: act ? theme.color.accent : "#f2f0ed" }} />
                <View style={{ flex: 1, minWidth: 0 }}>
                  <Text style={{ fontFamily: theme.font.semibold, fontSize: 14, color: theme.color.text }}>{i.title}</Text>
                  {i.w && i.h ? <Text style={{ fontFamily: theme.font.regular, fontSize: 11.5, color: theme.color.textMuted, marginTop: 3 }}>{i.w}×{i.h}</Text> : null}
                </View>
                {act ? (
                  <View style={{ paddingHorizontal: 8, paddingVertical: 3, backgroundColor: theme.color.accent, borderRadius: theme.radius.pill }}>
                    <Text style={{ fontFamily: theme.font.semibold, fontSize: 10, color: theme.color.text }}>LIVE</Text>
                  </View>
                ) : null}
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
    </View>
  );
}
