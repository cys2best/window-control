import { makeAdaptive } from "./adaptive";

function statsMap(loss: number, rttMs: number) {
  const m = new Map<string, any>();
  m.set("r1", { type: "inbound-rtp", kind: "video", packetsReceived: 1000, packetsLost: Math.round(loss * 1000 / (1 - loss)) });
  m.set("p1", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: rttMs / 1000 });
  return m;
}

test("downgrades after sustained congestion, once cooldown allows", async () => {
  let t = 100000;
  const applied: string[] = [];
  const pc = { getStats: async () => statsMap(0.2, 500) };
  const a = makeAdaptive({ serial: "A", onApply: (tier) => applied.push(tier), sampleMs: 1, now: () => t });
  a.start(pc as any);
  // drive 3 congested samples past the 10s change-cooldown
  for (let i = 0; i < 3; i++) { t += 6000; await (a as any)._tick(); }
  a.stop();
  expect(applied[0]).toBe("480"); // 720 -> 480
});

function videoStats(received: number, lost: number, framesDecoded: number, rttMs = 50) {
  const m = new Map<string, any>();
  m.set("r1", { type: "inbound-rtp", kind: "video", packetsReceived: received, packetsLost: lost, framesDecoded });
  m.set("p1", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: rttMs / 1000 });
  return m;
}

function adaptiveOver(reports: Map<string, any>[], options: Record<string, unknown> = {}) {
  let t = 100000;
  const applied: string[] = [];
  const a = makeAdaptive({
    serial: "A", onApply: (tier: string) => applied.push(tier), sampleMs: 1, now: () => t, ...options,
  });
  a.start({ getStats: async () => reports.shift() } as any);
  const tick = async () => { t += 5000; await (a as any)._tick(); };
  return { a, applied, tick, advance: (ms: number) => { t += ms; } };
}

test("drops straight to the lowest tier when a fifth of one interval's packets are lost", async () => {
  // A relayed link at 720p: stepping down one tier per 15s leaves the user
  // staring at a frozen picture for most of a minute.
  const { a, applied, tick } = adaptiveOver([
    videoStats(1000, 0, 100), videoStats(1300, 300, 130),
  ]);

  await tick();
  await tick();
  a.stop();

  expect(applied).toEqual(["360"]);
});

test("old losses do not count against a link that has recovered", async () => {
  // Cumulative counters stay bad forever after one rough patch; only the
  // loss inside the interval matters.
  const { a, applied, tick } = adaptiveOver([
    videoStats(1000, 600, 100), videoStats(2000, 600, 250), videoStats(3000, 601, 400),
  ]);

  await tick(); await tick(); await tick();
  a.stop();

  expect(applied).toEqual([]);
});

test("starts from the tier the server reports instead of assuming 720", async () => {
  const sustained = [0, 1, 2, 3].map((i) => videoStats(1000 + 1000 * i, 100 * i, 100 + 100 * i, 600));
  const { a, applied, tick } = adaptiveOver(sustained, { initialTier: "360" });

  for (let i = 0; i < 4; i += 1) await tick();
  a.stop();

  // Already on the lowest tier: nothing to step down to, and never up.
  expect(applied).toEqual([]);
  expect(a.current()).toBe("360");
});

test("a tier the user just picked is left alone for the manual hold", async () => {
  const { a, applied, tick, advance } = adaptiveOver([
    videoStats(1000, 0, 100), videoStats(1300, 300, 130), videoStats(1600, 600, 160), videoStats(1900, 900, 190),
  ]);
  a.pin("720");
  expect(applied).toEqual(["720"]);

  await tick(); await tick();
  expect(applied).toEqual(["720"]);

  advance(60000);
  await tick(); await tick();
  a.stop();
  expect(applied).toEqual(["720", "360"]);
});
