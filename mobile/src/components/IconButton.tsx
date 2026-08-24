import React from "react";
import { View } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { theme } from "../theme/tokens";

// Tap is implemented with react-native-gesture-handler (not Pressable) so it
// shares an arena with sibling RNGH gestures (e.g. the toolbar swipe) — a
// plain Pressable's RN-responder touch claim wins before RNGH can resolve
// which gesture the touch belongs to, silently eating swipes that start on
// a button.
export function IconButton({ children, onPress, active, label }:
  { children: React.ReactNode; onPress: () => void; active?: boolean; label: string }) {
  const tap = Gesture.Tap().runOnJS(true).onEnd(() => onPress());
  return (
    <GestureDetector gesture={tap}>
      <View collapsable={false} accessibilityLabel={label}
        style={{ width: 48, height: 48, alignItems: "center", justifyContent: "center",
          backgroundColor: active ? theme.color.accent : "transparent", borderRadius: theme.radius.sm }}>
        {children}
      </View>
    </GestureDetector>
  );
}
