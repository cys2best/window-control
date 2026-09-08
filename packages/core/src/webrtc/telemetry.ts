export type StreamTelemetry = {
  rttMs: number | null;
  loss: number | null;
  decodeMs: number | null;
  networkMs: number | null;
  inputMs: number | null;
  jitterMs: number | null;
  bitrateMbps: number | null;
  droppedFrames: number | null;
  transport: "LAN" | "RELAY";
};

type StatsPeer = { getStats: () => Promise<any> };

type TelemetrySamplerOptions = {
  pc: StatsPeer;
  transport: "local" | "public";
  onSample: (sample: StreamTelemetry) => void;
  sampleMs?: number;
  now?: () => number;
};

type SignalLevel = { bars: 1 | 2 | 3 | 4; tone: "mint" | "amber" | "tangerine" };

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function signalLevel(
  telemetry: Pick<StreamTelemetry, "rttMs" | "loss">,
  connected: boolean
): SignalLevel {
  if (!connected || telemetry.rttMs === null || telemetry.loss === null) {
    return { bars: 1, tone: "tangerine" };
  }
  if (telemetry.rttMs < 25 && telemetry.loss < 0.01) {
    return { bars: 4, tone: "mint" };
  }
  if (telemetry.rttMs <= 60 && telemetry.loss <= 0.05) {
    return { bars: 3, tone: "amber" };
  }
  return { bars: 2, tone: "tangerine" };
}

export function makeTelemetrySampler({ pc, transport, onSample, sampleMs = 1_000, now = Date.now }: TelemetrySamplerOptions) {
  let inputMs: number | null = null;
  let lastBytes: number | null = null;
  let lastTimestamp: number | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;

  const sample = async (): Promise<StreamTelemetry> => {
    const stats = await pc.getStats();
    let inbound: any = null;
    let rttMs: number | null = null;

    stats.forEach((report: any) => {
      if (report.type === "inbound-rtp" && (report.kind === "video" || report.mediaType === "video")) {
        inbound = report;
      }
      if (report.type === "candidate-pair" && report.state === "succeeded") {
        const currentRtt = numberOrNull(report.currentRoundTripTime);
        if (currentRtt !== null) rttMs = currentRtt * 1_000;
      }
    });

    const received = numberOrNull(inbound?.packetsReceived);
    const lost = numberOrNull(inbound?.packetsLost);
    const decoded = numberOrNull(inbound?.framesDecoded);
    const decodeTime = numberOrNull(inbound?.totalDecodeTime);
    const emitted = numberOrNull(inbound?.jitterBufferEmittedCount);
    const jitterDelay = numberOrNull(inbound?.jitterBufferDelay);
    const bytes = numberOrNull(inbound?.bytesReceived);
    const timestamp = numberOrNull(inbound?.timestamp);
    const totalPackets = received !== null && lost !== null ? received + lost : null;
    const loss = totalPackets !== null && totalPackets > 0 && lost !== null ? lost / totalPackets : null;
    const decodeMs = decoded !== null && decoded > 0 && decodeTime !== null ? 1_000 * decodeTime / decoded : null;
    const jitterMs = emitted !== null && emitted > 0 && jitterDelay !== null ? 1_000 * jitterDelay / emitted : null;
    const bitrateMbps = bytes !== null && timestamp !== null && lastBytes !== null && lastTimestamp !== null && timestamp > lastTimestamp
      ? 8 * (bytes - lastBytes) / (timestamp - lastTimestamp) / 1_000
      : null;

    if (bytes !== null && timestamp !== null) {
      lastBytes = bytes;
      lastTimestamp = timestamp;
    }

    // Jitter is time held in the receiver buffer; when available, remove it
    // from the candidate-pair RTT to keep the NETWORK row transport-focused.
    const networkMs = rttMs !== null && jitterMs !== null ? Math.max(0, rttMs - jitterMs) : rttMs;
    const telemetry: StreamTelemetry = {
      rttMs,
      loss,
      decodeMs,
      networkMs,
      inputMs,
      jitterMs,
      bitrateMbps,
      droppedFrames: numberOrNull(inbound?.framesDropped),
      transport: transport === "local" ? "LAN" : "RELAY",
    };
    onSample(telemetry);
    return telemetry;
  };

  return {
    setInputRtt(ms: number | null) {
      inputMs = numberOrNull(ms);
    },
    sample,
    start() {
      if (timer !== null) return;
      void sample().catch(() => {});
      timer = setInterval(() => { void sample().catch(() => {}); }, sampleMs);
    },
    stop() {
      if (timer !== null) clearInterval(timer);
      timer = null;
    },
  };
}
