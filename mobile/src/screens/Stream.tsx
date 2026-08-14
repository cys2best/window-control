import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, TextInput } from "react-native";
import { RTCView } from "react-native-webrtc";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { runOnJS } from "react-native-reanimated";
import { useServer } from "../api/ServerContext";
import { theme } from "../theme/tokens";
import { connectWhep } from "../webrtc/whep";
import { makeInputSocket, clickMsg, dragStartMsg, dragMoveMsg, dragEndMsg, scrollMsg, keyMsg } from "../input/inputSocket";
import { normalizeCoords } from "../input/coords";
import { makeAdaptive } from "../quality/adaptive";
import { StreamToolbar } from "../components/StreamToolbar";
import { SettingsModal } from "../components/SettingsModal";
import { SwitchDrawer } from "../components/SwitchDrawer";
import { StatsOverlay } from "../components/StatsOverlay";
import { ErrorOverlay } from "../components/ErrorOverlay";

type Net = "connected" | "connecting" | "disconnected";

export function Stream({ route, navigation }: { route: any; navigation: any }) {
  const { client } = useServer();
  const { serial } = route.params;
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [net, setNet] = useState<Net>("connecting");
  const [failed, setFailed] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [overlay, setOverlay] = useState<null | "settings" | "drawer">(null);
  const [keyboardOn, setKeyboardOn] = useState(false);
  const [statsOn, setStatsOn] = useState(false);
  const [tier, setTier] = useState("auto");
  const [instances, setInstances] = useState<any[]>([]);
  const rect = useRef({ width: 1, height: 1 });
  const content = useRef({ w: 1, h: 1 });
  const session = useRef<any>(null);
  const sock = useRef<any>(null);
  const adaptive = useRef<any>(null);

  const start = useCallback(async () => {
    if (!client) return;
    setFailed(false); setNet("connecting");
    try {
      const sel = await client.select(serial);
      content.current = { w: sel.w, h: sel.h };
      session.current?.close();
      session.current = connectWhep({
        whepUrl: sel.whep_url, stunUrl: sel.stun_url,
        onStream: (s) => setStreamUrl(s.toURL()),
        onState: (st) => {
          setNet(st === "connected" ? "connected" : st === "failed" ? "disconnected" : "connecting");
          if (st === "failed") setFailed(true);
        },
      });
      adaptive.current?.stop();
      adaptive.current = makeAdaptive({ serial, onApply: (t) => client.setQuality(serial, t) });
      adaptive.current.start(session.current.pc);
    } catch { setFailed(true); setNet("disconnected"); }
  }, [client, serial]);

  useEffect(() => {
    sock.current = makeInputSocket(client!.inputWsUrl(), { onNet: (s) => { if (s === "bad") setNet("disconnected"); } });
    client!.instances().then(setInstances).catch(() => {});
    start();
    return () => { session.current?.close(); sock.current?.close(); adaptive.current?.stop(); };
  }, [start]);

  const send = (m: object) => sock.current?.send(m);
  const norm = (px: number, py: number) => normalizeCoords({ x: px, y: py }, rect.current, content.current);

  const tap = Gesture.Tap().onEnd((e) => { const c = norm(e.x, e.y); runOnJS(send)(clickMsg(c.x, c.y)); });
  const pan = Gesture.Pan()
    .onStart((e) => { const c = norm(e.x, e.y); runOnJS(send)(dragStartMsg(c.x, c.y)); })
    .onUpdate((e) => { const c = norm(e.x, e.y); runOnJS(send)(dragMoveMsg(c.x, c.y, Math.abs(e.velocityY) > Math.abs(e.velocityX) * 1.5)); })
    .onEnd((e) => { const c = norm(e.x, e.y); runOnJS(send)(dragEndMsg(c.x, c.y)); });
  const gesture = Gesture.Exclusive(pan, tap);

  const pickTier = (t: string) => {
    setTier(t);
    if (t === "auto") adaptive.current?.setAuto();
    else adaptive.current?.pin(t);
  };
  const switchTo = (inst: any) => {
    setOverlay(null);
    client?.keyframe(inst.serial);
    navigation.replace("Stream", { serial: inst.serial, title: inst.title });
  };
  const reconnect = async () => { setReconnecting(true); await start(); setReconnecting(false); };

  const statsLines = `TIER   ${tier}`; // full stats sampling wired in device pass; tier always shown

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.streamBg }}>
      <GestureDetector gesture={gesture}>
        <View style={{ flex: 1 }} onLayout={(e) => { rect.current = { width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height }; }}>
          {streamUrl ? <RTCView streamURL={streamUrl} objectFit="contain" style={{ flex: 1 }} /> : null}
        </View>
      </GestureDetector>

      {statsOn && !failed ? <StatsOverlay lines={statsLines} /> : null}

      <StreamToolbar net={net}
        active={{ settings: overlay === "settings", drawer: overlay === "drawer", keyboard: keyboardOn, stats: statsOn }}
        onSettings={() => setOverlay(overlay === "settings" ? null : "settings")}
        onSwitch={() => setOverlay(overlay === "drawer" ? null : "drawer")}
        onKeyboard={() => setKeyboardOn((v) => !v)}
        onStats={() => setStatsOn((v) => !v)}
        onBack={() => navigation.navigate("InstanceList")} />

      {keyboardOn ? (
        <TextInput autoFocus onKeyPress={(e) => send(keyMsg(e.nativeEvent.key))}
          onBlur={() => setKeyboardOn(false)}
          style={{ position: "absolute", opacity: 0, height: 1, width: 1 }} />
      ) : null}

      {overlay === "drawer" ? (
        <SwitchDrawer instances={instances} activeSerial={serial} onPick={switchTo} onClose={() => setOverlay(null)} />
      ) : null}
      {overlay === "settings" ? (
        <SettingsModal tier={tier} onPick={pickTier} statsOn={statsOn}
          onToggleStats={() => setStatsOn((v) => !v)} onClose={() => setOverlay(null)} />
      ) : null}
      {failed ? <ErrorOverlay onReconnect={reconnect} onBack={() => navigation.navigate("InstanceList")} reconnecting={reconnecting} /> : null}
    </View>
  );
}
