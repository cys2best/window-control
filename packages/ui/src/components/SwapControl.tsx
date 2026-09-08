import React, { useMemo } from "react";
import { Pressable, Text, View } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { theme } from "../theme/tokens";

export const SWAP_CONTROL_HALF_HEIGHT = 58;

export type SwapControlProps = { activeIndex: number; count: number; onOpen: () => void; onCycle: (direction: 1 | -1) => void; onWake: () => void; tick: () => void };

export function SwapControl({ activeIndex, count, onOpen, onCycle, onWake, tick }: SwapControlProps) {
  const pan = useMemo(() => Gesture.Pan().runOnJS(true).activeOffsetY([-20, 20]).failOffsetX([-15, 15])
    .onEnd((event) => {
      if (Math.abs(event.translationY) < 20) return;
      const direction: 1 | -1 = event.translationY < 0 ? 1 : -1;
      const next = activeIndex + direction;
      if (next >= 0 && next < count) { onWake(); tick(); onCycle(direction); }
    }), [activeIndex, count, onCycle, onWake, tick]);
  return <GestureDetector gesture={pan}><View style={{ position: "absolute", left: 0, top: "50%" as any, marginTop: -SWAP_CONTROL_HALF_HEIGHT, width: 68,
    backgroundColor: theme.color.glass, borderRightWidth: 1, borderTopWidth: 1, borderBottomWidth: 1,
    borderColor: "rgba(255,255,255,0.10)", alignItems: "center" }}>
    <Pressable accessibilityRole="button" accessibilityLabel="Switch instance" onPress={() => { onWake(); tick(); onOpen(); }}
      style={{ width: 68, height: 116, alignItems: "center", justifyContent: "center", gap: 7 }}>
      <Text style={{ color: theme.color.accent, fontSize: 24, lineHeight: 25 }}>⇅</Text>
      <Text style={{ color: theme.color.text, fontFamily: theme.font.monoBold, fontSize: 9, letterSpacing: 1 }}>SWAP</Text>
      <Text style={{ color: theme.color.textDim, fontFamily: theme.font.mono, fontSize: 9 }}>{activeIndex + 1} / {count}</Text>
    </Pressable>
  </View></GestureDetector>;
}
