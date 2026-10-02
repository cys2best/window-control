import React, { useMemo } from "react";
import { Pressable, Text, View } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { theme } from "../theme/tokens";

export type SwapControlProps = {
  activeIndex: number; count: number; onOpen: () => void; onCycle: (direction: 1 | -1) => void; onWake: () => void; tick: () => void;
  // Height already taken at the top of the left gutter (the diagnostic HUD).
  topInset?: number;
};

export function SwapControl({ activeIndex, count, onOpen, onCycle, onWake, tick, topInset = 0 }: SwapControlProps) {
  const pan = useMemo(() => Gesture.Pan().runOnJS(true).activeOffsetY([-20, 20]).failOffsetX([-15, 15])
    .onEnd((event) => {
      if (Math.abs(event.translationY) < 20) return;
      const direction: 1 | -1 = event.translationY < 0 ? 1 : -1;
      const next = activeIndex + direction;
      if (next >= 0 && next < count) { onWake(); tick(); onCycle(direction); }
    }), [activeIndex, count, onCycle, onWake, tick]);
  // The slot spans the gutter below `topInset` and centres the control in
  // it, so on a short screen the control sits under the HUD rather than on
  // top of it. With no inset that is the middle of the screen.
  return <View testID="swap-control" pointerEvents="box-none"
    style={{ position: "absolute", left: 0, top: topInset, bottom: 0, width: 68, justifyContent: "center" }}>
  <GestureDetector gesture={pan}><View style={{ width: 68,
    backgroundColor: theme.color.glass, borderRightWidth: 1, borderTopWidth: 1, borderBottomWidth: 1,
    borderColor: "rgba(255,255,255,0.10)", alignItems: "center" }}>
    <Pressable accessibilityRole="button" accessibilityLabel="Switch instance" onPress={() => { onWake(); tick(); onOpen(); }}
      style={{ width: 68, height: 116, alignItems: "center", justifyContent: "center", gap: 7 }}>
      <Text style={{ color: theme.color.accent, fontSize: 24, lineHeight: 25 }}>⇅</Text>
      <Text style={{ color: theme.color.text, fontFamily: theme.font.monoBold, fontSize: 9, letterSpacing: 1 }}>SWAP</Text>
      <Text style={{ color: theme.color.textDim, fontFamily: theme.font.mono, fontSize: 9 }}>{activeIndex + 1} / {count}</Text>
    </Pressable>
  </View></GestureDetector>
  </View>;
}
