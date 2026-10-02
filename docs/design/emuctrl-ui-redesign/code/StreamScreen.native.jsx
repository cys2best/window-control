/**
 * EmuCtrl — Immersive Stream Screen (React Native / iOS)
 * Matches option 1b "Tactical" in `Stream Options.dc.html`.
 *
 * Deps: react-native-gesture-handler, react-native-reanimated,
 *       expo-blur (or @react-native-community/blur), expo-haptics.
 * Video: whatever WebRTC/H.264 view you already use — swap <StreamView/>.
 */
import React, { useEffect, useRef, useState } from 'react';
import { View, Text, Pressable, StyleSheet, Dimensions, Image } from 'react-native';
import { BlurView } from 'expo-blur';
import * as Haptics from 'expo-haptics';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, {
  useSharedValue, useAnimatedStyle, withTiming, withRepeat, Easing, runOnJS,
} from 'react-native-reanimated';

const C = {
  canvas: '#090a0f', surface: '#13161f', hairline: '#222738',
  cyan: '#00E5FF', tang: '#FF5722', mint: '#10B981', amber: '#edbb00',
  ink: '#E6EAF2', ink2: '#B7C0D0', muted: '#7A8496',
};
const MONO = 'JetBrainsMono-Regular';
const UI = 'SpaceGrotesk-Regular';
const S = { 1: 5, 2: 10, 3: 15, 4: 20, 6: 30, 8: 40 }; // Broadsheet density 1.25x

const DOCK = [
  { id: 'swap', glyph: '⇅', tag: 'SWAP', label: 'Quick-switch instances' },
  { id: 'keys', glyph: '⌨', tag: 'KEYS', label: 'Virtual keyboard' },
  { id: 'set', glyph: '⚙', tag: 'SET', label: 'Stream settings' },
  { id: 'hud', glyph: '◱', tag: 'HUD', label: 'Diagnostic HUD' },
  { id: 'exit', glyph: '✕', tag: 'EXIT', label: 'Back to dashboard' },
];

const IDLE_COLLAPSE_MS = 4000;
const BACK_COMMIT = 96;
const SWAP_STEP = 56;

export default function StreamScreen({
  instance, telemetry, onExit, onSwap, onOpenDrawer, onOpenSettings, onOpenKeyboard,
  haptics = true, StreamView,
}) {
  const [dockOpen, setDockOpen] = useState(true);
  const [hud, setHud] = useState(false);
  const [phase, setPhase] = useState('prefetch'); // 'prefetch' | 'slow' | 'idle'
  const idle = useRef(0);

  const tick = () => { if (haptics) Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); };
  const wake = () => {
    setDockOpen(true);
    clearTimeout(idle.current);
    idle.current = setTimeout(() => setDockOpen(false), IDLE_COLLAPSE_MS);
  };
  useEffect(() => { wake(); return () => clearTimeout(idle.current); }, []);

  /* invisible IDR prefetch — hold cached frame, sweep once, no text */
  const sweep = useSharedValue(-1.2);
  useEffect(() => {
    setPhase('prefetch');
    sweep.value = -1.2;
    sweep.value = withRepeat(withTiming(3.2, { duration: 200, easing: Easing.bezier(.4, 0, .6, 1) }), -1);
    const settle = setTimeout(() => setPhase('idle'), 200);
    const slow = setTimeout(() => setPhase((p) => (p === 'prefetch' ? 'slow' : p)), 450);
    return () => { clearTimeout(settle); clearTimeout(slow); };
  }, [instance.id]);

  const { width } = Dimensions.get('window');
  const sweepStyle = useAnimatedStyle(() => ({ transform: [{ translateX: sweep.value * width }, { skewX: '-14deg' }] }));

  /* gesture 1 — vertical swipe on the right dock rail cycles instances */
  const fired = useRef(0);
  const railSwipe = Gesture.Pan()
    .activeOffsetY([-12, 12])
    .onBegin(() => { fired.current = 0; })
    .onUpdate((e) => {
      const step = Math.trunc(e.translationY / SWAP_STEP);
      if (step !== fired.current) {
        fired.current = step;
        runOnJS(tick)();
        runOnJS(onSwap)(step > 0 ? 1 : -1);
      }
    })
    .onEnd(() => runOnJS(wake)());

  /* gesture 2 — swipe in from the left edge returns to the dashboard */
  const pull = useSharedValue(0);
  const backSwipe = Gesture.Pan()
    .activeOffsetX([-999, 24])
    .onUpdate((e) => { pull.value = Math.max(0, Math.min(e.translationX, BACK_COMMIT * 1.3)); })
    .onEnd(() => {
      if (pull.value >= BACK_COMMIT) runOnJS(onExit)();
      pull.value = withTiming(0, { duration: 180 });
    });
  const pullStyle = useAnimatedStyle(() => ({ width: pull.value, opacity: Math.min(1, pull.value / BACK_COMMIT) }));

  const act = { swap: onOpenDrawer, keys: onOpenKeyboard, set: onOpenSettings, hud: () => setHud((v) => !v), exit: onExit };
  const dot = phase === 'slow' ? C.amber : C.mint;

  return (
    <View style={st.root}>
      {/* letterboxed canvas */}
      <View style={st.stage}>
        {StreamView ? <StreamView style={st.video} /> : <View style={[st.video, { backgroundColor: '#0b0d13' }]} />}
        {phase !== 'idle' && (
          <>
            {instance.cachedFrameUri && <Image source={{ uri: instance.cachedFrameUri }} style={st.video} resizeMode="contain" />}
            <Animated.View pointerEvents="none" style={[st.sweep, { width: width * 0.24 }, sweepStyle]} />
          </>
        )}
      </View>

      {/* telemetry pill */}
      <BlurView intensity={40} tint="dark" style={st.pill}>
        <View style={st.pillCell}>
          <View style={[st.dot, { backgroundColor: dot, shadowColor: dot }]} />
          <Text style={st.mono}>{instance.res}</Text>
        </View>
        <View style={st.div} />
        <Text style={[st.mono, st.pillPad, { color: C.ink2 }]}>{instance.fps} FPS</Text>
        <View style={st.div} />
        <Text style={[st.mono, st.pillPad, { color: C.mint }]}>RTT {telemetry.rtt}ms</Text>
        <View style={st.div} />
        <Text style={[st.ui, st.pillPad, { color: C.muted, fontSize: 11.5 }]}>{telemetry.transport}</Text>
      </BlurView>

      {hud && (
        <BlurView intensity={40} tint="dark" style={st.hud}>
          {[['DECODE', telemetry.decodeMs, 1], ['NETWORK', telemetry.networkMs, 1],
            ['INPUT→HOST', telemetry.inputMs, 1], ['JITTER', telemetry.jitterMs, 0],
          ].map(([k, v, good]) => (
            <View key={k} style={st.hudRow}>
              <Text style={[st.mono, { color: C.muted, fontSize: 9.5 }]}>{k}</Text>
              <Text style={[st.mono, { color: good ? C.mint : C.ink, fontSize: 9.5 }]}>{v.toFixed(1)} ms</Text>
            </View>
          ))}
        </BlurView>
      )}

      {/* edge dock */}
      <GestureDetector gesture={railSwipe}>
        <View style={st.rail}>
          {dockOpen ? (
            <BlurView intensity={50} tint="dark" style={st.dock}>
              <View style={st.dockHead}>
                <View style={[st.dot, { backgroundColor: C.mint, shadowColor: C.mint }]} />
                <Text style={[st.mono, { color: C.mint, fontSize: 8 }]}>{telemetry.rtt}ms</Text>
              </View>
              {DOCK.map((b) => {
                const on = b.id === 'hud' && hud;
                return (
                  <Pressable key={b.id} accessibilityLabel={b.label}
                    onPress={() => { tick(); act[b.id](); wake(); }}
                    style={({ pressed }) => [st.btn, on && st.btnOn, pressed && { transform: [{ scale: 0.95 }] }]}>
                    <Text style={{ fontSize: 15, color: on ? C.cyan : C.ink2 }}>{b.glyph}</Text>
                    <Text style={[st.mono, { fontSize: 6.5, color: on ? C.cyan : C.ink2, opacity: 0.8 }]}>{b.tag}</Text>
                  </Pressable>
                );
              })}
              <Pressable onPress={() => setDockOpen(false)} style={st.collapse}>
                <Text style={{ color: C.muted, fontSize: 11 }}>›</Text>
              </Pressable>
            </BlurView>
          ) : (
            <Pressable onPress={wake} accessibilityLabel="Expand dock">
              <BlurView intensity={50} tint="dark" style={st.stub}>
                <View style={[st.dot, { backgroundColor: C.mint, shadowColor: C.mint }]} />
                <Text style={[st.mono, { color: C.ink2, fontSize: 9 }]}>{telemetry.rtt}ms</Text>
                <Text style={{ color: C.muted, fontSize: 10 }}>‹</Text>
              </BlurView>
            </Pressable>
          )}
        </View>
      </GestureDetector>

      {/* left edge back-swipe */}
      <GestureDetector gesture={backSwipe}>
        <View style={st.leftEdge}><Animated.View style={[st.pullSheet, pullStyle]} /></View>
      </GestureDetector>
    </View>
  );
}

const st = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#000' },
  stage: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#000' },
  video: { ...StyleSheet.absoluteFillObject },
  sweep: { position: 'absolute', top: -40, bottom: -40, backgroundColor: 'rgba(255,255,255,0.09)' },

  pill: { position: 'absolute', left: S[4], top: S[3], flexDirection: 'row', alignItems: 'center',
    borderRadius: 999, overflow: 'hidden', borderWidth: 1, borderColor: 'rgba(255,255,255,0.09)' },
  pillCell: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, paddingVertical: 8 },
  pillPad: { paddingHorizontal: 11, paddingVertical: 8 },
  div: { width: 1, height: 14, backgroundColor: 'rgba(255,255,255,0.1)' },
  mono: { fontFamily: MONO, fontSize: 10, letterSpacing: 0.8, color: C.ink },
  ui: { fontFamily: UI, color: C.ink },
  dot: { width: 6, height: 6, borderRadius: 999, shadowOpacity: 0.9, shadowRadius: 5 },

  hud: { position: 'absolute', left: S[4], top: 57, width: 196, gap: 6, padding: 12, borderRadius: 12,
    overflow: 'hidden', borderWidth: 1, borderColor: 'rgba(255,255,255,0.08)' },
  hudRow: { flexDirection: 'row', justifyContent: 'space-between' },

  rail: { position: 'absolute', right: 0, top: 0, bottom: 0, width: 60, alignItems: 'center', justifyContent: 'center' },
  dock: { gap: 7, padding: 8, borderRadius: 22, overflow: 'hidden', borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)' },
  dockHead: { alignItems: 'center', gap: 3, paddingTop: 7, paddingBottom: 9,
    borderBottomWidth: 1, borderBottomColor: 'rgba(255,255,255,0.08)' },
  btn: { width: 44, height: 44, borderRadius: 13, alignItems: 'center', justifyContent: 'center', gap: 2 },
  btnOn: { backgroundColor: 'rgba(0,229,255,0.16)' },
  collapse: { height: 22, alignItems: 'center', justifyContent: 'center' },
  stub: { flexDirection: 'row', alignItems: 'center', gap: 7, paddingHorizontal: 11, paddingVertical: 8,
    borderRadius: 999, overflow: 'hidden', borderWidth: 1, borderColor: 'rgba(255,255,255,0.1)' },

  leftEdge: { position: 'absolute', left: 0, top: 0, bottom: 0, width: 28 },
  pullSheet: { position: 'absolute', left: 0, top: 0, bottom: 0, backgroundColor: 'rgba(9,10,15,0.8)' },
});
