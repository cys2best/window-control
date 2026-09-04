import React from "react";
import { Pressable, View, Text, Image } from "react-native";
import { theme } from "../theme/tokens";
import type { Instance } from "@wc/core";

export function InstanceRow({ instance, active, previewSource, onPress }:
  { instance: Instance; active: boolean; previewSource: { uri: string; headers?: { Authorization: string } }; onPress: () => void }) {
  return (
    <Pressable onPress={onPress}
      style={{ padding: 8, marginBottom: 10, backgroundColor: active ? theme.color.cardActive : theme.color.card,
        borderRadius: theme.radius.card, borderWidth: 1.5, borderColor: active ? theme.color.accent : "transparent",
        shadowColor: "#1c1a19", shadowOpacity: 0.05, shadowRadius: 5, shadowOffset: { width: 0, height: 1 }, elevation: 1 }}>
      <View style={{ aspectRatio: 16 / 9, borderRadius: theme.radius.sm, overflow: "hidden", backgroundColor: "#e7e3de" }}>
        <Image source={previewSource} resizeMode="cover" style={{ width: "100%", height: "100%" }} />
      </View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 8, paddingTop: 12, paddingBottom: 6 }}>
        <Text style={{ flex: 1, fontFamily: theme.font.semibold, fontSize: 15, color: theme.color.text }}>{instance.title}</Text>
        {active ? (
          <View style={{ paddingHorizontal: 9, paddingVertical: 4, backgroundColor: theme.color.accent, borderRadius: theme.radius.pill }}>
            <Text style={{ fontFamily: theme.font.semibold, fontSize: 10, color: theme.color.text }}>LIVE</Text>
          </View>
        ) : null}
        {/* Chevron placeholder; swap for the v3 react-native-svg chevron. */}
        <Text style={{ fontFamily: theme.font.regular, fontSize: 16, color: "rgba(28,26,25,0.35)" }}>›</Text>
      </View>
    </Pressable>
  );
}
