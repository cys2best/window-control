import React, { useEffect, useRef } from "react";
import { Animated, Pressable, View, Text, Image } from "react-native";
import { theme } from "../theme/tokens";
import type { Instance } from "@wc/core";

export function InstanceRow({ instance, previewSource, onPress }:
  { instance: Instance; previewSource: { uri: string; headers?: { Authorization: string } }; onPress: () => void }) {
  const scanline = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!instance.active) return;
    const animation = Animated.loop(Animated.timing(scanline, {
      toValue: 1, duration: 4000, useNativeDriver: true, isInteraction: false,
    }));
    animation.start();
    return () => animation.stop();
  }, [instance.active, scanline]);
  const translateY = scanline.interpolate({ inputRange: [0, 1], outputRange: [-4, 120] });
  const hasResolution = typeof instance.w === "number" && typeof instance.h === "number";
  const hasFps = typeof instance.fps === "number";
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={instance.title} onPress={onPress}
      style={{ flex: 1, minWidth: 260, padding: 8, margin: 8, backgroundColor: theme.color.surfaceRaised,
        borderRadius: 14, borderWidth: 1, borderColor: theme.color.border }}>
      <View style={{ aspectRatio: 16 / 9, borderRadius: theme.radius.sm, overflow: "hidden", backgroundColor: theme.color.surface }}>
        <Image testID="instance-preview" source={previewSource} resizeMode="cover" style={{ width: "100%", height: "100%" }} />
        {instance.active ? <Animated.View testID="instance-scanline" pointerEvents="none"
          style={{ position: "absolute", left: 0, right: 0, height: 2, backgroundColor: theme.color.accent,
            opacity: 0.82, transform: [{ translateY }] }} /> : null}
      </View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 8, paddingTop: 12, paddingBottom: 6 }}>
        <Text style={{ flex: 1, fontFamily: theme.font.semibold, fontSize: 15, color: theme.color.text }}>{instance.title}</Text>
        {instance.active ? (
          <View style={{ paddingHorizontal: 7, paddingVertical: 3, backgroundColor: theme.color.live, borderRadius: 4 }}>
            <Text style={{ fontFamily: theme.font.monoBold, fontSize: 10, color: theme.color.bg }}>LIVE</Text>
          </View>
        ) : null}
      </View>
      {(hasResolution || hasFps) ? <View style={{ flexDirection: "row", gap: 6, paddingHorizontal: 8, paddingBottom: 6 }}>
        {hasResolution ? <Text style={{ paddingHorizontal: 6, paddingVertical: 3, borderRadius: 4, backgroundColor: theme.color.surface, fontFamily: theme.font.mono, fontSize: 10, color: theme.color.textMuted }}>{instance.w}×{instance.h}</Text> : null}
        {hasFps ? <Text style={{ paddingHorizontal: 6, paddingVertical: 3, borderRadius: 4, backgroundColor: theme.color.surface, fontFamily: theme.font.mono, fontSize: 10, color: theme.color.textMuted }}>{instance.fps} FPS</Text> : null}
      </View> : null}
    </Pressable>
  );
}
