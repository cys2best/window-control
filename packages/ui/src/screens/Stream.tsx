import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, TextInput, PanResponder } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import type { VideoViewComponent } from "../video/VideoView";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { runOnJS } from "react-native-reanimated";
import { useServer, connectEngineSession, EngineSession, normalizeCoords, makeAdaptive } from "@wc/core";
import { theme } from "../theme/tokens";
import { StreamToolbar } from "../components/StreamToolbar";
import { SettingsModal } from "../components/SettingsModal";
import { SwitchDrawer } from "../components/SwitchDrawer";
import { StatsOverlay } from "../components/StatsOverlay";
import { ErrorOverlay } from "../components/ErrorOverlay";

type Net = "connected" | "connecting" | "disconnected";

export function Stream({
  route,
  navigation,
  RTCImpl,
  VideoView,
}: {
  route: any;
  navigation: any;
  RTCImpl: any;
  VideoView: VideoViewComponent;
}) {
  const { client, authToken } = useServer();
  const { serial } = route.params;
  const [stream, setStream] = useState<MediaStream | null>(null);
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
  const session = useRef<EngineSession | null>(null);
  const adaptive = useRef<any>(null);
  const inputHealth = useRef<any>(null);
  const scrollLast = useRef(0);
  const keyInput = useRef<TextInput>(null);
  const dragStarted = useRef(false);
  const isScroll = useRef(false);
  const lastTouch = useRef({ x: 0, y: 0 });

  const releaseActiveDrag = useCallback((input = session.current?.input) => {
    if (input && dragStarted.current) {
      const point = lastTouch.current;
      const c = normalizeCoords({ x: point.x, y: point.y }, rect.current, content.current);
      input.dragEnd(c.x, c.y);
    }
    dragStarted.current = false;
    isScroll.current = false;
  }, []);

  const startGen = useRef(0);
  const start = useCallback(async () => {
    if (!client) return;
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
      if (gen !== startGen.current) return; // superseded before session even started
      content.current = { w: sel.w, h: sel.h };
      let nextStream: any = null;
      const s = await connectEngineSession({
        selection: sel,
        authToken,
        RTCImpl,
        onStream: (stream) => {
          if (gen !== startGen.current) return;
          nextStream = stream;
        },
        onInputRtt: (ms) => { if (gen === startGen.current) setRtt(ms); },
        onState: (st) => {
          if (gen !== startGen.current) return;
          setNet(st);
          if (st === "disconnected") {
            // A closed input channel or failed ICE triggers a fresh
            // select()/reconnect rather than surfacing the manual
            // ErrorOverlay for something the app can recover from on its own.
            if (gen === startGen.current) {
              releaseActiveDrag();
              start();
            }
          }
        },
      }).catch((error) => {
        if (gen === startGen.current) { setFailed(true); setNet("disconnected"); }
        throw error;
      });
      if (gen !== startGen.current) { s.close(); return; }
      // Keep the current session visible until its replacement is ready,
      // then close the stale one — avoids a visible gap while the new
      // session negotiates.
      const previous = session.current;
      session.current = s;
      if (nextStream) setStream(nextStream);
      if (previous) {
        releaseActiveDrag(previous.input);
        previous.close();
      }
      if (inputHealth.current) clearInterval(inputHealth.current);
      s.input.send({ type: "idr" });
      inputHealth.current = setInterval(() => {
        if (gen === startGen.current && session.current === s) {
          s.input.send({ type: "echo", t: Date.now() });
        }
      }, 2000);
      adaptive.current?.stop();
      adaptive.current = makeAdaptive({
        serial,
        onApply: (t) => client.setQuality(serial, t),
      });
      adaptive.current.start(session.current.pc);
    } catch { if (gen === startGen.current) { setFailed(true); setNet("disconnected"); } }
  }, [client, authToken, serial, releaseActiveDrag]);

  // Instance list is owned by the client identity, not by `start`.
  useEffect(() => {
    if (!client) return;
    client.instances().then(setInstances).catch(() => {});
  }, [client]);

  // WHEP session + input channel + adaptive quality follow `start`
  // (serial/client changes).
  useEffect(() => {
    start();
    return () => {
      releaseActiveDrag();
      startGen.current += 1;
      if (inputHealth.current) clearInterval(inputHealth.current);
      inputHealth.current = null;
      session.current?.close();
      adaptive.current?.stop();
    };
  }, [start, releaseActiveDrag]);

  // Open the stream in landscape by default, but still allow the user to
  // rotate freely (either landscape direction) while this screen is up.
  // Restore the app-wide portrait lock on exit.
  useEffect(() => {
    try {
      ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.LANDSCAPE).catch(() => {});
    } catch {}
    return () => {
      try {
        ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP).catch(() => {});
      } catch {}
    };
  }, []);

  const norm = (px: number, py: number) => normalizeCoords({ x: px, y: py }, rect.current, content.current);

  // react-native-gesture-handler's Pan gesture never delivers onUpdate in
  // this project (confirmed: onStart is immediately followed by a clean
  // onEnd/state=END with zero move samples, reproduced across four different
  // gesture configs). A raw PanResponder on the same view tracks every move
  // correctly, so pan/scroll are built on PanResponder instead. RNGH is kept
  // for the toolbar (tap + swipe), which does work there.
  // Single-finger input begins with drag_start so every touch, including a
  // tap, has a matching drag_end. Motion remains thresholded and coalesced by
  // the sender to avoid flooding the reliable channel.
  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: (e) => {
        isScroll.current = e.nativeEvent.touches.length >= 2;
        dragStarted.current = false;
        const { locationX: x, locationY: y } = e.nativeEvent;
        lastTouch.current = { x, y };
        if (isScroll.current) {
          scrollLast.current = 0;
          return;
        }
        const input = session.current?.input;
        if (input) {
          const c = norm(x, y);
          input.dragStart(c.x, c.y);
          dragStarted.current = true;
        }
      },
      onPanResponderMove: (e, gs) => {
        const input = session.current?.input;
        if (!input) return;
        const touches = e.nativeEvent.touches;
        if (touches.length >= 2) {
          if (!isScroll.current) {
            isScroll.current = true;
            if (dragStarted.current) {
              const c = norm(e.nativeEvent.locationX, e.nativeEvent.locationY);
              input.dragEnd(c.x, c.y);
              dragStarted.current = false;
            }
            scrollLast.current = gs.dy;
          }
          const delta = gs.dy - scrollLast.current;
          if (Math.abs(delta) < 1) return;
          scrollLast.current = gs.dy;
          const x = (touches[0].locationX + touches[1].locationX) / 2;
          const y = (touches[0].locationY + touches[1].locationY) / 2;
          const c = norm(x, y);
          input.scroll(c.x, c.y, -delta / rect.current.height);
          return;
        }
        if (isScroll.current) return; // was a 2-finger gesture that dropped to 1 finger
        const { locationX: x, locationY: y } = e.nativeEvent;
        lastTouch.current = { x, y };
        if (Math.abs(gs.dx) < 3 && Math.abs(gs.dy) < 3) return;
        const c = norm(x, y);
        input.dragMove(c.x, c.y);
      },
      onPanResponderRelease: (e) => {
        const { locationX: x, locationY: y } = e.nativeEvent;
        lastTouch.current = { x, y };
        releaseActiveDrag();
      },
      onPanResponderTerminate: (e) => {
        if (e?.nativeEvent) {
          lastTouch.current = { x: e.nativeEvent.locationX, y: e.nativeEvent.locationY };
        }
        releaseActiveDrag();
      },
    })
  ).current;

  const switchTo = (inst: any) => {
    setOverlay(null);
    // setParams (not replace) keeps this screen mounted so the landscape
    // lock in the orientation effect below doesn't flash back to portrait.
    navigation.setParams({ serial: inst.serial, title: inst.title });
  };
  const cycleInstance = (dir: 1 | -1) => {
    if (instances.length < 2) return;
    const i = instances.findIndex((x) => x.serial === serial);
    const j = i + dir;
    if (j < 0 || j >= instances.length) return; // at the first/last instance — no wrap
    switchTo(instances[j]);
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
  const reconnect = async () => {
    releaseActiveDrag();
    setReconnecting(true);
    await start();
    setReconnecting(false);
  };

  // RN key names -> server X11 key names (`_JS_KEY_TO_KEYCODE`). Only map the
  // reliably-wrong ones; everything else passes through unchanged.
  const KEYMAP: Record<string, string> = { Enter: "Return", Backspace: "BackSpace" };
  const sendKey = (k: string) => session.current?.input.send({ type: "key", key: KEYMAP[k] ?? k });

  const statsLines = `TIER   ${tier}\ninput  ${rtt == null ? "—" : `${rtt}ms`}`; // full stats sampling wired in device pass

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.streamBg }}>
      <View collapsable={false} style={{ flex: 1 }} {...panResponder.panHandlers}
        onLayout={(e) => { rect.current = { width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height }; }}>
        {stream ? <VideoView stream={stream} /> : null}
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
            try {
              ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP).catch(() => {});
            } catch {}
            navigation.navigate("InstanceList");
          }} />
      </GestureDetector>

      <TextInput ref={keyInput} testID="stream-key-input" onKeyPress={(e) => sendKey(e.nativeEvent.key)}
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
