export type StreamTelemetry = {
  rttMs: number | null;
  loss: number | null;
  decodeMs: number | null;
  networkMs: number | null;
  inputMs: number | null;
  jitterMs: number | null;
  bitrateMbps: number | null;
  droppedFrames: number | null;
  transport: "direct" | "relay" | "unknown";
  route: "direct" | "relay" | "unknown";
  addressFamily: "IPv4" | "IPv6" | "mixed" | "unknown";
  relayProtocol: "udp" | "tcp" | "tls" | "unknown";
  sourceWidth: number | null;
  sourceHeight: number | null;
  decodedWidth: number | null;
  decodedHeight: number | null;
  decodedFps: number | null;
  framesDecoded: number | null;
  freezeCount: number | null;
  totalFreezeSeconds: number | null;
  maxFreezeSeconds: number | null;
};

type StatsPeer = { getStats: () => Promise<any> };

export type TelemetrySamplerOptions = {
  pc: StatsPeer;
  transport: "local" | "remote";
  onSample: (sample: StreamTelemetry) => void;
  sourceDimensions?: () => { width: number; height: number } | null;
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

function nonnegative(value: unknown): number | null {
  const n = numberOrNull(value);
  return n !== null && n >= 0 ? n : null;
}

function family(candidate: any): "IPv4" | "IPv6" | "unknown" {
  const address = candidate?.address ?? candidate?.ip;
  if (typeof address !== "string") return "unknown";
  if (/^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$/.test(address) && address.split(".").every(n => Number(n) <= 255)) return "IPv4";
  if (address.includes(":") && /^[0-9a-f:.]+$/i.test(address)) return "IPv6";
  return "unknown";
}

function selectedPair(stats: Map<string, any>): [string, any] | null {
  const transports = [...stats.values()].filter(s => s.type === "transport");
  const ids = [...new Set(transports.map(s => s.selectedCandidatePairId).filter((id): id is string => typeof id === "string"))];
  if (ids.length > 1) return null;
  if (ids.length === 1) {
    const pair = stats.get(ids[0]);
    if (pair?.type === "candidate-pair") return [ids[0], pair];
  }
  const pairs = [...stats.entries()].filter(([, s]) => s.type === "candidate-pair" && s.state === "succeeded");
  const selected = pairs.filter(([, s]) => s.selected === true);
  if (selected.length) return selected.length === 1 ? selected[0] : null;
  const nominated = pairs.filter(([, s]) => s.nominated === true);
  return nominated.length === 1 ? nominated[0] : null;
}

export function makeTelemetrySampler({ pc, onSample, sourceDimensions, sampleMs = 1_000 }: TelemetrySamplerOptions) {
  let inputMs: number | null = null;
  let lastBytes: number | null = null, lastFrames: number | null = null, lastTimestamp: number | null = null;
  let lastPair: string | null | undefined;
  let timer: ReturnType<typeof setInterval> | null = null;
  let active = true;
  let pending: Promise<StreamTelemetry> | null = null;

  const read = async (): Promise<StreamTelemetry> => {
    const stats = await pc.getStats();
    const video = [...stats.values()].filter((r: any) => r.type === "inbound-rtp" && (r.kind === "video" || r.mediaType === "video"));
    const inbound = video.length === 1 ? video[0] : null;
    const pair = selectedPair(stats);
    const local = pair ? stats.get(pair[1].localCandidateId) : undefined;
    const remote = pair ? stats.get(pair[1].remoteCandidateId) : undefined;
    const candidateTypes = ["host", "srflx", "prflx", "relay"];
    const route: StreamTelemetry["route"] = !local || !remote || !candidateTypes.includes(local.candidateType) || !candidateTypes.includes(remote.candidateType)
      ? "unknown" : local.candidateType === "relay" || remote.candidateType === "relay" ? "relay" : "direct";
    const localFamily = family(local), remoteFamily = family(remote);
    const addressFamily = localFamily === "unknown" || remoteFamily === "unknown" ? "unknown"
      : localFamily === remoteFamily ? localFamily : "mixed";
    const relays = [local, remote].filter(c => c?.candidateType === "relay");
    const protocols = [...new Set(relays.map(c => c.relayProtocol))];
    const relayProtocol = route === "relay" && protocols.length === 1 && ["udp", "tcp", "tls"].includes(protocols[0]) ? protocols[0] : "unknown";
    const rtt = numberOrNull(pair?.[1].currentRoundTripTime);
    const rttMs = rtt === null ? null : rtt * 1_000;
    const received = nonnegative(inbound?.packetsReceived), lost = nonnegative(inbound?.packetsLost);
    const decoded = nonnegative(inbound?.framesDecoded), decodeTime = nonnegative(inbound?.totalDecodeTime);
    const emitted = nonnegative(inbound?.jitterBufferEmittedCount), jitterDelay = nonnegative(inbound?.jitterBufferDelay);
    const bytes = nonnegative(inbound?.bytesReceived), timestamp = numberOrNull(inbound?.timestamp);
    const totalPackets = received !== null && lost !== null ? received + lost : null;
    const loss = totalPackets !== null && totalPackets > 0 && lost !== null ? lost / totalPackets : null;
    const decodeMs = decoded !== null && decoded > 0 && decodeTime !== null ? 1_000 * decodeTime / decoded : null;
    const jitterMs = emitted !== null && emitted > 0 && jitterDelay !== null ? 1_000 * jitterDelay / emitted : null;
    const pairId = pair?.[0] ?? null;
    const reset = lastPair !== undefined && lastPair !== pairId
      || bytes !== null && lastBytes !== null && bytes < lastBytes
      || decoded !== null && lastFrames !== null && decoded < lastFrames
      || timestamp !== null && lastTimestamp !== null && timestamp <= lastTimestamp;
    if (reset) { lastBytes = null; lastFrames = null; lastTimestamp = null; }
    const elapsed = timestamp !== null && lastTimestamp !== null ? timestamp - lastTimestamp : null;
    const bitrateMbps = elapsed !== null && elapsed > 0 && bytes !== null && lastBytes !== null ? 8 * (bytes - lastBytes) / elapsed / 1_000 : null;
    const decodedFps = elapsed !== null && elapsed > 0 && decoded !== null && lastFrames !== null ? 1_000 * (decoded - lastFrames) / elapsed : null;
    lastBytes = bytes; lastFrames = decoded; lastTimestamp = timestamp; lastPair = pairId;
    const source = sourceDimensions?.();
    const telemetry: StreamTelemetry = {
      rttMs, loss, decodeMs, networkMs: rttMs !== null && jitterMs !== null ? Math.max(0, rttMs - jitterMs) : rttMs,
      inputMs, jitterMs, bitrateMbps, droppedFrames: nonnegative(inbound?.framesDropped),
      transport: route, route, addressFamily, relayProtocol,
      sourceWidth: nonnegative(source?.width), sourceHeight: nonnegative(source?.height),
      decodedWidth: nonnegative(inbound?.frameWidth), decodedHeight: nonnegative(inbound?.frameHeight),
      decodedFps, framesDecoded: decoded, freezeCount: nonnegative(inbound?.freezeCount),
      totalFreezeSeconds: nonnegative(inbound?.totalFreezesDuration),
      // A cumulative count/duration or FPS bucket cannot establish the longest freeze.
      maxFreezeSeconds: nonnegative(inbound?.maxFreezeDuration),
    };
    if (active) onSample(telemetry);
    return telemetry;
  };
  const sample = () => pending ??= read().finally(() => { pending = null; });
  return {
    setInputRtt(ms: number | null) { inputMs = numberOrNull(ms); },
    sample,
    start() {
      if (timer !== null) return;
      active = true;
      void sample().catch(() => {});
      timer = setInterval(() => { void sample().catch(() => {}); }, sampleMs);
    },
    stop() { active = false; if (timer !== null) clearInterval(timer); timer = null; },
  };
}
