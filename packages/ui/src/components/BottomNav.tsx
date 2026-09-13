import React from "react";
import { Platform, View, Text, Pressable, useWindowDimensions } from "react-native";
import { theme } from "../theme/tokens";

type Insets = { top: number; right: number; bottom: number; left: number };
const EMPTY_INSETS: Insets = { top: 0, right: 0, bottom: 0, left: 0 };
const EmptyInsetsContext = React.createContext<Insets>(EMPTY_INSETS);

export type BottomNavProps = {
  onInstances: () => void;
  onResume: () => void;
  onHealth: () => void;
};

export function BottomNav({ onInstances, onResume, onHealth }: BottomNavProps) {
  const { width } = useWindowDimensions();
  const SafeAreaInsetsContext: React.Context<Insets | null> = Platform.OS === "web"
    ? EmptyInsetsContext
    : require("react-native-safe-area-context").SafeAreaInsetsContext;
  const insets = React.useContext(SafeAreaInsetsContext) ?? EMPTY_INSETS;
  const position = Platform.OS === "web" && width >= 768
    ? { top: 24 }
    : { bottom: Platform.OS === "web" ? "calc(34px + env(safe-area-inset-bottom, 0px))" : insets.bottom + 34 };
  return (
    <View testID="navigation-capsule" style={{ position: "absolute", left: 16, right: 16, height: 60, flexDirection: "row",
      alignItems: "center", paddingHorizontal: 6, backgroundColor: "rgba(19,22,31,.82)", borderWidth: 1, borderColor: "#262b3c", borderRadius: theme.radius.pill, ...position } as any}>
      <Pressable accessibilityRole="button" accessibilityLabel="Instances" onPress={onInstances} style={{ flex: 1, height: 50, alignItems: "center", justifyContent: "center" }}>
        <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 8.5, letterSpacing: 1, color: theme.color.accent }}>INSTANCES</Text>
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Resume" onPress={onResume} style={{ width: 66, height: 48, borderRadius: 25, alignItems: "center", justifyContent: "center", backgroundColor: theme.color.accent, shadowColor: theme.color.accent, shadowOpacity: .35, shadowRadius: 13, elevation: 5 }}>
        <Text style={{ fontFamily: theme.font.monoBold, fontSize: 11, color: theme.color.bg }}>▶</Text>
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Health" onPress={onHealth} style={{ flex: 1, height: 50, alignItems: "center", justifyContent: "center" }}>
        <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 8.5, letterSpacing: 1, color: theme.color.textMuted }}>HEALTH</Text>
      </Pressable>
    </View>
  );
}
