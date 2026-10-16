type StatsPeer = { getStats: () => Promise<any> };

type StallWatchdogOptions = {
  pc: StatsPeer;
  onStall: () => void;
  sampleMs?: number;
  minGapMs?: number;
  now?: () => number;
};

type Counters = { frames: number; packets: number };

// The engine does not answer RTCP PLI and keyframes are sparse, so one lost
// packet leaves the decoder frozen until the next keyframe, often many
// seconds away. This watches the receiver's own counters and reports a stall
// the moment packets are still arriving but nothing decodes, so the caller
// can ask the host for a keyframe.
export function makeStallWatchdog({ pc, onStall, sampleMs = 1_000, minGapMs = 2_000, now = Date.now }: StallWatchdogOptions) {
  let previous: Counters | null = null;
  let lastStall: number | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;

  const read = async (): Promise<Counters | null> => {
    let counters: Counters | null = null;
    const stats = await pc.getStats();
    stats.forEach((report: any) => {
      if (report.type !== "inbound-rtp" || (report.kind !== "video" && report.mediaType !== "video")) return;
      if (typeof report.framesDecoded !== "number" || typeof report.packetsReceived !== "number") return;
      counters = { frames: report.framesDecoded, packets: report.packetsReceived };
    });
    return counters;
  };

  const sample = async () => {
    let current: Counters | null = null;
    try {
      current = await read();
    } catch {
      // A closing peer rejects getStats; the next sample starts over.
    }
    const before = previous;
    previous = current;
    if (!current || !before) return;
    // A still device screen sends no packets at all; only "packets but no
    // frames" means the decoder is waiting for a keyframe.
    const stalled = current.frames <= before.frames && current.packets > before.packets;
    if (!stalled) return;
    const time = now();
    if (lastStall !== null && time - lastStall < minGapMs) return;
    lastStall = time;
    onStall();
  };

  return {
    start() {
      if (timer) clearInterval(timer);
      timer = setInterval(() => { void sample(); }, sampleMs);
    },
    stop() {
      if (timer) clearInterval(timer);
      timer = null;
    },
    sample,
  };
}
