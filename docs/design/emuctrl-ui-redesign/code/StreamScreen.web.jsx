/**
 * EmuCtrl — Immersive Stream Screen (web / PWA)
 * React 18 + Tailwind. Matches option 1b "Tactical" in `Stream Options.dc.html`.
 *
 * Tailwind config additions this file assumes:
 *   colors: { canvas:'#090a0f', surface:'#13161f', hairline:'#222738',
 *             cyan:'#00E5FF', tang:'#FF5722', mint:'#10B981',
 *             ink:'#E6EAF2', ink2:'#B7C0D0', muted:'#7A8496' }
 *   fontFamily: { ui:['Space Grotesk','system-ui'], mono:['JetBrains Mono','monospace'],
 *                 display:['Source Serif 4','serif'] }
 *   spacing follows Broadsheet density 1.25x: 5 / 10 / 15 / 20 / 30 / 40 px
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';

const DOCK = [
  { id: 'swap', glyph: '⇅', tag: 'SWAP', label: 'Quick-switch instances' },
  { id: 'keys', glyph: '⌨', tag: 'KEYS', label: 'Virtual keyboard' },
  { id: 'set', glyph: '⚙', tag: 'SET', label: 'Stream settings' },
  { id: 'hud', glyph: '◱', tag: 'HUD', label: 'Diagnostic HUD' },
  { id: 'exit', glyph: '✕', tag: 'EXIT', label: 'Back to dashboard' },
];

const IDLE_COLLAPSE_MS = 4000;
const BACK_ARM_PX = 24;
const BACK_COMMIT_PX = 96;
const SWAP_STEP_PX = 56;

export default function StreamScreen({
  instance,               // { id, name, game, res, fps, cachedFrameUrl }
  telemetry,              // { rtt, decodeMs, networkMs, inputMs, jitterMs, bitrateMbps, transport }
  onExit, onSwap, onOpenDrawer, onOpenSettings, onOpenKeyboard,
  haptics = true,
}) {
  const [dockOpen, setDockOpen] = useState(true);
  const [hud, setHud] = useState(false);
  /** 'idle' | 'prefetch' | 'slow' — the invisible IDR handshake state */
  const [phase, setPhase] = useState('prefetch');
  const idle = useRef(0);

  const tick = useCallback(() => {
    if (haptics && navigator.vibrate) navigator.vibrate(10);
  }, [haptics]);

  /* ── dock auto-collapse after 4s of no touch ───────────────────────── */
  const wake = useCallback(() => {
    setDockOpen(true);
    clearTimeout(idle.current);
    idle.current = setTimeout(() => setDockOpen(false), IDLE_COLLAPSE_MS);
  }, []);
  useEffect(() => { wake(); return () => clearTimeout(idle.current); }, [wake]);

  /* ── invisible prefetch: hold last frame, one shimmer sweep ────────── */
  useEffect(() => {
    setPhase('prefetch');
    const settle = setTimeout(() => setPhase('idle'), 200);      // typical IDR prefetch
    const slow = setTimeout(() => setPhase((p) => (p === 'prefetch' ? 'slow' : p)), 450);
    return () => { clearTimeout(settle); clearTimeout(slow); };
  }, [instance.id]);

  /* ── the only two gestures we claim, both on the bezel ─────────────── */
  const drag = useRef(null);
  const onTouchStart = (e) => {
    const t = e.touches[0];
    const w = window.innerWidth;
    drag.current = { x0: t.clientX, y0: t.clientY, edge: t.clientX < 28 ? 'left' : t.clientX > w - 44 ? 'right' : null, fired: 0 };
  };
  const onTouchMove = (e) => {
    const d = drag.current; if (!d || !d.edge) return;
    const t = e.touches[0];
    if (d.edge === 'left') {
      const dx = t.clientX - d.x0;
      if (dx > BACK_ARM_PX) setBackPull(Math.min(dx, BACK_COMMIT_PX * 1.3));
    } else {
      const steps = Math.trunc((t.clientY - d.y0) / SWAP_STEP_PX);
      if (steps !== d.fired) { d.fired = steps; tick(); onSwap(steps > 0 ? 1 : -1); }
    }
  };
  const onTouchEnd = () => {
    const d = drag.current;
    if (d && d.edge === 'left' && backPull >= BACK_COMMIT_PX) onExit();
    setBackPull(0);
    drag.current = null;
    wake();
  };
  const [backPull, setBackPull] = useState(0);

  const act = {
    swap: onOpenDrawer, keys: onOpenKeyboard, set: onOpenSettings,
    hud: () => setHud((v) => !v), exit: onExit,
  };
  const ms = (n) => `${n.toFixed(1)} ms`;

  return (
    <div
      className="relative h-screen w-screen overflow-hidden bg-black font-ui text-ink select-none touch-none"
      onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd}
      onPointerDown={wake}
    >
      {/* video canvas — letterboxed, never stretched */}
      <div className="absolute inset-0 grid place-items-center bg-black">
        <div className="relative aspect-video max-h-full w-full overflow-hidden bg-[#0b0d13]">
          <video id="wc-stream" className="h-full w-full object-contain" autoPlay muted playsInline />
          {phase !== 'idle' && (
            <>
              {instance.cachedFrameUrl && (
                <img src={instance.cachedFrameUrl} alt="" className="absolute inset-0 h-full w-full object-contain" />
              )}
              <div
                className="pointer-events-none absolute -inset-y-1/5 w-1/4 bg-gradient-to-r from-transparent via-white/[.09] to-transparent"
                style={{
                  animation: `wc-shimmer ${phase === 'slow' ? 900 : 200}ms cubic-bezier(.4,0,.6,1) infinite`,
                  transform: 'skewX(-14deg)',
                }}
              />
            </>
          )}
        </div>
      </div>

      {/* telemetry pill — two registers: mono numbers, plain-English transport */}
      <div className="absolute left-5 top-[15px] flex items-center overflow-hidden rounded-full border border-white/[.09] bg-[#06070b]/55 shadow-[0_6px_22px_rgba(0,0,0,.5)] backdrop-blur-[18px]">
        <div className="flex items-center gap-2 px-3 py-2">
          <span className={`h-1.5 w-1.5 rounded-full ${phase === 'slow' ? 'bg-[#edbb00]' : 'bg-mint'} shadow-[0_0_9px_currentColor] animate-pulse`} />
          <span className="font-mono text-[10px] font-medium tracking-[.08em]">{instance.res}</span>
        </div>
        <Divider /><Cell>{instance.fps} FPS</Cell>
        <Divider /><Cell className="text-mint">RTT {telemetry.rtt}ms</Cell>
        <Divider /><span className="px-[13px] py-[7px] text-[11.5px] text-muted">{telemetry.transport}</span>
      </div>

      {hud && (
        <dl className="absolute left-5 top-[57px] flex w-[196px] flex-col gap-1.5 rounded-xl border border-white/[.08] bg-[#06070b]/60 p-[11px_13px] font-mono text-[9.5px] text-muted backdrop-blur-[18px]">
          <Row k="DECODE" v={ms(telemetry.decodeMs)} good />
          <Row k="NETWORK" v={ms(telemetry.networkMs)} good />
          <Row k="INPUT→HOST" v={ms(telemetry.inputMs)} good />
          <Row k="JITTER" v={ms(telemetry.jitterMs)} />
          <Row k="BITRATE" v={`${telemetry.bitrateMbps} Mb/s`} />
          <div className="mt-[3px] h-[3px] overflow-hidden rounded-full bg-[#141824]">
            <div className="h-full bg-mint" style={{ width: `${Math.min(100, telemetry.bitrateMbps * 2)}%` }} />
          </div>
        </dl>
      )}

      {/* edge dock — right thumb rail */}
      <div className="absolute right-4 top-1/2 -translate-y-1/2">
        {dockOpen ? (
          <div className="flex flex-col gap-[7px] rounded-[22px] border border-white/10 bg-canvas/[.66] p-2 shadow-[0_10px_34px_rgba(0,0,0,.6)] backdrop-blur-[20px] transition-[width,opacity] duration-200 ease-[cubic-bezier(.2,.8,.2,1)]">
            <div className="flex flex-col items-center gap-[3px] border-b border-white/[.08] pb-[9px] pt-[7px]">
              <span className="h-[7px] w-[7px] rounded-full bg-mint shadow-[0_0_9px_#10B981]" />
              <span className="font-mono text-[8px] tracking-[.06em] text-mint">{telemetry.rtt}ms</span>
            </div>
            {DOCK.map((b) => {
              const on = b.id === 'hud' && hud;
              return (
                <button
                  key={b.id} aria-label={b.label} title={b.label}
                  onClick={() => { tick(); act[b.id](); wake(); }}
                  className={`flex h-11 w-11 flex-col items-center justify-center gap-0.5 rounded-[13px] transition-colors
                    active:scale-95 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan
                    ${on ? 'bg-cyan/[.16] text-cyan' : 'text-ink2 hover:bg-cyan/[.14] hover:text-cyan'}`}
                >
                  <span className="text-[15px] leading-none">{b.glyph}</span>
                  <span className="font-mono text-[6.5px] tracking-[.06em] opacity-80">{b.tag}</span>
                </button>
              );
            })}
            <button onClick={() => setDockOpen(false)} aria-label="Collapse dock"
              className="h-[22px] text-[11px] text-muted hover:text-cyan">›</button>
          </div>
        ) : (
          <button onClick={wake} aria-label="Expand dock"
            className="flex items-center gap-[7px] rounded-full border border-white/10 bg-canvas/60 px-[11px] py-2 backdrop-blur-[20px] hover:border-cyan/50">
            <span className="h-1.5 w-1.5 rounded-full bg-mint shadow-[0_0_8px_#10B981]" />
            <span className="font-mono text-[9px] text-ink2">{telemetry.rtt}ms</span>
            <span className="text-[10px] text-muted">‹</span>
          </button>
        )}
      </div>

      {/* left-edge back-swipe wick + rubber-band preview */}
      <div className="pointer-events-none absolute inset-y-0 left-0 w-4 bg-gradient-to-r from-cyan/10 to-transparent" />
      {backPull > 0 && (
        <div className="pointer-events-none absolute inset-y-0 left-0 bg-canvas/80 backdrop-blur-sm"
          style={{ width: backPull, opacity: Math.min(1, backPull / BACK_COMMIT_PX) }} />
      )}

      <style>{`@keyframes wc-shimmer{0%{transform:translateX(-120%) skewX(-14deg)}100%{transform:translateX(320%) skewX(-14deg)}}`}</style>
    </div>
  );
}

const Divider = () => <span className="h-3.5 w-px bg-white/10" />;
const Cell = ({ children, className = '' }) => (
  <span className={`px-[11px] py-2 font-mono text-[10px] tracking-[.06em] text-ink2 ${className}`}>{children}</span>
);
const Row = ({ k, v, good }) => (
  <div className="flex justify-between"><dt>{k}</dt><dd className={good ? 'text-mint' : 'text-ink'}>{v}</dd></div>
);
