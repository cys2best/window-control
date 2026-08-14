import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, TextInput, PanResponder } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
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
  const [rtt, setRtt] = useState<number | null>(null);
  const rect = useRef({ width: 1, height: 1 });
  const content = useRef({ w: 1, h: 1 });
  const session = useRef<any>(null);
  const sock = useRef<any>(null);
  const adaptive = useRef<any>(null);
  const lastMove = useRef(0);
  const scrollLast = useRef(0);
  const keyInput = useRef<TextInput>(null);

  const startGen = useRef(0);
  const start = useCallback(async () => {
    if (!client) return;
    const t0 = Date.now();
    // Rapid instance switches (fast toolbar swipes) can fire start() again
    // before the previous call's client.select() round-trip has returned.
    // Without this guard, an earlier call can finish after a later one, close
    // the NEWER session (wrong one) and overwrite session.current with its
    // own stale session — orphaning the real current session with no
    // reference left to close it. It then sits server-side with a full write
    // queue until mediamtx eventually times it out on its own.
    const gen = ++startGen.current;
    setFailed(false); setNet("connecting");
    try {
      const sel = await client.select(serial);
      console.log(`[stream] select() answered +${Date.now() - t0}ms`);
      if (gen !== startGen.current) return; // superseded before WHEP even started
      content.current = { w: sel.w, h: sel.h };
      session.current?.close();
      const s = connectWhep({
        whepUrl: sel.whep_url, stunUrl: sel.stun_url,
        onStream: (stream) => {
          if (gen !== startGen.current) return;
          console.log(`[stream] first track/frame +${Date.now() - t0}ms`);
          setStreamUrl(stream.toURL());
        },
        onState: (st) => {
          if (gen !== startGen.current) return;
          setNet(st === "connected" ? "connected" : st === "failed" ? "disconnected" : "connecting");
          if (st === "failed") setFailed(true);
        },
      });
      // The WHEP POST inside connectWhep() is already in flight and will
      // create a session server-side regardless of whether we win the race.
      // If a newer switch supersedes us before that POST resolves (or even
      // right after), `s` never gets stored below and would otherwise be
      // leaked — DELETE it explicitly the moment we notice we lost.
      if (gen !== startGen.current) { s.close(); return; }
      session.current = s;
      adaptive.current?.stop();
      adaptive.current = makeAdaptive({ serial, onApply: (t) => client.setQuality(serial, t) });
      adaptive.current.start(session.current.pc);
    } catch { if (gen === startGen.current) { setFailed(true); setNet("disconnected"); } }
  }, [client, serial]);

  // Input socket + instance list are owned by the client identity, not by
  // `start`. Keying this to [client] keeps the socket alive across serial
  // changes so in-flight input is not dropped.
  useEffect(() => {
    if (!client) return;
    sock.current = makeInputSocket(client.inputWsUrl(), {
      onNet: (s) => { if (s === "bad") setNet("disconnected"); },
      onRtt: (ms) => setRtt(ms),
    });
    client.instances().then(setInstances).catch(() => {});
    return () => { sock.current?.close(); };
  }, [client]);

  // WHEP session + adaptive quality follow `start` (serial/client changes).
  useEffect(() => {
    start();
    return () => { session.current?.close(); adaptive.current?.stop(); };
  }, [start]);

  // Open the stream in landscape by default, but still allow the user to
  // rotate freely (either landscape direction) while this screen is up.
  // Restore the app-wide portrait lock on exit.
  useEffect(() => {
    ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.LANDSCAPE);
    return () => { ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP); };
  }, []);

  const send = (m: object) => { sock.current?.send(m); };
  const norm = (px: number, py: number) => normalizeCoords({ x: px, y: py }, rect.current, content.current);

  // react-native-gesture-handler's Pan gesture never delivers onUpdate in
  // this project (confirmed: onStart is immediately followed by a clean
  // onEnd/state=END with zero move samples, reproduced across four different
  // gesture configs). A raw PanResponder on the same view tracks every move
  // correctly, so pan/scroll are built on PanResponder instead. RNGH is kept
  // for the toolbar (tap + swipe), which does work there.
  // Single-finger: a real touch-down isn't sent to the remote until movement
  // is confirmed past a small threshold, so a plain tap sends one click
  // (down+up) instead of an unpaired drag-start down followed by a second,
  // separate click down+up.
  const dragStarted = useRef(false);
  const isScroll = useRef(false);
  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (e) => {
        isScroll.current = e.nativeEvent.touches.length >= 2;
        dragStarted.current = false;
        if (isScroll.current) scrollLast.current = 0;
      },
      onPanResponderMove: (e, gs) => {
        const touches = e.nativeEvent.touches;
        if (touches.length >= 2) {
          if (!isScroll.current) { isScroll.current = true; scrollLast.current = gs.dy; }
          const delta = gs.dy - scrollLast.current;
          if (Math.abs(delta) < 1) return;
          scrollLast.current = gs.dy;
          const x = (touches[0].locationX + touches[1].locationX) / 2;
          const y = (touches[0].locationY + touches[1].locationY) / 2;
          const c = norm(x, y);
          send(scrollMsg(c.x, c.y, delta > 0 ? -1 : 1));
          return;
        }
        if (isScroll.current) return; // was a 2-finger gesture that dropped to 1 finger
        const { locationX: x, locationY: y } = e.nativeEvent;
        if (!dragStarted.current) {
          if (Math.abs(gs.dx) < 3 && Math.abs(gs.dy) < 3) return;
          dragStarted.current = true;
          const c = norm(x - gs.dx, y - gs.dy); // touch-down point, before this move's delta
          send(dragStartMsg(c.x, c.y));
        }
        const now = Date.now();
        if (now - lastMove.current < 16) return; // ~60fps cap, web-client parity
        lastMove.current = now;
        const c = norm(x, y);
        send(dragMoveMsg(c.x, c.y, Math.abs(gs.vy) > Math.abs(gs.vx) * 1.5));
      },
      onPanResponderRelease: (e, gs) => {
        const { locationX: x, locationY: y } = e.nativeEvent;
        const c = norm(x, y);
        if (isScroll.current) { /* no discrete end event for scroll */ }
        else if (dragStarted.current) send(dragEndMsg(c.x, c.y));
        else send(clickMsg(c.x, c.y));
        dragStarted.current = false;
        isScroll.current = false;
      },
      onPanResponderTerminate: () => { dragStarted.current = false; isScroll.current = false; },
    })
  ).current;

  const switchTo = (inst: any) => {
    setOverlay(null);
    client?.keyframe(inst.serial);
    // setParams (not replace) keeps this screen mounted so the landscape
    // lock in the orientation effect below doesn't flash back to portrait.
    navigation.setParams({ serial: inst.serial, title: inst.title });
  };
  const cycleInstance = (dir: 1 | -1) => {
    if (instances.length < 2) return;
    const i = instances.findIndex((x) => x.serial === serial);
    const next = instances[(i + dir + instances.length) % instances.length];
    switchTo(next);
  };
  // Vertical swipe on the toolbar strip (not the video) cycles instances,
  // so it never competes with the drag gesture used for remote mouse input.
  // Memoized on [instances, serial] for the same reattachment reason as the
  // video gesture above — only rebuild when what cycleInstance closes over
  // actually changes, not on every rtt/net/stats render.
  const toolbarSwipe = React.useMemo(() => Gesture.Pan()
    .runOnJS(true)
    .activeOffsetY([-20, 20])
    .failOffsetX([-15, 15])
    .onEnd((e) => { cycleInstance(e.translationY < 0 ? 1 : -1); }),
  [instances, serial]);

  const pickTier = (t: string) => {
    setTier(t);
    if (t === "auto") adaptive.current?.setAuto();
    else adaptive.current?.pin(t);
  };
  const reconnect = async () => { setReconnecting(true); await start(); setReconnecting(false); };

  // RN key names -> server X11 key names (`_JS_KEY_TO_KEYCODE`). Only map the
  // reliably-wrong ones; everything else passes through unchanged.
  const KEYMAP: Record<string, string> = { Enter: "Return", Backspace: "BackSpace" };
  const sendKey = (k: string) => send(keyMsg(KEYMAP[k] ?? k));

  const statsLines = `TIER   ${tier}\ninput  ${rtt == null ? "—" : `${rtt}ms`}`; // full stats sampling wired in device pass

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.streamBg }}>
      <View collapsable={false} style={{ flex: 1 }} {...panResponder.panHandlers}
        onLayout={(e) => { rect.current = { width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height }; }}>
        {streamUrl ? <RTCView streamURL={streamUrl} objectFit="contain" pointerEvents="none" style={{ flex: 1 }} /> : null}
      </View>

      {statsOn && !failed ? <StatsOverlay lines={statsLines} /> : null}

      <GestureDetector gesture={toolbarSwipe}>
        <StreamToolbar net={net}
          active={{ settings: overlay === "settings", drawer: overlay === "drawer", keyboard: keyboardOn, stats: statsOn }}
          onSettings={() => setOverlay(overlay === "settings" ? null : "settings")}
          onSwitch={() => setOverlay(overlay === "drawer" ? null : "drawer")}
          onKeyboard={() => (keyboardOn ? keyInput.current?.blur() : keyInput.current?.focus())}
          onStats={() => setStatsOn((v) => !v)}
          onBack={() => {
            // Lock portrait before the screen-pop transition starts, not only
            // in the unmount cleanup below — requesting the geometry change
            // mid-transition can get silently dropped by iOS.
            ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP);
            navigation.navigate("InstanceList");
          }} />
      </GestureDetector>

      <TextInput ref={keyInput} onKeyPress={(e) => sendKey(e.nativeEvent.key)}
        showSoftInputOnFocus
        onFocus={() => setKeyboardOn(true)}
        onBlur={() => setKeyboardOn(false)}
        returnKeyType="done"
        onSubmitEditing={() => keyInput.current?.blur()}
        style={{ position: "absolute", opacity: 0, height: 1, width: 1 }} />

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
