import { engineToLocalMs } from "./clock";
import type { EngineEntry } from "./benchClient";
import type { TapRecord } from "./taps";

export const STAGES = [
  "input_network", "engine_input", "guest", "engine_video", "network_receive", "receiver", "tap_to_photon",
] as const;
export type StageName = (typeof STAGES)[number];

export type StageStats = { count: number; mean: number; p50: number; p95: number; p99: number; p50CiHalf: number };
export type FrameMeta = { rtpTimestamp: number; receiveTime: number; expectedDisplayTime: number };
export type AnalyzeInput = {
  taps: TapRecord[];
  frames: FrameMeta[];
  engine: EngineEntry[];
  engineWrapped: boolean;
  offsetUs: number;
  driftUs: number;
  transportChanged: boolean;
};
export type Validity = { failed: boolean; reasons: string[]; crossClockReliable: boolean; breakdownAvailable: boolean };
export type Analysis = { stats: Partial<Record<StageName, StageStats>>; validity: Validity; missRate: number };

export const MAX_DRIFT_US = 2000;
export const MAX_MISS_RATE = 0.05;
const MIN_JOIN_RATE = 0.5;

export function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return NaN;
  const rank = (sorted.length - 1) * p;
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - lo);
}

export function bootstrapP50CiHalf(values: number[], random: () => number, iterations = 1000): number {
  if (values.length < 2) return 0;
  const medians: number[] = [];
  for (let i = 0; i < iterations; i++) {
    const resample = values.map(() => values[Math.floor(random() * values.length)]).sort((a, b) => a - b);
    medians.push(percentile(resample, 0.5));
  }
  medians.sort((a, b) => a - b);
  return (percentile(medians, 0.975) - percentile(medians, 0.025)) / 2;
}

function summarize(values: number[], random: () => number): StageStats {
  const sorted = [...values].sort((a, b) => a - b);
  return {
    count: sorted.length,
    mean: sorted.reduce((sum, v) => sum + v, 0) / sorted.length,
    p50: percentile(sorted, 0.5),
    p95: percentile(sorted, 0.95),
    p99: percentile(sorted, 0.99),
    p50CiHalf: bootstrapP50CiHalf(sorted, random),
  };
}

export function analyze(input: AnalyzeInput, random: () => number = Math.random): Analysis {
  const reasons: string[] = [];
  let failed = false;

  const measured = input.taps.filter((t) => !t.miss && t.t1 !== null);
  const missRate = input.taps.length === 0 ? 1 : (input.taps.length - measured.length) / input.taps.length;
  if (missRate > MAX_MISS_RATE) {
    failed = true;
    reasons.push(`miss rate ${(missRate * 100).toFixed(1)}% exceeds 5%`);
  }
  if (input.transportChanged) {
    failed = true;
    reasons.push("transport changed during the run");
  }
  const crossClockReliable = input.driftUs <= MAX_DRIFT_US;
  if (!crossClockReliable) {
    reasons.push(`clock drift ${(input.driftUs / 1000).toFixed(2)} ms exceeds 2 ms; cross-clock stages unreliable`);
  }

  const inputs = new Map<number, EngineEntry>();
  const frames = new Map<number, EngineEntry>();
  for (const entry of input.engine) (entry.kind === "input" ? inputs : frames).set(entry.key, entry);
  const local = (us: number) => engineToLocalMs(us, input.offsetUs);

  const values = Object.fromEntries(STAGES.map((s) => [s, [] as number[]])) as Record<StageName, number[]>;
  let joined = 0;
  for (const tap of measured) {
    values.tap_to_photon.push(tap.t1! - tap.t0);
    const inp = inputs.get(tap.seq);
    const frame = tap.rtpTimestamp === null ? undefined : frames.get(tap.rtpTimestamp);
    if (!inp || !frame || tap.receiveTime === null) continue;
    joined++;
    values.input_network.push(local(inp.t1Us) - tap.t0);
    values.engine_input.push((inp.t2Us - inp.t1Us) / 1000);
    values.guest.push((frame.t1Us - inp.t2Us) / 1000);
    values.receiver.push(tap.t1! - tap.receiveTime);
  }
  // Every received frame, not only tap frames, for the two per-frame stages.
  for (const meta of input.frames) {
    const frame = frames.get(meta.rtpTimestamp);
    if (!frame) continue;
    values.engine_video.push((frame.t3Us - frame.t1Us) / 1000);
    values.network_receive.push(meta.receiveTime - local(frame.t3Us));
  }

  let breakdownAvailable = true;
  if (input.engineWrapped || input.engine.length === 0) {
    breakdownAvailable = false;
    reasons.push("engine log missing or wrapped; breakdown omitted");
  } else if (measured.length > 0 && joined / measured.length < MIN_JOIN_RATE) {
    breakdownAvailable = false;
    reasons.push("engine entries did not match the measured taps (stream restarted?); breakdown omitted");
  }

  const stats: Partial<Record<StageName, StageStats>> = {};
  if (values.tap_to_photon.length > 0) stats.tap_to_photon = summarize(values.tap_to_photon, random);
  if (breakdownAvailable) {
    for (const stage of STAGES) {
      if (stage !== "tap_to_photon" && values[stage].length > 0) stats[stage] = summarize(values[stage], random);
    }
  }
  return { stats, validity: { failed, reasons, crossClockReliable, breakdownAvailable }, missRate };
}
