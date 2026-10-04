import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, TextInput, PanResponder } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import type { VideoViewComponent } from "../video/VideoView";
import { useServer, connectEngineSession, EngineSession, normalizeCoords, makeAdaptive, makeStallWatchdog, makeTelemetrySampler, makeMeasurementRecorder, TIER_ORDER, DEFAULT_STREAM_PREFERENCES, type QualitySelection, type StreamTelemetry, type RemoteRunEvidence } from "@wc/core";
import { theme } from "../theme/tokens";
import { StreamRail, STREAM_RAIL_WIDTH } from "../components/StreamRail";
import { SwapControl } from "../components/SwapControl";
import { SettingsModal } from "../components/SettingsModal";
import { SwitchDrawer } from "../components/SwitchDrawer";
import { StatsOverlay } from "../components/StatsOverlay";
import { ErrorOverlay } from "../components/ErrorOverlay";

type Net = "connected" | "connecting" | "disconnected";
const emptyTelemetry: StreamTelemetry = { rttMs: null, loss: null, decodeMs: null, networkMs: null, inputMs: null, jitterMs: null, bitrateMbps: null, droppedFrames: null, transport: "unknown", route: "unknown", addressFamily: "unknown", relayProtocol: "unknown", sourceWidth: null, sourceHeight: null, decodedWidth: null, decodedHeight: null, decodedFps: null, framesDecoded: null, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null };

// Encoding targets configured by src/config.py; receive bitrate and source dimensions remain measurements.
const targetBitrates: Record<string, number> = { "360": .8, "480": 2, "720": 4, "1080": 8, "1440": 12 };
export function Stream({
  route,
  navigation,
  RTCImpl,
  VideoView,
  performHaptic,
  onExportMeasurement,
}: {
  route: any;
  navigation: any;
  RTCImpl: any;
  VideoView: VideoViewComponent;
  performHaptic?: () => void;
  onExportMeasurement?: (run: RemoteRunEvidence) => void | Promise<void>;
}) {
  const { client, clearAuth, preferences = DEFAULT_STREAM_PREFERENCES, updatePreferences = () => {} } = useServer() as any;
  const { serial } = route.params;
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [net, setNet] = useState<Net>("connecting");
  const [failed, setFailed] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [overlay, setOverlay] = useState<null | "settings" | "drawer">(null);
  const [keyboardOn, setKeyboardOn] = useState(false);
  const [statsOn, setStatsOn] = useState(false);
  const [instances, setInstances] = useState<any[]>([]);
  const [telemetry, setTelemetry] = useState<StreamTelemetry>(emptyTelemetry);
  const [capacityText, setCapacityText] = useState("");
  const [expectedRoute, setExpectedRoute] = useState("unknown");
  const [exportState, setExportState] = useState<"idle" | "sharing" | "failed">("idle");
  const capacity = capacityText.trim() === "" ? null : Number(capacityText);
  const measurementMeta = useRef({ capacityMbps: Number.isFinite(capacity) && capacity !== null && capacity > 0 ? capacity : null, expectedRoute });
  measurementMeta.current = { capacityMbps: Number.isFinite(capacity) && capacity !== null && capacity > 0 ? capacity : null, expectedRoute };
  const measurement = useRef<{ recorder: ReturnType<typeof makeMeasurementRecorder>; meta: string; target: number } | null>(null);
  const downgrades = useRef({ client, serial, count: 0 });
  if (downgrades.current.client !== client || downgrades.current.serial !== serial) downgrades.current = { client, serial, count: 0 };
  const [railOpen, setRailOpen] = useState(true);
  const [hudHeight, setHudHeight] = useState(0);
  const rect = useRef({ width: 1, height: 1 });
  const content = useRef({ w: 1, h: 1 });
  const session = useRef<EngineSession | null>(null);
  const adaptive = useRef<any>(null);
  const manualQualityChange = useRef(false);
  const pinQuality = (tier: string) => {
    manualQualityChange.current = true;
    try { adaptive.current?.pin(tier); } finally { manualQualityChange.current = false; }
  };
  const appliedTier = useRef<QualitySelection | null>(null);
  const currentQuality = useRef<QualitySelection>(preferences.quality);
  const scrollLast = useRef(0);
  const keyInput = useRef<TextInput>(null);
  const dragStarted = useRef(false);
  const isScroll = useRef(false);
  const lastTouch = useRef({ x: 0, y: 0 });
  const twoFingerMoved = useRef(false);

  currentQuality.current = preferences.quality;
  // Hosts (the web pages) build `navigation` inline, so its identity changes
  // on every parent render, such as each 30s host probe. Reading it through
  // a ref keeps it out of the effect deps below; as a dep it tore the live
  // session down and reconnected on every one of those renders.
  const navigationRef = useRef(navigation);
  navigationRef.current = navigation;

  const wakeRail = useCallback(() => setRailOpen(true), []);
  const tick = useCallback(() => {
    if (preferences.haptics) performHaptic?.();
  }, [performHaptic, preferences.haptics]);

  useEffect(() => {
    setStatsOn(preferences.showHudOnConnect);
    if (adaptive.current && appliedTier.current !== preferences.quality) {
      if (preferences.quality === "auto") adaptive.current.setAuto();
      else pinQuality(preferences.quality);
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
  const startingTier = useRef<{ client: any; serial: string; initial: string; native: string } | null>(null);
  const targetClient = useRef(client);
  targetClient.current = client;
  const owner = useRef<{ client: any; retire: () => Promise<void> } | null>(null);
  const retiring = useRef<Promise<void>>(Promise.resolve());
  const retiringClient = useRef(client);
  const start = useCallback(async (qualityTier?: string) => {
    if (!client) return;
    const gen = ++startGen.current;
    const initiatingQuality = currentQuality.current;
    const controller = new AbortController();
    // Includes retirement, selection, gathering, answer and adoption.
    const deadline = performance.now() + 30_000;
    const previous = owner.current;
    if (retiringClient.current !== client) {
      // Provider replacement disposes its old client. Its cleanup cannot
      // affect, or prevent admission on, a different installation/client.
      retiring.current = Promise.resolve();
      retiringClient.current = client;
    }
    if (previous && previous.client === client) {
      retiring.current = Promise.all([retiring.current, previous.retire()]).then(() => {});
      void retiring.current.catch(() => {});
    }
    let selected: any = null, peer: EngineSession | null = null;
    let retired = false;
    let source: { width: number; height: number } | null = null;
    let cleanup: Promise<void> | undefined, selectionCleanup: Promise<void> | undefined;
    let qualityCommand: Promise<void> | undefined;
    let health: ReturnType<typeof setInterval> | undefined;
    let renewal: ReturnType<typeof setTimeout> | undefined;
    let sample: ReturnType<typeof makeTelemetrySampler> | undefined;
    let watchdog: ReturnType<typeof makeStallWatchdog> | undefined;
    let qualityController: ReturnType<typeof makeAdaptive> | undefined;
    const current = () => !retired && gen === startGen.current && targetClient.current === client;
    const closeSelection = () => selectionCleanup ??= Promise.resolve().then(() => client.closeSession(selected));
    const scope = {
      client,
      retire() {
        if (cleanup) return cleanup;
        retired = true;
        source = null;
        clearInterval(health); clearTimeout(renewal);
        sample?.stop(); watchdog?.stop(); qualityController?.stop();
        if (adaptive.current === qualityController) adaptive.current = null;
        if (peer) {
          releaseActiveDrag(peer.input);
          if (session.current === peer) session.current = null;
        }
        // A pending remote transport and its client share exact close dedup.
        const closing = peer ? peer.close() : selected?.kind === "remote" ? closeSelection() : Promise.resolve();
        // The quality API is not abortable. Fence its completion before a
        // successor select so a retired mutation cannot reconfigure that peer.
        cleanup = Promise.all([closing, qualityCommand?.catch(() => {})]).then(() => {});
        controller.abort();
        void cleanup.catch(() => {});
        return cleanup;
      },
    };
    owner.current = scope;
    setFailed(false); setNet("connecting");
    setTelemetry(emptyTelemetry);
    measurement.current = null;
    // Race even platform calls that do not implement AbortSignal. Their late
    // continuation still owns and releases any selected/negotiated resource.
    const wait = <T,>(promise: Promise<T>): Promise<T> => new Promise((resolve, reject) => {
      const abort = () => { reject(new Error("canceled")); };
      controller.signal.addEventListener("abort", abort, { once: true });
      promise.then(resolve, reject).finally(() => controller.signal.removeEventListener("abort", abort));
      if (controller.signal.aborted) abort();
    });
    const timeout = setTimeout(() => controller.abort(), Math.max(0, deadline - performance.now()));
    try {
      if (client.kind === "remote") await wait(retiring.current);
      if (!current() || controller.signal.aborted) return;
      const rememberedTier = startingTier.current;
      const tiers = rememberedTier && rememberedTier.client === client && rememberedTier.serial === serial ? rememberedTier : null;
      const requestedTier = qualityTier ?? (tiers ? (currentQuality.current === "auto" ? tiers.initial : currentQuality.current) : undefined);
      if (client.kind === "remote" && requestedTier !== undefined && (qualityTier !== undefined || requestedTier !== tiers?.native)) {
        qualityCommand = Promise.resolve(client.setQuality(serial, requestedTier));
        await wait(qualityCommand);
      }
      if (!current() || controller.signal.aborted) return;
      const selecting = client.kind === "remote" ? client.select(serial, { signal: controller.signal, deadline }) : client.select(serial);
      const sel = await wait(Promise.resolve(selecting).then(async (value: any) => {
        selected = value; // Own the exact selection before any further await.
        if (!current() || controller.signal.aborted) {
          if (value.kind === "remote") await closeSelection();
          throw new Error("canceled");
        }
        return value;
      }));
      if (client.kind === "remote") {
        startingTier.current = { client, serial, initial: tiers?.initial ?? sel.tier, native: sel.tier };
      }
      content.current = { w: sel.w, h: sel.h };
      source = { width: sel.w, height: sel.h };
      let nextStream: any = null;
      const connecting = connectEngineSession({
        selection: sel, client: client.kind === "remote" ? client : undefined,
        deadline, signal: controller.signal, RTCImpl,
        onStream: value => { if (current()) { if (peer) setStream(value); else nextStream = value; } },
        onInputRtt: ms => { if (current()) sample?.setInputRtt(ms); },
        onState: state => {
          if (!current()) return;
          setNet(state);
          // Only an adopted peer may initiate recovery. Setup failures are
          // reported by the setup promise and must not recurse.
          if (state === "disconnected" && peer) void start();
        },
      }).then(async value => {
        if (!current() || controller.signal.aborted) { await value.close(); throw new Error("canceled"); }
        return value;
      });
      const s = await wait(connecting);
      if (!current() || controller.signal.aborted) { void s.close().catch(() => {}); return; }
      peer = s;
      session.current = s;
      let target = targetBitrates[sel.tier] ?? NaN;
      const resetMeasurement = () => {
        const meta = measurementMeta.current;
        measurement.current = { recorder: makeMeasurementRecorder({ ...meta, targetBitrateMbps: target }), meta: JSON.stringify(meta), target };
      };
      resetMeasurement();
      sample = makeTelemetrySampler({ pc: s.pc, transport: s.kind,
        sourceDimensions: () => current() ? source : null,
        onSample: value => {
          if (!current()) return;
          setTelemetry(value);
          if (measurement.current?.meta !== JSON.stringify(measurementMeta.current)) resetMeasurement();
          measurement.current?.recorder.add(value, downgrades.current.count);
        },
      });
      sample.start();
      watchdog = makeStallWatchdog({ pc: s.pc, onStall: () => { if (current()) void client.keyframe(serial); } });
      watchdog.start();
      if (nextStream || s.stream) setStream(nextStream || s.stream);
      s.input.send({ type: "idr" });
      health = setInterval(() => { if (current()) s.input.send({ type: "echo", t: Date.now() }); }, 2000);
      let nativeTier = sel.tier;
      qualityController = makeAdaptive({
        serial, initialTier: qualityTier ?? sel.tier,
        onApply: tier => {
          if (!current() || adaptive.current !== qualityController) return;
          const priorTier = nativeTier;
          if (tier !== priorTier && currentQuality.current === "auto" && !manualQualityChange.current && priorTier !== undefined
            && TIER_ORDER.indexOf(tier as any) >= 0 && TIER_ORDER.indexOf(tier as any) < TIER_ORDER.indexOf(priorTier as any)) downgrades.current.count += 1;
          if (client.kind === "remote") {
            if (tier !== nativeTier) { nativeTier = tier; void start(tier); }
          } else {
            if (tier !== nativeTier) {
              nativeTier = tier;
              source = null;
              target = targetBitrates[tier] ?? NaN;
              resetMeasurement();
            }
            void client.setQuality(serial, tier);
          }
        },
        onStall: () => { if (current()) s.input.send({ type: "idr" }); },
      });
      adaptive.current = qualityController;
      qualityController.start(s.pc);
      const quality = currentQuality.current;
      if (quality === "auto") qualityController.setAuto();
      // Preserve a downgrade only while its initiating preference still
      // applies. A newer manual preference must use the replacement path.
      else pinQuality(quality === initiatingQuality ? qualityTier ?? quality : quality);
      appliedTier.current = quality;
      if (sel.kind === "remote" && current()) {
        // expires_at is Unix seconds; credentials were issued 3600s earlier.
        const due = (sel.expires_at - 3600 + sel.renew_after) * 1000;
        renewal = setTimeout(() => { if (current()) void start(); }, Math.max(0, due - Date.now()));
      }
    } catch (error: any) {
      const active = current();
      void scope.retire().catch(() => {});
      if (!active) return;
      if (error?.status === 401 || error?.code === "not_paired") {
        if (clearAuth) await clearAuth();
        if (gen !== startGen.current || targetClient.current !== client) return;
        const nav = navigationRef.current;
        if (nav?.replace) nav.replace("Pair"); else nav?.navigate?.("Pair");
      } else { setFailed(true); setNet("disconnected"); }
    } finally {
      clearTimeout(timeout);
      if (controller.signal.aborted && !retired) void scope.retire().catch(() => {});
    }
  }, [client, clearAuth, serial, RTCImpl, releaseActiveDrag]);

  useEffect(() => {
    if (!client) return;
    let active = true;
    client.instances().then((value: any[]) => { if (active && targetClient.current === client) setInstances(value); }).catch(async (err: any) => {
      if (!active || targetClient.current !== client) return;
      if (err?.status === 401 || err?.code === "not_paired") {
        if (clearAuth) await clearAuth();
        if (!active || targetClient.current !== client) return;
        const nav = navigationRef.current;
        if (nav?.replace) nav.replace("Pair"); else nav?.navigate?.("Pair");
      }
    });
    return () => { active = false; };
  }, [client, clearAuth]);

  useEffect(() => {
    void start();
    return () => {
      startGen.current += 1;
      if (owner.current) {
        retiringClient.current = owner.current.client;
        retiring.current = Promise.all([retiring.current, owner.current.retire()]).then(() => {});
        void retiring.current.catch(() => {});
      }
      owner.current = null;
    };
  }, [start]);

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
    else pinQuality(t);
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

  const exportMeasurement = async () => {
    if (!onExportMeasurement || exportState === "sharing") return;
    setExportState("sharing");
    try {
      const capture = measurement.current;
      const meta = measurementMeta.current;
      const run = capture && capture.meta === JSON.stringify(meta) ? capture.recorder.exportRun()
        : makeMeasurementRecorder({ ...meta, targetBitrateMbps: capture?.target ?? NaN }).exportRun();
      await onExportMeasurement(run);
      setExportState("idle");
    } catch { setExportState("failed"); }
  };
  const hudShown = statsOn && !failed;

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.streamBg }}>
      <View collapsable={false} style={{ flex: 1, marginHorizontal: STREAM_RAIL_WIDTH }} {...panResponder.panHandlers}
        onLayout={(e) => { rect.current = { width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height }; }}>
        {stream ? <VideoView stream={stream} /> : null}
      </View>

      {hudShown ? <StatsOverlay telemetry={telemetry} onHeight={setHudHeight}
        onExport={onExportMeasurement ? exportMeasurement : undefined} exportState={exportState}
        capacityText={capacityText} onCapacityText={setCapacityText} expectedRoute={expectedRoute} onExpectedRoute={setExpectedRoute} /> : null}

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
      {overlay === null ? <SwapControl topInset={hudShown ? hudHeight : 0} activeIndex={Math.max(0, instances.findIndex((x) => x.serial === serial))} count={instances.length}
        onOpen={() => setOverlay("drawer")} onCycle={cycleInstance} onWake={wakeRail} tick={tick} /> : null}

      <TextInput ref={keyInput} testID="stream-key-input" onKeyPress={(e) => sendKey(e.nativeEvent.key)}
        showSoftInputOnFocus
        onFocus={() => setKeyboardOn(true)}
        onBlur={() => setKeyboardOn(false)}
        returnKeyType="done"
        onSubmitEditing={() => keyInput.current?.blur()}
        style={{ position: "absolute", opacity: 0, height: 1, width: 1 }} />

      {overlay === "drawer" ? (
        <SwitchDrawer instances={instances} activeSerial={serial} client={client} onPick={switchTo} onClose={() => setOverlay(null)} />
      ) : null}
      {overlay === "settings" ? (
        <SettingsModal preferences={preferences} onPickQuality={(value) => { pickTier(value); void updatePreferences({ quality: value }); }}
          onPreferences={(patch) => { void updatePreferences(patch); if (patch.showHudOnConnect !== undefined) setStatsOn(patch.showHudOnConnect); }} onClose={() => setOverlay(null)} />
      ) : null}
      {failed ? <ErrorOverlay onReconnect={reconnect} onBack={() => navigation.navigate("InstanceList")} reconnecting={reconnecting} /> : null}
    </View>
  );
}
