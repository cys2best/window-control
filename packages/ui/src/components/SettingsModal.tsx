import React from "react";
import { View, Text, Pressable, Modal } from "react-native";
import { TIER_ORDER, type QualitySelection, type StreamPreferences } from "@wc/core";
import { theme } from "../theme/tokens";
import { Toggle } from "./Toggle";

export type SettingsModalProps = { preferences: StreamPreferences; onPickQuality: (tier: QualitySelection) => void; onPreferences: (patch: Partial<StreamPreferences>) => void; onClose: () => void };
export function SettingsModal({ preferences, onPickQuality, onPreferences, onClose }: SettingsModalProps) {
  return <Modal transparent animationType="fade" onRequestClose={onClose}><Pressable onPress={onClose} style={{ flex: 1, backgroundColor: "rgba(0,0,0,.65)", alignItems: "center", justifyContent: "center" }}><Pressable onPress={() => {}} style={{ width: 440, maxWidth: "92%", backgroundColor: theme.color.surfaceRaised, borderWidth: 1, borderColor: theme.color.border, padding: 20 }}>
    <Text style={{ color: theme.color.text, fontFamily: theme.font.monoBold, fontSize: 12, letterSpacing: 1 }}>STREAM SETTINGS</Text><Text style={{ color: theme.color.textDim, fontFamily: theme.font.mono, fontSize: 10, marginTop: 18, marginBottom: 8 }}>QUALITY</Text>
    <View style={{ flexDirection: "row", padding: 4, backgroundColor: theme.color.surface, borderWidth: 1, borderColor: theme.color.border }}>{(["auto", ...TIER_ORDER] as QualitySelection[]).map((tier) => <Pressable key={tier} onPress={() => onPickQuality(tier)} style={{ flex: 1, height: 38, alignItems: "center", justifyContent: "center", backgroundColor: preferences.quality === tier ? theme.color.accent : "transparent" }}><Text style={{ color: preferences.quality === tier ? theme.color.bg : theme.color.textMuted, fontFamily: theme.font.monoBold, fontSize: 10 }}>{tier === "auto" ? "Auto" : `${tier}p`}</Text></Pressable>)}</View>
    <View style={{ gap: 14, marginTop: 22 }}><Toggle label="Diagnostic HUD" value={preferences.showHudOnConnect} onChange={(value) => onPreferences({ showHudOnConnect: value })} /><Toggle label="Touch haptics" value={preferences.haptics} onChange={(value) => onPreferences({ haptics: value })} /></View>
    <Pressable accessibilityLabel="Close settings" onPress={onClose} style={{ alignSelf: "flex-end", marginTop: 22 }}><Text style={{ color: theme.color.accent, fontFamily: theme.font.monoBold, fontSize: 11 }}>DONE</Text></Pressable>
  </Pressable></Pressable></Modal>;
}
