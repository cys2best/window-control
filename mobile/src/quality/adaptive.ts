import { shouldDowngrade, nextBadStreak, stepTier, DOWNGRADE_STREAK } from "./tiers";

// Consecutive sample() ticks with zero new frames (while otherwise sampling
// fine) before we call the stream frozen. At the default 5s sampleMs that's
// ~10s of a genuinely stalled decoder — long enough to not fire on a normal
// brief stutter, short enough to recover before the user notices too much.
const STALL_TICKS = 2;

type Opts = {
  serial: string; onApply: (tier: string) => void;
  onStall?: () => void; sampleMs?: number; now?: () => number;
};

export function makeAdaptive(opts: Opts) {
  const now = opts.now || Date.now;
  let pc: any = null;
  let timer: any = null;
  let current = "720";
  let badStreak = 0;
  let manualUntil = 0;
  let lastChange = 0;
  let lastFrames = -1;
  let stallStreak = 0;

  const apply = (tier: string) => {
    if (tier === current) return;
    current = tier;
    lastChange = now();
    opts.onApply(tier);
  };

  const tick = async () => {
    if (!pc) return;
    let loss = 0, rtt = 0, seen = false, frames = -1;
    const stats = await pc.getStats();
    stats.forEach((r: any) => {
      if (r.type === "inbound-rtp" && r.kind === "video") {
        const recv = r.packetsReceived || 0, lost = r.packetsLost || 0;
        if (recv + lost > 0) loss = lost / (recv + lost);
        frames = r.framesDecoded ?? -1;
        seen = true;
      }
      if (r.type === "candidate-pair" && r.state === "succeeded" && r.currentRoundTripTime != null) {
        rtt = r.currentRoundTripTime * 1000;
      }
    });
    if (!seen) return;
    if (frames >= 0) {
      if (lastFrames >= 0 && frames <= lastFrames) {
        stallStreak++;
        if (stallStreak >= STALL_TICKS) { stallStreak = 0; lastFrames = -1; opts.onStall?.(); return; }
      } else {
        stallStreak = 0;
      }
      lastFrames = frames;
    }
    if (now() < manualUntil) return;
    if (now() - lastChange < 10000) return;
    if (shouldDowngrade(loss, rtt)) {
      badStreak = nextBadStreak(badStreak, true);
      if (badStreak >= DOWNGRADE_STREAK) { badStreak = 0; apply(stepTier(current, -1)); }
    } else {
      badStreak = 0;
    }
  };

  return {
    start(peer: any) { pc = peer; badStreak = 0; stallStreak = 0; lastFrames = -1; clearInterval(timer); timer = setInterval(tick, opts.sampleMs ?? 5000); },
    stop() { clearInterval(timer); timer = null; },
    pin(tier: string) { current = tier; manualUntil = now() + 60000; lastChange = now(); opts.onApply(tier); },
    setAuto() { manualUntil = 0; },
    current() { return current; },
    _tick: tick,
  };
}
