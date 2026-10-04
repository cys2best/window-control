import React from "react";
import { View, Text, Pressable, ScrollView, Image } from "react-native";
import type { ApiClient, Instance } from "@wc/core";
import { theme } from "../theme/tokens";
import { useInstancePreview } from "../hooks/useInstancePreview";
export function SwitchDrawer({ instances, activeSerial, client, onPick, onClose }: { instances: Instance[]; activeSerial: string; client: ApiClient | null; onPick: (i: Instance) => void; onClose: () => void }) { return <View style={{ position: "absolute", inset: 0 as any }}><Pressable onPress={onClose} style={{ position: "absolute", inset: 0 as any, backgroundColor: "rgba(6,7,11,.45)" }} /><View style={{ position: "absolute", left: 0, right: 0, bottom: 0, backgroundColor: "rgba(9,10,15,.94)", borderTopWidth: 1, borderColor: "rgba(255,255,255,.12)", paddingTop: 11, paddingBottom: 14, paddingLeft: 68 }}><View style={{ flexDirection: "row", paddingHorizontal: 16, marginBottom: 9 }}><Text style={{ flex: 1, color: theme.color.textDim, fontFamily: theme.font.monoBold, fontSize: 9, letterSpacing: 1.6 }}>HOT-SWAP · TRANSPORT STAYS UP</Text><Pressable accessibilityLabel="Close instance switcher" onPress={onClose}><Text style={{ color: theme.color.accent, fontFamily: theme.font.monoBold, fontSize: 9, letterSpacing: 1 }}>CLOSE ✕</Text></Pressable></View><ScrollView horizontal contentContainerStyle={{ paddingHorizontal: 10, gap: 9 }}>{instances.map((i) => <DrawerRow key={i.id} instance={i} active={i.serial === activeSerial} client={client} onPick={onPick} />)}</ScrollView></View></View>; }


function DrawerRow({ instance: i, active, client, onPick }: { instance: Instance; active: boolean; client: ApiClient | null; onPick: (i: Instance) => void }) {
  const preview = useInstancePreview(client, i.serial);
  return <Pressable accessibilityLabel={i.title} onPress={() => onPick(i)} style={{ width: 132, borderWidth: 1, borderColor: active ? theme.color.accent : theme.color.border, backgroundColor: theme.color.surfaceRaised, padding: 7 }}>
    <View style={{ width: "100%", aspectRatio: 16 / 9, backgroundColor: theme.color.surface }}>
      {preview !== null && <Image source={preview} style={{ width: "100%", aspectRatio: 16 / 9, backgroundColor: theme.color.surface }} />}
    </View>
    <View style={{ flexDirection: "row", gap: 4, marginTop: 7 }}><Text numberOfLines={1} style={{ flex: 1, color: theme.color.text, fontFamily: theme.font.medium, fontSize: 11 }}>{i.title}</Text>{i.active ? <Text style={{ color: theme.color.live, fontFamily: theme.font.monoBold, fontSize: 9 }}>LIVE</Text> : null}</View>
    {(i.w && i.h) || typeof i.fps === "number" ? <Text style={{ color: theme.color.textDim, fontFamily: theme.font.mono, fontSize: 9, marginTop: 4 }}>{i.w && i.h ? `${i.w}×${i.h}` : ""}{i.w && i.h && typeof i.fps === "number" ? " · " : ""}{typeof i.fps === "number" ? `${i.fps} FPS` : ""}</Text> : null}
  </Pressable>;
}
