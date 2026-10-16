import React, { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { TIER_ORDER, useServer, type QualitySelection } from "@wc/core";
import { Toggle } from "../components/Toggle";
import { theme } from "../theme/tokens";

const QUALITY_OPTIONS: QualitySelection[] = ["auto", ...TIER_ORDER];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={{ marginTop: 26 }}>
      <Text style={{ marginBottom: 8, fontFamily: theme.font.monoMedium, fontSize: 11, color: theme.color.textMuted }}>{title}</Text>
      <View style={{ borderTopWidth: 1, borderColor: theme.color.border }}>{children}</View>
    </View>
  );
}

function Row({ children, borderColor = theme.color.border }: { children: React.ReactNode; borderColor?: string }) {
  return <View style={{ minHeight: 56, justifyContent: "center", borderBottomWidth: 1, borderColor }}>{children}</View>;
}

export function Account({ navigation }: { navigation: any }) {
  const { authToken, hostReachability, preferences, updatePreferences, clearAuth } = useServer() as any;
  const [unpairing, setUnpairing] = useState(false);
  const host = hostReachability?.host || null;
  const quality = preferences?.quality ?? "auto";

  const selectNextQuality = () => {
    const index = QUALITY_OPTIONS.indexOf(quality);
    void updatePreferences({ quality: QUALITY_OPTIONS[(index + 1) % QUALITY_OPTIONS.length] });
  };
  const unpair = async () => {
    if (unpairing) return;
    setUnpairing(true);
    try {
      await clearAuth();
      navigation.replace("Pair");
    } finally {
      setUnpairing(false);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.screen }}>
      <ScrollView contentContainerStyle={{ padding: 24, paddingTop: 56, paddingBottom: 48 }}>
        <Text style={{ fontFamily: theme.font.semibold, fontSize: 22, letterSpacing: -0.2, color: theme.color.text, marginBottom: 2 }}>Settings</Text>

        <Section title="STREAM DEFAULTS">
          <Row>
            <Pressable accessibilityRole="button" accessibilityLabel="Stream quality" onPress={selectNextQuality} style={{ minHeight: 56, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14.5, color: theme.color.text }}>Default quality</Text>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><Text style={{ fontFamily: theme.font.monoMedium, fontSize: 12, color: theme.color.textMuted }}>{quality === "auto" ? "AUTO" : `${quality}P`}</Text><Text style={{ fontFamily: theme.font.mono, fontSize: 14, color: theme.color.textDim }}>›</Text></View>
            </Pressable>
          </Row>
          <Row><Toggle label="Show HUD on connect" value={Boolean(preferences?.showHudOnConnect)} onChange={(value) => void updatePreferences({ showHudOnConnect: value })} /></Row>
          <Row><Toggle label="Touch haptics" value={Boolean(preferences?.haptics)} onChange={(value) => void updatePreferences({ haptics: value })} /></Row>
        </Section>

        {host ? <Section title="HOST">
          <Row>
            <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 16 }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14.5, color: theme.color.text }}>Current host</Text>
              <Text numberOfLines={1} style={{ flexShrink: 1, fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{host}</Text>
            </View>
          </Row>
        </Section> : null}

        {/* The PC's own window is trusted without a token, so it has nothing to unpair. */}
        {authToken ? <Section title="THIS DEVICE">
          <Row borderColor={theme.color.live}>
            <Pressable accessibilityRole="button" accessibilityLabel="Unpair this device" disabled={unpairing} onPress={() => { void unpair(); }} style={{ minHeight: 56, justifyContent: "center" }}>
              <Text style={{ fontFamily: theme.font.semibold, fontSize: 14, color: theme.color.live }}>{unpairing ? "Unpairing…" : "Unpair this device"}</Text>
            </Pressable>
          </Row>
          <Text style={{ marginTop: 10, fontFamily: theme.font.regular, fontSize: 12, lineHeight: 18, color: theme.color.textMuted }}>Removes this device's saved access. To use it again, pair with a new code from the PC.</Text>
        </Section> : null}
      </ScrollView>
    </View>
  );
}
