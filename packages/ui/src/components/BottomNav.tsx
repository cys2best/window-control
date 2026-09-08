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
    <View testID="navigation-capsule" style={{ position: "absolute", left: 20, right: 20, height: 60, flexDirection: "row",
      alignItems: "center", paddingHorizontal: 8, backgroundColor: "rgba(19,22,31,.82)", borderRadius: theme.radius.pill, ...position } as any}>
      <Pressable accessibilityRole="button" accessibilityLabel="Instances" onPress={onInstances} style={{ flex: 1, height: 50, alignItems: "center", justifyContent: "center" }}>
        <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 11, color: theme.color.textMuted }}>Instances</Text>
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Resume" onPress={onResume} style={{ width: 50, height: 50, borderRadius: 25, alignItems: "center", justifyContent: "center", backgroundColor: theme.color.accent }}>
        <Text style={{ fontFamily: theme.font.monoBold, fontSize: 11, color: theme.color.bg }}>▶</Text>
      </Pressable>
      <Pressable accessibilityRole="button" accessibilityLabel="Health" onPress={onHealth} style={{ flex: 1, height: 50, alignItems: "center", justifyContent: "center" }}>
        <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 11, color: theme.color.textMuted }}>Health</Text>
      </Pressable>
    </View>
  );
}
