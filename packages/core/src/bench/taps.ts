import { classify, opposite } from "./detect";
import type { TargetColor } from "./detect";

export type FrameSample = {
  lum: number;
  rtpTimestamp: number | null;
  receiveTime: number | null;
  expectedDisplayTime: number;
};

export type TapRecord = {
  seq: number;
  t0: number;
  t1: number | null;
  rtpTimestamp: number | null;
  receiveTime: number | null;
  miss: boolean;
};

export type TapDeps = {
  sendClick(seq: number): void;
  // Resolves with the next presented frame, or null after timeoutMs.
  nextFrame(timeoutMs: number): Promise<FrameSample | null>;
  now(): number;
  sleep(ms: number): Promise<void>;
  random(): number;
};

export type TapOptions = {
  count: number;
  warmup: number;
  missTimeoutMs: number;
  gapMinMs: number;
  gapMaxMs: number;
  stableMs: number;
};

export const DEFAULT_TAP_OPTIONS: TapOptions = {
  count: 200, warmup: 10, missTimeoutMs: 2000, gapMinMs: 300, gapMaxMs: 700, stableMs: 1000,
};

export class TargetNotInFrontError extends Error {
  constructor() {
    super("target page not in front");
  }
}

async function readStableColor(deps: TapDeps, stableMs: number): Promise<TargetColor> {
  const start = deps.now();
  let color: TargetColor | null = null;
  while (deps.now() - start < stableMs) {
    const frame = await deps.nextFrame(stableMs);
    const current = frame ? classify(frame.lum) : null;
    if (current === null || (color !== null && current !== color)) throw new TargetNotInFrontError();
    color = current;
  }
  if (color === null) throw new TargetNotInFrontError();
  return color;
}

export async function runTaps(deps: TapDeps, options: TapOptions = DEFAULT_TAP_OPTIONS): Promise<TapRecord[]> {
  let current = await readStableColor(deps, options.stableMs);
  const records: TapRecord[] = [];

  for (let seq = 0; seq < options.warmup + options.count; seq++) {
    const expected = opposite(current);
    const t0 = deps.now();
    deps.sendClick(seq);

    let record: TapRecord = { seq, t0, t1: null, rtpTimestamp: null, receiveTime: null, miss: true };
    for (;;) {
      const remaining = options.missTimeoutMs - (deps.now() - t0);
      const frame = remaining > 0 ? await deps.nextFrame(remaining) : null;
      if (!frame) break;
      if (frame.expectedDisplayTime >= t0 && classify(frame.lum) === expected) {
        record = {
          seq, t0, t1: frame.expectedDisplayTime,
          rtpTimestamp: frame.rtpTimestamp, receiveTime: frame.receiveTime, miss: false,
        };
        break;
      }
    }

    if (record.miss) {
      // The click may still land late; re-read the screen before the next tap.
      const frame = await deps.nextFrame(options.missTimeoutMs);
      current = (frame && classify(frame.lum)) || current;
    } else {
      current = expected;
    }
    if (seq >= options.warmup) records.push(record);
    await deps.sleep(options.gapMinMs + deps.random() * (options.gapMaxMs - options.gapMinMs));
  }
  return records;
}
