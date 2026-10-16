import { shouldDowngrade, nextBadStreak, stepTier, DOWNGRADE_STREAK, TIER_ORDER } from "./tiers";

// Consecutive sample() ticks with zero new frames (while otherwise sampling
// fine) before we call the stream frozen. At the default 5s sampleMs that's
// ~10s of a genuinely stalled decoder — long enough to not fire on a normal
// brief stutter, short enough to recover before the user notices too much.
const STALL_TICKS = 2;

// Share of one sampling interval's packets lost before the link is treated
// as unable to carry the stream at all (a relayed path, for example). One
// tier down would not help there, and waiting out a 15s streak per tier
// leaves the picture frozen for most of a minute.
const SEVERE_INTERVAL_LOSS = 0.2;

type Opts = {
  serial: string; onApply: (tier: string) => void;
  onStall?: () => void; sampleMs?: number; now?: () => number;
  // The tier the host is encoding at (from select). Without it the first
  // "step down" from the assumed default could land above the real tier.
  initialTier?: string;
};

export function makeAdaptive(opts: Opts) {
  const now = opts.now || Date.now;
  let pc: any = null;
  let timer: any = null;
  let current = opts.initialTier && (TIER_ORDER as readonly string[]).includes(opts.initialTier) ? opts.initialTier : "720";
  let lastReceived = -1;
  let lastLost = -1;
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
    // Loss is measured inside the sampling interval. The cumulative counters
    // never forget one rough patch, which kept stepping a recovered link down.
    let rtt = 0, seen = false, frames = -1, intervalLoss = 0;
    const stats = await pc.getStats();
    stats.forEach((r: any) => {
      if (r.type === "inbound-rtp" && r.kind === "video") {
        const recv = r.packetsReceived || 0, lost = r.packetsLost || 0;
        if (lastReceived >= 0 && recv >= lastReceived && lost >= lastLost) {
          const newPackets = (recv - lastReceived) + (lost - lastLost);
          if (newPackets > 0) intervalLoss = (lost - lastLost) / newPackets;
        }
        lastReceived = recv;
        lastLost = lost;
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
    if (intervalLoss > SEVERE_INTERVAL_LOSS) { badStreak = 0; apply(TIER_ORDER[0]); return; }
    if (now() - lastChange < 10000) return;
    if (shouldDowngrade(intervalLoss, rtt)) {
      badStreak = nextBadStreak(badStreak, true);
      if (badStreak >= DOWNGRADE_STREAK) { badStreak = 0; apply(stepTier(current, -1)); }
    } else {
      badStreak = 0;
    }
  };

  return {
    start(peer: any) { pc = peer; badStreak = 0; stallStreak = 0; lastFrames = -1; lastReceived = -1; lastLost = -1; clearInterval(timer); timer = setInterval(tick, opts.sampleMs ?? 5000); },
    stop() { clearInterval(timer); timer = null; },
    pin(tier: string) { current = tier; manualUntil = now() + 60000; lastChange = now(); opts.onApply(tier); },
    setAuto() { manualUntil = 0; },
    current() { return current; },
    _tick: tick,
  };
}
