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
      style={{ flex: 1, minWidth: 260, margin: 8, overflow: "hidden", backgroundColor: theme.color.surfaceRaised,
        borderRadius: 14, borderWidth: 1, borderColor: instance.active ? theme.color.accent : theme.color.border }}>
      <View style={{ aspectRatio: 16 / 9, overflow: "hidden", backgroundColor: theme.color.surface }}>
        <Image testID="instance-preview" source={previewSource} resizeMode="cover" style={{ width: "100%", height: "100%" }} />
        {instance.active ? <Animated.View testID="instance-scanline" pointerEvents="none"
          style={{ position: "absolute", left: 0, right: 0, height: 2, backgroundColor: theme.color.accent,
            opacity: 0.82, transform: [{ translateY }] }} /> : null}
        {instance.active ? <View style={{ position: "absolute", left: 11, top: 10, paddingHorizontal: 7, paddingVertical: 3, backgroundColor: theme.color.live, borderRadius: 4 }}><Text style={{ fontFamily: theme.font.monoBold, fontSize: 9, letterSpacing: .7, color: theme.color.bg }}>LIVE</Text></View> : null}
        {(hasResolution || hasFps) ? <View style={{ position: "absolute", right: 10, top: 10, flexDirection: "row", gap: 5 }}>
          {hasResolution ? <Text style={{ paddingHorizontal: 6, paddingVertical: 3, borderRadius: 4, backgroundColor: "rgba(6,7,11,.72)", borderWidth: 1, borderColor: theme.color.border, fontFamily: theme.font.mono, fontSize: 9, color: theme.color.textMuted }}>{instance.w}×{instance.h}</Text> : null}
          {hasFps ? <Text style={{ paddingHorizontal: 6, paddingVertical: 3, borderRadius: 4, backgroundColor: "rgba(6,7,11,.72)", borderWidth: 1, borderColor: theme.color.border, fontFamily: theme.font.mono, fontSize: 9, color: theme.color.textMuted }}>{instance.fps} FPS</Text> : null}
        </View> : null}
        <Text numberOfLines={1} style={{ position: "absolute", left: 11, right: 11, bottom: 10, fontFamily: theme.font.semibold, fontSize: 15, color: theme.color.text, textShadowColor: "rgba(0,0,0,.9)", textShadowOffset: { width: 0, height: 2 }, textShadowRadius: 10 }}>{instance.title}</Text>
      </View>
      <View style={{ minHeight: 42, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 11, borderTopWidth: 1, borderColor: "#1b1f2b" }}><Text style={{ fontFamily: theme.font.mono, fontSize: 9.5, letterSpacing: .5, color: theme.color.textMuted }}>{instance.serial}</Text><Text style={{ fontFamily: theme.font.mono, fontSize: 8.5, letterSpacing: .8, color: theme.color.accent }}>TAP TO STREAM</Text></View>
    </Pressable>
  );
}
