import { makeTelemetrySampler, signalLevel, StreamTelemetry } from "./telemetry";

function stats({ bytesReceived, timestamp }: { bytesReceived: number; timestamp: number }) {
  return new Map([
    ["inbound-video", {
      type: "inbound-rtp", kind: "video", bytesReceived, timestamp,
      packetsReceived: 980, packetsLost: 20, framesDecoded: 100,
      totalDecodeTime: 0.4, jitterBufferDelay: 0.6,
      jitterBufferEmittedCount: 100, framesDropped: 3,
    }],
    ["candidate-pair", {
      type: "candidate-pair", state: "succeeded", currentRoundTripTime: 0.018,
    }],
  ]);
}

test("derives decode, network, loss, jitter, bitrate and dropped frames from RTC stats", async () => {
  let now = 1_000;
  const reports = [
    stats({ bytesReceived: 1_000_000, timestamp: 1_000 }),
    stats({ bytesReceived: 2_000_000, timestamp: 2_000 }),
  ];
  const samples: StreamTelemetry[] = [];
  const sampler = makeTelemetrySampler({
    pc: { getStats: jest.fn().mockImplementation(async () => reports.shift()) },
    transport: "local",
    onSample: (sample) => samples.push(sample),
    now: () => now,
  });
  await sampler.sample();
  now = 2_000;
  await sampler.sample();
  expect(samples[samples.length - 1]).toMatchObject({
    rttMs: 18,
    loss: 0.02,
    decodeMs: 4,
    networkMs: 12,
    jitterMs: 6,
    bitrateMbps: 8,
    droppedFrames: 3,
    transport: "LAN",
  });
});

test("preserves unavailable metrics as null and accepts input echo RTT", async () => {
  const samples: StreamTelemetry[] = [];
  const sampler = makeTelemetrySampler({
    pc: { getStats: async () => new Map() },
    transport: "public",
    onSample: (sample) => samples.push(sample),
  });

  sampler.setInputRtt(11);
  await sampler.sample();

  expect(samples[0]).toEqual({
    rttMs: null, loss: null, decodeMs: null, networkMs: null, inputMs: 11,
    jitterMs: null, bitrateMbps: null, droppedFrames: null, transport: "RELAY",
  });
});

test.each([
  [{ rttMs: 20, loss: 0 }, true, { bars: 4, tone: "mint" }],
  [{ rttMs: 45, loss: 0.01 }, true, { bars: 3, tone: "amber" }],
  [{ rttMs: 90, loss: 0.08 }, true, { bars: 2, tone: "tangerine" }],
  [{ rttMs: null, loss: null }, false, { bars: 1, tone: "tangerine" }],
])("maps real transport health", (telemetry, connected, expected) => {
  expect(signalLevel(telemetry as any, connected)).toEqual(expected);
});
