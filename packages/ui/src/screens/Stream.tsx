import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, TextInput, PanResponder } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import type { VideoViewComponent } from "../video/VideoView";
import { useServer, connectEngineSession, EngineSession, normalizeCoords, makeAdaptive, makeTelemetrySampler, DEFAULT_STREAM_PREFERENCES, type QualitySelection, type StreamTelemetry } from "@wc/core";
import { theme } from "../theme/tokens";
import { StreamRail, STREAM_RAIL_WIDTH } from "../components/StreamRail";
import { SwapControl } from "../components/SwapControl";
import { SettingsModal } from "../components/SettingsModal";
import { SwitchDrawer } from "../components/SwitchDrawer";
import { StatsOverlay } from "../components/StatsOverlay";
import { ErrorOverlay } from "../components/ErrorOverlay";

type Net = "connected" | "connecting" | "disconnected";
const IDLE_COLLAPSE_MS = 4000;

export function Stream({
  route,
  navigation,
  RTCImpl,
  VideoView,
  performHaptic,
}: {
  route: any;
  navigation: any;
  RTCImpl: any;
  VideoView: VideoViewComponent;
  performHaptic?: () => void;
}) {
  const { client, authToken, clearAuth, preferences = DEFAULT_STREAM_PREFERENCES, updatePreferences = () => {} } = useServer() as any;
  const { serial } = route.params;
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [net, setNet] = useState<Net>("connecting");
  const [failed, setFailed] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [overlay, setOverlay] = useState<null | "settings" | "drawer">(null);
  const [keyboardOn, setKeyboardOn] = useState(false);
  const [statsOn, setStatsOn] = useState(false);
  const [instances, setInstances] = useState<any[]>([]);
  const [telemetry, setTelemetry] = useState<StreamTelemetry>({ rttMs: null, loss: null, decodeMs: null, networkMs: null, inputMs: null, jitterMs: null, bitrateMbps: null, droppedFrames: null, transport: "LAN" });
  const [railOpen, setRailOpen] = useState(true);
  const rect = useRef({ width: 1, height: 1 });
  const content = useRef({ w: 1, h: 1 });
  const session = useRef<EngineSession | null>(null);
  const adaptive = useRef<any>(null);
  const inputHealth = useRef<any>(null);
  const sampler = useRef<ReturnType<typeof makeTelemetrySampler> | null>(null);
  const appliedTier = useRef<QualitySelection | null>(null);
  const currentQuality = useRef<QualitySelection>(preferences.quality);
  const scrollLast = useRef(0);
  const keyInput = useRef<TextInput>(null);
  const dragStarted = useRef(false);
  const isScroll = useRef(false);
  const lastTouch = useRef({ x: 0, y: 0 });
  const railTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const twoFingerMoved = useRef(false);

  currentQuality.current = preferences.quality;

  const wakeRail = useCallback(() => {
    setRailOpen(true);
    if (railTimer.current) clearTimeout(railTimer.current);
    if (preferences.hideRailWhilePlaying) {
      railTimer.current = setTimeout(() => setRailOpen(false), IDLE_COLLAPSE_MS);
    }
  }, [preferences.hideRailWhilePlaying]);
  const tick = useCallback(() => {
    if (preferences.haptics) performHaptic?.();
  }, [performHaptic, preferences.haptics]);

  useEffect(() => {
    wakeRail();
    return () => { if (railTimer.current) clearTimeout(railTimer.current); };
  }, [wakeRail]);
  useEffect(() => {
    setStatsOn(preferences.showHudOnConnect);
    if (adaptive.current && appliedTier.current !== preferences.quality) {
      if (preferences.quality === "auto") adaptive.current.setAuto();
      else adaptive.current.pin(preferences.quality);
      appliedTier.current = preferences.quality;
    }
  }, [preferences.quality, preferences.showHudOnConnect]);

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
        onInputRtt: (ms) => { if (gen === startGen.current) sampler.current?.setInputRtt(ms); },
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
      sampler.current?.stop();
      sampler.current = makeTelemetrySampler({ pc: s.pc, transport: s.kind, onSample: setTelemetry });
      sampler.current.start();
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
      const quality = currentQuality.current;
      if (quality === "auto") adaptive.current.setAuto();
      else adaptive.current.pin(quality);
      appliedTier.current = quality;
    } catch (error: any) {
      if (error?.status === 401) {
        if (clearAuth) await clearAuth();
        if (navigation?.replace) {
          navigation.replace("Login");
        } else if (navigation?.navigate) {
          navigation.navigate("Login");
        }
        return;
      }
      if (gen === startGen.current) { setFailed(true); setNet("disconnected"); }
    }
  }, [client, authToken, clearAuth, serial, releaseActiveDrag, navigation]);

  // Instance list is owned by the client identity, not by `start`.
  useEffect(() => {
    if (!client) return;
    client.instances().then(setInstances).catch((err: any) => {
      if (err?.status === 401) {
        if (clearAuth) clearAuth();
        if (navigation?.replace) {
          navigation.replace("Login");
        } else if (navigation?.navigate) {
          navigation.navigate("Login");
        }
      }
    });
  }, [client, navigation, clearAuth]);

  // WHEP session + input channel + adaptive quality follow `start`
  // (serial/client changes).
  useEffect(() => {
    start();
    return () => {
      releaseActiveDrag();
      startGen.current += 1;
      if (inputHealth.current) clearInterval(inputHealth.current);
      inputHealth.current = null;
      sampler.current?.stop();
      sampler.current = null;
      session.current?.close();
      adaptive.current?.stop();
    };
  }, [start, releaseActiveDrag]);

  // Open the stream in landscape by default, but still allow the user to
  // rotate freely (either landscape direction) while this screen is up.
  // Restore the app-wide portrait lock on exit.
  useEffect(() => {
    try {
      Promise.resolve(ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.LANDSCAPE)).catch(() => {});
    } catch {}
    return () => {
      try {
        Promise.resolve(ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP)).catch(() => {});
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
          twoFingerMoved.current = false;
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
            twoFingerMoved.current = false;
          }
          if (!twoFingerMoved.current) {
            if (Math.abs(gs.dx) <= 3 && Math.abs(gs.dy) <= 3) return;
            twoFingerMoved.current = true;
            // Treat the threshold-crossing movement as gesture activation,
            // not scrolling, so a HUD toggle never leaks a tiny scroll.
            scrollLast.current = gs.dy;
            return;
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
        if (isScroll.current && !twoFingerMoved.current) setStatsOn((v) => !v);
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
    if (instances.length < 2) return false;
    const i = instances.findIndex((x) => x.serial === serial);
    const j = i + dir;
    if (j < 0 || j >= instances.length) return false; // at the first/last instance — no wrap
    switchTo(instances[j]);
    return true;
  };

  const pickTier = (t: QualitySelection) => {
    if (t === "auto") adaptive.current?.setAuto();
    else adaptive.current?.pin(t);
    appliedTier.current = t;
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

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.streamBg }}>
      <View collapsable={false} style={{ flex: 1, marginHorizontal: STREAM_RAIL_WIDTH }} {...panResponder.panHandlers}
        onLayout={(e) => { rect.current = { width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height }; }}>
        {stream ? <VideoView stream={stream} /> : null}
      </View>

      {statsOn && !failed ? <StatsOverlay telemetry={telemetry} /> : null}

      <StreamRail visible={railOpen && overlay === null} telemetry={telemetry}
        connected={net === "connected"} keyboardOn={keyboardOn} settingsOn={overlay === "settings"}
        onDiagnostics={() => setStatsOn((v) => !v)}
        onKeyboard={() => (keyboardOn ? keyInput.current?.blur() : keyInput.current?.focus())}
        onSystemKey={(key) => session.current?.input.send({ type: "key", key })}
        onSettings={() => setOverlay(overlay === "settings" ? null : "settings")}
        onExit={() => {
            // Lock portrait before the screen-pop transition starts, not only
            // in the unmount cleanup below — requesting the geometry change
            // mid-transition can get silently dropped by iOS.
            try {
              Promise.resolve(ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP)).catch(() => {});
            } catch {}
            navigation.navigate("InstanceList");
          }}
        onWake={wakeRail} tick={tick} />
      {!railOpen && overlay === null ? <View testID="rail-wake-target" onTouchEnd={wakeRail}
        style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 12 }} /> : null}
      {overlay === null ? <SwapControl activeIndex={Math.max(0, instances.findIndex((x) => x.serial === serial))} count={instances.length}
        onOpen={() => setOverlay("drawer")} onCycle={cycleInstance} onWake={wakeRail} tick={tick} /> : null}

      <TextInput ref={keyInput} testID="stream-key-input" onKeyPress={(e) => sendKey(e.nativeEvent.key)}
        showSoftInputOnFocus
        onFocus={() => setKeyboardOn(true)}
        onBlur={() => setKeyboardOn(false)}
        returnKeyType="done"
        onSubmitEditing={() => keyInput.current?.blur()}
        style={{ position: "absolute", opacity: 0, height: 1, width: 1 }} />

      {overlay === "drawer" ? (
        <SwitchDrawer instances={instances} activeSerial={serial} previewSource={(value) => client.previewSource(value)} onPick={switchTo} onClose={() => setOverlay(null)} />
      ) : null}
      {overlay === "settings" ? (
        <SettingsModal preferences={preferences} onPickQuality={(value) => { pickTier(value); void updatePreferences({ quality: value }); }}
          onPreferences={(patch) => { void updatePreferences(patch); if (patch.showHudOnConnect !== undefined) setStatsOn(patch.showHudOnConnect); }} onClose={() => setOverlay(null)} />
      ) : null}
      {failed ? <ErrorOverlay onReconnect={reconnect} onBack={() => navigation.navigate("InstanceList")} reconnecting={reconnecting} /> : null}
    </View>
  );
}
