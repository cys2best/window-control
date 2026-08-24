import React, { useCallback, useEffect, useState } from "react";
import { View, Text, FlatList, ScrollView, RefreshControl } from "react-native";
import Svg, { Rect, Path, Circle } from "react-native-svg";
import { useServer } from "../api/ServerContext";
import { theme } from "../theme/tokens";
import { InstanceRow } from "../components/InstanceRow";
import { NetChip } from "../components/NetChip";
import { BottomNav } from "../components/BottomNav";
import type { Instance } from "../api/client";

export function InstanceList({ navigation }: { navigation: any }) {
  const { client, base } = useServer();
  const [items, setItems] = useState<Instance[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [reachable, setReachable] = useState(true);

  const load = useCallback(async () => {
    if (!client) return;
    try { setItems(await client.instances()); setReachable(true); }
    catch { setReachable(false); }
  }, [client]);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };
  const open = (inst: Instance) => {
    client?.keyframe(inst.serial);
    navigation.navigate("Stream", { serial: inst.serial, title: inst.title });
  };
  const host = (base ?? "").replace(/^https?:\/\//, "");
  const net = reachable ? "connected" : "disconnected";

  const header = (
    <View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 14, padding: 16, marginBottom: 22,
        backgroundColor: theme.color.card, borderRadius: theme.radius.card,
        shadowColor: "#1c1a19", shadowOpacity: 0.06, shadowRadius: 6, shadowOffset: { width: 0, height: 1 }, elevation: 1 }}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 12.5, color: theme.color.textMuted, marginBottom: 5 }}>Server</Text>
          <Text numberOfLines={1} style={{ fontFamily: theme.font.semibold, fontSize: 15, color: theme.color.text }}>{host}</Text>
        </View>
        <NetChip state={net as any} />
      </View>
      <View style={{ flexDirection: "row", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
        <Text style={{ flex: 1, fontFamily: theme.font.semibold, fontSize: 16, color: theme.color.text }}>Instances</Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 12.5, color: theme.color.textMuted }}>{refreshing ? "Syncing…" : `${items.length} online`}</Text>
      </View>
    </View>
  );

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.screen }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 24, paddingTop: 52, paddingBottom: 8 }}>
        <Svg width={30} height={30} viewBox="0 0 40 40" aria-label="EmuCtrl">
          <Rect width={40} height={40} rx={12} fill={theme.color.accent} />
          <Path d="M13.2 10.4 15.4 14M26.8 10.4 24.6 14" stroke={theme.color.text} strokeWidth={2} strokeLinecap="round" />
          <Path d="M11 22.6a9 9 0 0 1 18 0z" fill={theme.color.text} />
          <Circle cx={16.4} cy={18.6} r={1.25} fill={theme.color.accent} />
          <Circle cx={23.6} cy={18.6} r={1.25} fill={theme.color.accent} />
          <Rect x={11} y={24.6} width={18} height={5.6} rx={2.6} fill={theme.color.text} />
          <Path d="M22.6 21.8 32.8 27l-4.2 1.1-1.1 4.2z" fill={theme.color.text} stroke={theme.color.accent} strokeWidth={1.6} strokeLinejoin="round" />
        </Svg>
        <Text style={{ flex: 1, fontFamily: theme.font.bold, fontSize: 26, color: theme.color.text }}>Windows</Text>
      </View>
      {items.length === 0 ? (
        <ScrollView
          contentContainerStyle={{ flexGrow: 1, padding: 24 }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}>
          {header}
          <View style={{ padding: 34, backgroundColor: theme.color.card, borderRadius: theme.radius.card, alignItems: "center" }}>
            <Text style={{ fontFamily: theme.font.semibold, fontSize: 17, color: theme.color.text, marginBottom: 8 }}>
              {reachable ? "No windows found" : "Can't reach the server"}
            </Text>
            <Text style={{ fontFamily: theme.font.regular, fontSize: 13, textAlign: "center", color: theme.color.textMuted }}>
              {reachable
                ? "The server answered, but nothing is running. Start an instance in LDPlayer, then pull to refresh."
                : "We couldn't reach the server on its last check. Confirm it's running and reachable, then pull to refresh."}
            </Text>
          </View>
        </ScrollView>
      ) : (
        <FlatList data={items} keyExtractor={(i) => i.id}
          ListHeaderComponent={header}
          refreshing={refreshing} onRefresh={onRefresh}
          contentContainerStyle={{ paddingHorizontal: 24, paddingTop: 8, paddingBottom: 120 }}
          renderItem={({ item }) => (
            <InstanceRow instance={item} active={false}
              previewUri={client!.previewUrl(item.serial)} onPress={() => open(item)} />
          )} />
      )}
      <BottomNav active="windows"
        onWindows={() => {}}
        onStream={() => { if (items[0]) open(items[0]); }}
        onSetup={() => navigation.navigate("ServerSetup")} />
    </View>
  );
}
