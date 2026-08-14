import React from "react";
import { View, Text, Pressable, Modal } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "./Button";

const PILLS: { label: string; tier: string }[] = [
  { label: "Auto", tier: "auto" }, { label: "480p", tier: "480" }, { label: "720p", tier: "720" },
  { label: "1080p", tier: "1080" }, { label: "1440p", tier: "1440" },
];

// v3: white rounded card, coral pill tiers, rounded stats-toggle row, coral Done.
export function SettingsModal({ tier, onPick, statsOn, onToggleStats, onClose }:
  { tier: string; onPick: (t: string) => void; statsOn: boolean; onToggleStats: () => void; onClose: () => void }) {
  return (
    <Modal transparent animationType="fade" onRequestClose={onClose}>
      <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: "rgba(20,17,16,0.55)", alignItems: "center", justifyContent: "center" }}>
        <Pressable onPress={() => {}} style={{ width: 440, maxWidth: "92%", backgroundColor: "#faf8f6", borderRadius: 26, padding: 22 }}>
          <Text style={{ fontFamily: theme.font.bold, fontSize: 19, color: theme.color.text, marginBottom: 18 }}>Settings</Text>
          <Text style={{ fontFamily: theme.font.semibold, fontSize: 12.5, color: theme.color.textMuted, marginBottom: 10 }}>Quality</Text>
          <View style={{ flexDirection: "row", gap: 7, marginBottom: 20 }}>
            {PILLS.map((p) => {
              const sel = p.tier === tier;
              return (
                <Pressable key={p.tier} onPress={() => onPick(p.tier)}
                  style={{ flex: 1, height: 42, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.pill,
                    backgroundColor: sel ? theme.color.accent : "transparent",
                    borderWidth: 1.5, borderColor: sel ? theme.color.accent : "rgba(28,26,25,0.15)" }}>
                  <Text style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.text }}>{p.label}</Text>
                </Pressable>
              );
            })}
          </View>
          <Pressable onPress={onToggleStats} style={{ flexDirection: "row", alignItems: "center", gap: 14, padding: 16, marginBottom: 20, backgroundColor: "#f2f0ed", borderRadius: 20 }}>
            <View style={{ flex: 1 }}>
              <Text style={{ fontFamily: theme.font.semibold, fontSize: 14.5, color: theme.color.text }}>Show live stats</Text>
              <Text style={{ fontFamily: theme.font.regular, fontSize: 12, color: theme.color.textMuted, marginTop: 4 }}>Bitrate, RTT and input latency over the stream.</Text>
            </View>
            <View style={{ width: 52, height: 30, borderRadius: theme.radius.pill, padding: 3,
              backgroundColor: statsOn ? theme.color.accent : "rgba(28,26,25,0.18)",
              justifyContent: "center", alignItems: statsOn ? "flex-end" : "flex-start" }}>
              <View style={{ width: 24, height: 24, borderRadius: theme.radius.pill, backgroundColor: "#fff" }} />
            </View>
          </Pressable>
          <Button label="Done" onPress={onClose} />
        </Pressable>
      </Pressable>
    </Modal>
  );
}
