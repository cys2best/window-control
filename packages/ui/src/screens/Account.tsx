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
  const { identity, hostReachability, preferences, updatePreferences, clearAuth } = useServer() as any;
  const [signingOut, setSigningOut] = useState(false);
  const host = hostReachability?.host || null;
  const route = hostReachability?.route || null;
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
        <Text style={{ fontFamily: theme.font.semibold, fontSize: 22, letterSpacing: -0.2, color: theme.color.text, marginBottom: 2 }}>Settings</Text>
        <Section title="IDENTITY">
          <Row>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
              <View style={{ width: 56, height: 56, borderRadius: 16, borderWidth: 1, borderColor: theme.color.border, alignItems: "center", justifyContent: "center", backgroundColor: theme.color.surfaceRaised }}>
                <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 18, color: theme.color.accent }}>{identity?.initials || "?"}</Text>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text numberOfLines={1} style={{ fontFamily: theme.font.semibold, fontSize: 17, color: theme.color.text }}>{identity?.displayName || "Signed in"}</Text>
                <Text numberOfLines={1} style={{ marginTop: 2, fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{identity?.email || ""}</Text>
              </View>
              <Pressable accessibilityRole="button" accessibilityLabel="Edit profile" disabled style={{ padding: 7 }}>
                <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 9.5, letterSpacing: 1.1, color: theme.color.accent }}>EDIT</Text>
              </Pressable>
            </View>
          </Row>
        </Section>

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

        {host || route ? <Section title="HOST & NETWORK">
          {host ? <Row>
            <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 16 }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14.5, color: theme.color.text }}>Current host</Text>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexShrink: 1 }}><Text numberOfLines={1} style={{ flexShrink: 1, fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{host}</Text><Text style={{ fontFamily: theme.font.mono, fontSize: 14, color: theme.color.textDim }}>›</Text></View>
            </View>
          </Row> : null}
          {route ? <Row>
            <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 16 }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14.5, color: theme.color.text }}>Connection route</Text>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><Text style={{ fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{route.toUpperCase()}</Text><Text style={{ fontFamily: theme.font.mono, fontSize: 14, color: theme.color.textDim }}>›</Text></View>
            </View>
          </Row> : null}
        </Section> : null}

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
