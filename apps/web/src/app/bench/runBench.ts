import {
  analyze, centerLuminance, clockDriftUs, connectWhep, DEFAULT_TAP_OPTIONS,
  makeBenchClient, runTaps, selectedCandidatePair,
} from "@wc/core";
import type {
  Analysis, BenchChannel, EngineEntry, FrameMeta, FrameSample, SelectResp, TapRecord,
} from "@wc/core";

export type BenchResult = {
  meta: {
    serial: string; tier: string | null; transport: string | null; offsetUs: number; driftUs: number;
    syncRttMs: number; count: number; warmup: number; userAgent: string; startedAt: string;
  };
  taps: TapRecord[];
  frames: FrameMeta[];
  engine: EngineEntry[];
  stats: Analysis["stats"];
  validity: Analysis["validity"];
  missRate: number;
};

type RunBenchOpts = {
  select: (serial: string) => Promise<SelectResp>;
  serial: string;
  count: number;
  warmup: number;
  video: HTMLVideoElement;
  onStatus: (status: string) => void;
};

const SAMPLE_SIZE = 8;

export async function runBench({ select, serial, count, warmup, video, onStatus }: RunBenchOpts): Promise<BenchResult> {
  const startedAt = new Date().toISOString();
  onStatus("selecting instance");
  const selection = await select(serial);

  const subscribers = new Set<(message: any) => void>();
  onStatus("connecting");
  const session = await connectWhep({
    whepUrl: selection.whep_url,
    whepToken: selection.whep_token || "",
    iceServers: selection.ice_servers || [],
    RTCImpl: window.RTCPeerConnection,
    onStream: (stream) => { video.srcObject = stream; },
    onInputRtt: () => {},
    onState: () => {},
    onMessage: (message) => subscribers.forEach((handler) => handler(message)),
  });
  await video.play().catch(() => {});

  const channel: BenchChannel = {
    send: (message) => session.input.send(message),
    subscribe: (handler) => { subscribers.add(handler); return () => { subscribers.delete(handler); }; },
  };

  // Frame pump: every presented frame is sampled once and handed to all waiters.
  const canvas = document.createElement("canvas");
  canvas.width = SAMPLE_SIZE;
  canvas.height = SAMPLE_SIZE;
  const context = canvas.getContext("2d", { willReadFrequently: true })!;
  const frames: FrameMeta[] = [];
  let waiters: Array<(sample: FrameSample) => void> = [];
  let stopped = false;
  const onFrame = (_now: number, meta: any) => {
    context.drawImage(video, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
    const lum = centerLuminance(context.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE).data, SAMPLE_SIZE, SAMPLE_SIZE);
    const sample: FrameSample = {
      lum,
      rtpTimestamp: typeof meta.rtpTimestamp === "number" ? meta.rtpTimestamp : null,
      receiveTime: typeof meta.receiveTime === "number" ? meta.receiveTime : null,
      expectedDisplayTime: meta.expectedDisplayTime,
    };
    if (sample.rtpTimestamp !== null && sample.receiveTime !== null) {
      frames.push({ rtpTimestamp: sample.rtpTimestamp, receiveTime: sample.receiveTime, expectedDisplayTime: sample.expectedDisplayTime });
    }
    const ready = waiters;
    waiters = [];
    ready.forEach((resolve) => resolve(sample));
    if (!stopped) (video as any).requestVideoFrameCallback(onFrame);
  };
  (video as any).requestVideoFrameCallback(onFrame);

  const nextFrame = (timeoutMs: number) =>
    new Promise<FrameSample | null>((resolve) => {
      const done = (sample: FrameSample) => { clearTimeout(timer); resolve(sample); };
      const timer = setTimeout(() => { waiters = waiters.filter((w) => w !== done); resolve(null); }, timeoutMs);
      waiters.push(done);
    });

  try {
    const bench = makeBenchClient(channel, { now: () => performance.now() });
    onStatus("syncing clocks");
    const before = await bench.syncClock();
    await bench.start();
    const pairBefore = selectedCandidatePair(await session.pc.getStats());

    onStatus(`tapping (${warmup} warm-up + ${count})`);
    const taps = await runTaps({
      sendClick: (seq) => session.input.send({ type: "click", x: 0.5, y: 0.5, bench_seq: seq }),
      nextFrame,
      now: () => performance.now(),
      sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      random: Math.random,
    }, { ...DEFAULT_TAP_OPTIONS, count, warmup });

    const pairAfter = selectedCandidatePair(await session.pc.getStats());
    onStatus("syncing clocks");
    const after = await bench.syncClock();
    onStatus("fetching engine log");
    const engineLog = await bench.fetchAll();
    stopped = true;

    const driftUs = clockDriftUs(before, after);
    const analysis = analyze({
      taps, frames, engine: engineLog.entries, engineWrapped: engineLog.wrapped,
      offsetUs: before.offsetUs, driftUs, transportChanged: pairBefore.id !== pairAfter.id,
    });
    return {
      meta: {
        serial, tier: selection.tier ?? null, transport: pairAfter.remoteType, offsetUs: before.offsetUs,
        driftUs, syncRttMs: before.rttMs, count, warmup, userAgent: navigator.userAgent, startedAt,
      },
      taps, frames, engine: engineLog.entries,
      stats: analysis.stats, validity: analysis.validity, missRate: analysis.missRate,
    };
  } finally {
    stopped = true;
    await session.close();
  }
}
