export type PingSample = { sendMs: number; recvMs: number; engineUs: number };
export type ClockEstimate = { offsetUs: number; rttMs: number; samples: number };

// The lowest-RTT ping has the least room for asymmetric delay, so its
// midpoint is the best estimate of when the engine read its clock.
export function estimateOffset(samples: PingSample[]): ClockEstimate {
  if (samples.length === 0) throw new Error("no clock samples");
  let best = samples[0];
  for (const sample of samples) {
    if (sample.recvMs - sample.sendMs < best.recvMs - best.sendMs) best = sample;
  }
  const midpointUs = ((best.sendMs + best.recvMs) / 2) * 1000;
  return { offsetUs: best.engineUs - midpointUs, rttMs: best.recvMs - best.sendMs, samples: samples.length };
}

export function engineToLocalMs(engineUs: number, offsetUs: number): number {
  return (engineUs - offsetUs) / 1000;
}

export function clockDriftUs(before: ClockEstimate, after: ClockEstimate): number {
  return Math.abs(after.offsetUs - before.offsetUs);
}
