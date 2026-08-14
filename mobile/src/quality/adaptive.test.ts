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
