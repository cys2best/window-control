import { analyze, bootstrapP50CiHalf, percentile } from "./analyze";
import type { AnalyzeInput } from "./analyze";
import type { EngineEntry } from "./benchClient";
import type { TapRecord } from "./taps";

const OFFSET_US = 1_000_000_000; // engine clock = local ms * 1000 + OFFSET_US
const e = (localMs: number) => localMs * 1000 + OFFSET_US;
const fixedRandom = () => 0.5;

// One tap: t0=100, engine recv 104, inject 105, frame read 150, packetized 151,
// sent 153, receiveTime 160, displayed 175.
function tap(seq: number, shift = 0): { tap: TapRecord; engine: EngineEntry[] } {
  const s = shift;
  const rtp = 9000 + seq;
  return {
    tap: { seq, t0: 100 + s, t1: 175 + s, rtpTimestamp: rtp, receiveTime: 160 + s, miss: false },
    engine: [
      { kind: "input", key: seq, t1Us: e(104 + s), t2Us: e(105 + s), t3Us: 0 },
      { kind: "frame", key: rtp, t1Us: e(150 + s), t2Us: e(151 + s), t3Us: e(153 + s) },
    ],
  };
}

function input(count: number, overrides: Partial<AnalyzeInput> = {}): AnalyzeInput {
  const taps = Array.from({ length: count }, (_, i) => tap(i, i * 1000));
  return {
    taps: taps.map((t) => t.tap),
    frames: taps.map((t) => ({ rtpTimestamp: t.tap.rtpTimestamp!, receiveTime: t.tap.receiveTime!, expectedDisplayTime: t.tap.t1! })),
    engine: taps.flatMap((t) => t.engine),
    engineWrapped: false,
    offsetUs: OFFSET_US,
    driftUs: 100,
    transportChanged: false,
    ...overrides,
  };
}

test("percentile interpolates between ranks", () => {
  expect(percentile([1, 2, 3, 4], 0.5)).toBe(2.5);
  expect(percentile([1, 2, 3, 4], 1)).toBe(4);
});

test("bootstrap interval of identical values is zero", () => {
  expect(bootstrapP50CiHalf([5, 5, 5, 5], Math.random, 200)).toBe(0);
});

test("computes every stage from joined tap, engine and frame data", () => {
  const { stats, validity, missRate } = analyze(input(20), fixedRandom);
  expect(validity).toEqual({ failed: false, reasons: [], crossClockReliable: true, breakdownAvailable: true });
  expect(missRate).toBe(0);
  expect(stats.tap_to_photon!.p50).toBeCloseTo(75);
  expect(stats.input_network!.p50).toBeCloseTo(4);
  expect(stats.engine_input!.p50).toBeCloseTo(1);
  expect(stats.guest!.p50).toBeCloseTo(45);
  expect(stats.engine_video!.p50).toBeCloseTo(3);
  expect(stats.network_receive!.p50).toBeCloseTo(7);
  expect(stats.receiver!.p50).toBeCloseTo(15);
  expect(stats.tap_to_photon!.count).toBe(20);
});

test("more than 5% misses fails the run", () => {
  const data = input(20);
  data.taps[0] = { ...data.taps[0], miss: true, t1: null };
  data.taps[1] = { ...data.taps[1], miss: true, t1: null };
  const { validity, missRate } = analyze(data, fixedRandom);
  expect(missRate).toBeCloseTo(0.1);
  expect(validity.failed).toBe(true);
});

test("drift over 2 ms marks cross-clock stages unreliable but keeps the headline", () => {
  const { stats, validity } = analyze(input(20, { driftUs: 2_500 }), fixedRandom);
  expect(validity.crossClockReliable).toBe(false);
  expect(validity.failed).toBe(false);
  expect(stats.tap_to_photon).toBeDefined();
});

test("a wrapped engine log omits the breakdown", () => {
  const { stats, validity } = analyze(input(20, { engineWrapped: true }), fixedRandom);
  expect(validity.breakdownAvailable).toBe(false);
  expect(Object.keys(stats)).toEqual(["tap_to_photon"]);
});

test("a mid-run reconnect that breaks the RTP join omits the breakdown", () => {
  const data = input(20);
  data.engine = data.engine.map((entry) => (entry.kind === "frame" ? { ...entry, key: entry.key + 500_000 } : entry));
  const { stats, validity } = analyze(data, fixedRandom);
  expect(validity.breakdownAvailable).toBe(false);
  expect(validity.reasons.join(" ")).toContain("did not match");
  expect(Object.keys(stats)).toEqual(["tap_to_photon"]);
});

test("frames from a second viewer do not disturb the join", () => {
  const data = input(20);
  data.engine.push(...Array.from({ length: 50 }, (_, i) => ({ kind: "frame" as const, key: 3_000_000 + i, t1Us: 1, t2Us: 2, t3Us: 3 })));
  const { stats, validity } = analyze(data, fixedRandom);
  expect(validity.breakdownAvailable).toBe(true);
  expect(stats.guest!.count).toBe(20);
});

test("a transport change fails the run", () => {
  expect(analyze(input(20, { transportChanged: true }), fixedRandom).validity.failed).toBe(true);
});
