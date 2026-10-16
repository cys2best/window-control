import { centerLuminance, classify } from "./detect";
import { runTaps, TargetNotInFrontError } from "./taps";
import type { FrameSample, TapDeps } from "./taps";

const FRAME_MS = 16;
const OPTIONS = { count: 5, warmup: 2, missTimeoutMs: 2000, gapMinMs: 300, gapMaxMs: 700, stableMs: 1000 };

// A screen that flips `latencyMs` after each delivered click, sampled every 16 ms.
function fakeTarget({ latencyMs, dropSeq = -1, lum }: { latencyMs: number; dropSeq?: number; lum?: number }) {
  let now = 0;
  const flips: number[] = [];
  let last: FrameSample | null = null;
  const colorAt = (t: number) => (flips.filter((f) => f + latencyMs <= t).length % 2 === 0 ? 0 : 255);
  const deps: TapDeps = {
    now: () => now,
    random: () => 0.5,
    sleep: async (ms) => { now += ms; },
    sendClick: (seq) => { if (seq !== dropSeq) flips.push(now); },
    lastFrame: () => last,
    nextFrame: async (timeoutMs): Promise<FrameSample | null> => {
      if (FRAME_MS > timeoutMs) { now += timeoutMs; return null; }
      now += FRAME_MS;
      last = { lum: lum ?? colorAt(now), rtpTimestamp: now * 90, receiveTime: now - 5, expectedDisplayTime: now };
      return last;
    },
  };
  return deps;
}

test("measures click-to-flip latency and drops warm-up taps", async () => {
  const records = await runTaps(fakeTarget({ latencyMs: 80 }), OPTIONS);
  expect(records.map((r) => r.seq)).toEqual([2, 3, 4, 5, 6]);
  for (const r of records) {
    expect(r.miss).toBe(false);
    expect(r.t1! - r.t0).toBeGreaterThanOrEqual(80);
    expect(r.t1! - r.t0).toBeLessThan(80 + FRAME_MS);
    expect(r.rtpTimestamp).toBe(r.t1! * 90);
  }
});

test("a lost click is a miss and later taps still measure", async () => {
  const records = await runTaps(fakeTarget({ latencyMs: 50, dropSeq: 3 }), OPTIONS);
  expect(records.find((r) => r.seq === 3)!.miss).toBe(true);
  expect(records.filter((r) => r.seq !== 3).every((r) => !r.miss)).toBe(true);
});

test("refuses to run when the target is not in front", async () => {
  await expect(runTaps(fakeTarget({ latencyMs: 50, lum: 128 }), OPTIONS)).rejects.toBeInstanceOf(TargetNotInFrontError);
});

test("centre luminance ignores the border and classify applies thresholds", () => {
  const width = 8, height = 8;
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const inside = x >= 2 && x < 6 && y >= 2 && y < 6;
    rgba.fill(inside ? 255 : 0, (y * width + x) * 4, (y * width + x) * 4 + 3);
  }
  expect(centerLuminance(rgba, width, height)).toBeCloseTo(255, 0);
  expect(classify(200)).toBe("white");
  expect(classify(40)).toBe("black");
  expect(classify(128)).toBeNull();
});

// Like the real stream: the encoder emits a frame only when the screen changes.
function staticTarget(latencyMs: number) {
  let now = 0;
  let last: FrameSample = { lum: 0, rtpTimestamp: 0, receiveTime: 0, expectedDisplayTime: 0 };
  const changes: number[] = [];
  const deps: TapDeps = {
    now: () => now,
    random: () => 0.5,
    sleep: async (ms) => { now += ms; },
    sendClick: () => { changes.push(now + latencyMs); },
    lastFrame: () => last,
    nextFrame: async (timeoutMs) => {
      const next = changes.find((t) => t > now);
      if (next === undefined || next - now > timeoutMs) { now += timeoutMs; return null; }
      now = next;
      last = { lum: last.lum === 0 ? 255 : 0, rtpTimestamp: now * 90, receiveTime: now - 5, expectedDisplayTime: now };
      return last;
    },
  };
  return deps;
}

test("a still screen that sends no frames until tapped is still a stable target", async () => {
  const records = await runTaps(staticTarget(60), OPTIONS);
  expect(records).toHaveLength(5);
  for (const r of records) {
    expect(r.miss).toBe(false);
    expect(r.t1! - r.t0).toBe(60);
  }
});
