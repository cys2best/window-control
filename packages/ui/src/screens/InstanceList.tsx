import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, FlatList, Pressable, useWindowDimensions } from "react-native";
import { useServer } from "@wc/core";
import { theme } from "../theme/tokens";
import { InstanceRow } from "../components/InstanceRow";
import { NetChip } from "../components/NetChip";
import { BottomNav } from "../components/BottomNav";
import { BrandMark } from "../components/BrandMark";
import type { Instance } from "@wc/core";

export function InstanceList({ navigation }: { navigation: any }) {
  const { client, clearAuth, hostReachability } = useServer() as any;
  const { width } = useWindowDimensions();
  const listRef = useRef<FlatList<Instance>>(null);
  const [items, setItems] = useState<Instance[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [reachable, setReachable] = useState(true);
  const [rtt, setRtt] = useState<number | null>(null);
  const [instancesOffset, setInstancesOffset] = useState(0);
  const columns = Math.max(1, Math.floor((width - 48 + 16) / (260 + 16)));
  const activeInstance = items.find((item) => item.active) ?? items[0];

  const load = useCallback(async () => {
    if (!client) return;
    const [instancesResult, pingResult] = await Promise.allSettled([client.instances(), client.ping()]);

    if (instancesResult.status === "fulfilled") {
      setItems(instancesResult.value);
      setReachable(true);
    } else {
      const err = instancesResult.reason as any;
      if (err?.status === 401) {
        if (clearAuth) await clearAuth();
        if (navigation?.replace) {
          navigation.replace("Login");
        } else if (navigation?.navigate) {
          navigation.navigate("Login");
        }
        return;
      }
      setReachable(false);
    }

    setRtt(pingResult.status === "fulfilled" ? pingResult.value : null);
  }, [client, navigation, clearAuth]);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };
  const open = (inst?: Instance) => {
    if (!inst) return;
    client?.keyframe(inst.serial);
    navigation.navigate("Stream", { serial: inst.serial, title: inst.title });
  };

  const header = (
    <View>
      <View testID="host-status-card" style={{ padding: 14, marginBottom: 20,
        backgroundColor: theme.color.surfaceRaised, borderRadius: 14, borderWidth: 1, borderColor: theme.color.border }}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <Text numberOfLines={1} style={{ flex: 1, fontFamily: theme.font.semibold, fontSize: 14, color: theme.color.text }}>{hostReachability?.host ?? "Host unavailable"}</Text>
          {hostReachability ? <NetChip route={hostReachability.route} state={hostReachability.state} host={hostReachability.host} /> : null}
        </View>
        <View style={{ flexDirection: "row", marginTop: 13, paddingTop: 12, borderTopWidth: 1, borderColor: "#1b1f2b" }}>
          <View style={{ flex: 1 }}><Text style={{ fontFamily: theme.font.mono, fontSize: 8.5, letterSpacing: 1.15, color: theme.color.textDim }}>PING</Text>{rtt !== null ? <Text style={{ fontFamily: theme.font.mono, fontSize: 18, color: theme.color.telemetry, marginTop: 3 }}>{rtt}<Text style={{ fontSize: 10, color: theme.color.textDim }}> ms</Text></Text> : <Text style={{ fontFamily: theme.font.mono, fontSize: 18, color: theme.color.textDim, marginTop: 3 }}>—</Text>}</View>
          <View style={{ flex: 1.45, borderLeftWidth: 1, borderColor: "#1b1f2b", paddingLeft: 12 }}><Text style={{ fontFamily: theme.font.mono, fontSize: 8.5, letterSpacing: 1.15, color: theme.color.textDim }}>ADDRESS</Text><Text numberOfLines={1} style={{ fontFamily: theme.font.mono, fontSize: 11, color: theme.color.text, marginTop: 6 }}>{hostReachability?.host ?? "—"}</Text></View>
        </View>
      </View>
      <View testID="instances-heading" onLayout={(event) => setInstancesOffset(event.nativeEvent.layout.y)} style={{ flexDirection: "row", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
        <Text style={{ flex: 1, fontFamily: theme.font.semibold, fontSize: 16, color: theme.color.text }}>Instances</Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 12.5, color: theme.color.textMuted }}>{refreshing ? "Syncing…" : `${items.length} online`}</Text>
      </View>
    </View>
  );

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.screen }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 20, paddingTop: 48, paddingBottom: 14 }}>
        <BrandMark size={28} />
        <Text style={{ flex: 1, fontFamily: theme.font.bold, fontSize: 13, letterSpacing: 1.7, color: theme.color.text }}>EMUCTRL</Text>
        <Pressable accessibilityRole="button" accessibilityLabel="Account" onPress={() => navigation.navigate("Account")}>
          <Text style={{ fontFamily: theme.font.monoMedium, fontSize: 9.5, letterSpacing: 1, color: theme.color.accent }}>SETTINGS</Text>
        </Pressable>
      </View>
      <FlatList testID="instance-grid" accessibilityLabel={`Instance grid, ${columns} columns`} ref={listRef} data={items} key={`grid-${columns}`} numColumns={columns} keyExtractor={(i) => i.id}
        ListHeaderComponent={header} refreshing={refreshing} onRefresh={onRefresh}
        contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 8, paddingBottom: 120, flexGrow: 1 }}
        ListEmptyComponent={<View style={{ padding: 34, backgroundColor: theme.color.surfaceRaised, borderRadius: 14, alignItems: "center" }}>
          <Text style={{ fontFamily: theme.font.semibold, fontSize: 17, color: theme.color.text, marginBottom: 8 }}>{reachable ? "No windows found" : "Can't reach the server"}</Text>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 13, textAlign: "center", color: theme.color.textMuted }}>{reachable ? "The server answered, but nothing is running. Start an instance in LDPlayer, then pull to refresh." : "We couldn't reach the server on its last check. Confirm it's running and reachable, then pull to refresh."}</Text>
        </View>}
        renderItem={({ item }) => <InstanceRow instance={item} previewSource={client!.previewSource(item.serial)} onPress={() => open(item)} />} />
      <BottomNav onInstances={() => listRef.current?.scrollToOffset({ offset: instancesOffset, animated: true })}
        onResume={() => open(activeInstance)} onHealth={() => listRef.current?.scrollToOffset({ offset: 0, animated: true })} />
    </View>
  );
}
