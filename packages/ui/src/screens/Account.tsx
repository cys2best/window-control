import React, { useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useServer, type QualitySelection } from "@wc/core";
import { Toggle } from "../components/Toggle";
import { theme } from "../theme/tokens";

const QUALITY_OPTIONS: QualitySelection[] = ["auto", "480", "720", "1080", "1440"];

function hostFromBase(base: string | null): string {
  if (!base) return "Host unavailable";
  try {
    return new URL(base).host;
  } catch {
    return base;
  }
}

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
  const { identity, base, hostReachability, preferences, updatePreferences, clearAuth } = useServer() as any;
  const [signingOut, setSigningOut] = useState(false);
  const host = hostReachability?.host || hostFromBase(base);
  const quality = preferences?.quality ?? "auto";

  const selectNextQuality = () => {
    const index = QUALITY_OPTIONS.indexOf(quality);
    void updatePreferences({ quality: QUALITY_OPTIONS[(index + 1) % QUALITY_OPTIONS.length] });
  };
  const signOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await clearAuth();
      navigation.replace("Login");
    } finally {
      setSigningOut(false);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.screen }}>
      <ScrollView contentContainerStyle={{ padding: 24, paddingTop: 56, paddingBottom: 48 }}>
        <Text style={{ fontFamily: theme.font.bold, fontSize: 26, color: theme.color.text }}>Account</Text>
        <Section title="IDENTITY">
          <Row>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
              <View style={{ width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center", backgroundColor: theme.color.surfaceRaised }}>
                <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 18, color: theme.color.accent }}>{identity?.initials || "?"}</Text>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text numberOfLines={1} style={{ fontFamily: theme.font.semibold, fontSize: 17, color: theme.color.text }}>{identity?.displayName || "Signed in"}</Text>
                <Text numberOfLines={1} style={{ marginTop: 2, fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{identity?.email || ""}</Text>
              </View>
              <Pressable accessibilityRole="button" accessibilityLabel="Edit profile" disabled>
                <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 11, color: theme.color.textDim }}>EDIT</Text>
              </Pressable>
            </View>
          </Row>
        </Section>

        <Section title="STREAM DEFAULTS">
          <Row>
            <Pressable accessibilityRole="button" accessibilityLabel="Stream quality" onPress={selectNextQuality} style={{ minHeight: 56, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14, color: theme.color.text }}>Quality</Text>
              <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 12, color: theme.color.accent }}>{quality === "auto" ? "AUTO" : `${quality}P`}</Text>
            </Pressable>
          </Row>
          <Row><Toggle label="Show HUD on connect" value={Boolean(preferences?.showHudOnConnect)} onChange={(value) => void updatePreferences({ showHudOnConnect: value })} /></Row>
          <Row><Toggle label="Touch haptics" value={Boolean(preferences?.haptics)} onChange={(value) => void updatePreferences({ haptics: value })} /></Row>
          <Row><Toggle label="Hide rail while playing" value={Boolean(preferences?.hideRailWhilePlaying)} onChange={(value) => void updatePreferences({ hideRailWhilePlaying: value })} /></Row>
        </Section>

        <Section title="HOST & NETWORK">
          <Row>
            <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 16 }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14, color: theme.color.text }}>Current host</Text>
              <Text numberOfLines={1} style={{ flexShrink: 1, fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{host}</Text>
            </View>
          </Row>
          <Row>
            <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 16 }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14, color: theme.color.text }}>Connection route</Text>
              <Text style={{ fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{hostReachability?.route === "relay" ? "RELAY" : "LAN"}</Text>
            </View>
          </Row>
        </Section>

        <Section title="SESSION">
          <Row borderColor={theme.color.live}>
            <Pressable accessibilityRole="button" accessibilityLabel="Sign out on this device" disabled={signingOut} onPress={() => { void signOut(); }} style={{ minHeight: 56, justifyContent: "center" }}>
              <Text style={{ fontFamily: theme.font.semibold, fontSize: 14, color: theme.color.live }}>{signingOut ? "Signing out…" : "Sign out on this device"}</Text>
            </Pressable>
          </Row>
          <Text style={{ marginTop: 10, fontFamily: theme.font.regular, fontSize: 12, lineHeight: 18, color: theme.color.textMuted }}>The host stays claimed; only this device is signed out.</Text>
        </Section>
      </ScrollView>
    </View>
  );
}
