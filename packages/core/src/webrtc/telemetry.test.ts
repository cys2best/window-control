import { makeTelemetrySampler, signalLevel, StreamTelemetry } from "./telemetry";

function stats({ bytesReceived, timestamp }: { bytesReceived: number; timestamp?: number }) {
  return new Map([
    ["inbound-video", {
      type: "inbound-rtp", kind: "video", bytesReceived, timestamp,
      packetsReceived: 980, packetsLost: 20, framesDecoded: 100,
      totalDecodeTime: 0.4, jitterBufferDelay: 0.6,
      jitterBufferEmittedCount: 100, framesDropped: 3,
    }],
    ["candidate-pair", {
      type: "candidate-pair", state: "succeeded", nominated: true, currentRoundTripTime: 0.018,
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
    transport: "unknown",
  });
});

test("preserves unavailable metrics as null and accepts input echo RTT", async () => {
  const samples: StreamTelemetry[] = [];
  const sampler = makeTelemetrySampler({
    pc: { getStats: async () => new Map() },
    transport: "local",
    onSample: (sample) => samples.push(sample),
  });

  sampler.setInputRtt(11);
  await sampler.sample();

  expect(samples[0]).toMatchObject({
    rttMs: null, loss: null, decodeMs: null, networkMs: null, inputMs: 11,
    jitterMs: null, bitrateMbps: null, droppedFrames: null, transport: "unknown",
  });
});

test("leaves bitrate unavailable when inbound RTC stats omit timestamps", async () => {
  let now = 1_000;
  const reports = [
    stats({ bytesReceived: 1_000_000, timestamp: undefined }),
    stats({ bytesReceived: 2_000_000, timestamp: undefined }),
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

  expect(samples[1].bitrateMbps).toBeNull();
});

test.each([
  [{ rttMs: 20, loss: 0 }, true, { bars: 4, tone: "mint" }],
  [{ rttMs: 45, loss: 0.01 }, true, { bars: 3, tone: "amber" }],
  [{ rttMs: 90, loss: 0.08 }, true, { bars: 2, tone: "tangerine" }],
  [{ rttMs: null, loss: null }, false, { bars: 1, tone: "tangerine" }],
])("maps real transport health", (telemetry, connected, expected) => {
  expect(signalLevel(telemetry as any, connected)).toEqual(expected);
});

test("remote lifecycle transport does not invent a measured route", async () => {
  const onSample = jest.fn();
  const sampler = makeTelemetrySampler({ pc: { getStats: async () => new Map() }, transport: "remote", onSample } as any);
  expect((await sampler.sample()).transport).toBe("unknown");
});

function selectedStats(local: any = {}, remote: any = {}, inbound: any = {}) {
  return new Map<string, any>([
    ["transport", { type: "transport", selectedCandidatePairId: "chosen" }],
    ["chosen", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: .02, localCandidateId: "local", remoteCandidateId: "remote" }],
    ["unused", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: .9, localCandidateId: "other", remoteCandidateId: "remote" }],
    ["local", { type: "local-candidate", candidateType: "host", address: "192.0.2.1", ...local }],
    ["remote", { type: "remote-candidate", candidateType: "srflx", address: "192.0.2.2", ...remote }],
    ["other", { type: "local-candidate", candidateType: "relay", relayProtocol: "tls" }],
    ["video", { type: "inbound-rtp", kind: "video", ...inbound }],
  ]);
}
function samplerFor(getStats: () => Promise<any>, extra: any = {}) {
  return makeTelemetrySampler({ pc: { getStats }, transport: "remote", onSample: () => {}, ...extra });
}
test("the transport-selected pair wins over another succeeded pair", async () => {
  expect(await samplerFor(async () => selectedStats()).sample()).toMatchObject({
    rttMs: 20, transport: "direct", route: "direct", addressFamily: "IPv4", relayProtocol: "unknown",
    decodedFps: null, totalFreezeSeconds: null, maxFreezeSeconds: null,
  });
});
test.each([
  [{ candidateType: "relay", relayProtocol: "udp" }, {}, "IPv4", "udp"],
  [{ address: "2001:db8::1" }, { candidateType: "relay", address: "2001:db8::2", relayProtocol: "tcp" }, "IPv6", "tcp"],
  [{ candidateType: "relay", address: "2001:db8::1", relayProtocol: "tls" }, {}, "mixed", "tls"],
  [{ candidateType: "relay", protocol: "tcp" }, {}, "IPv4", "unknown"],
  [{ candidateType: "relay", address: "obscured.local" }, {}, "unknown", "unknown"],
])("measures either selected relay endpoint and explicit family/protocol evidence", async (local, remote, family, protocol) => {
  expect(await samplerFor(async () => selectedStats(local, remote)).sample()).toMatchObject({
    route: "relay", transport: "relay", addressFamily: family, relayProtocol: protocol,
  });
});
test.each([["selected", false], ["nominated", false], ["selected", true], ["nominated", true]])("uses a unique %s succeeded fallback when transport linkage is missing (dangling: %s)", async (flag, dangling) => {
  const reports = selectedStats(); reports.delete("transport"); reports.get("chosen")[String(flag)] = true;
  if (dangling) reports.set("transport", { type: "transport", selectedCandidatePairId: "missing" });
  expect(await samplerFor(async () => reports).sample()).toMatchObject({ route: "direct", rttMs: 20 });
});
test.each(["succeeded", "nominated", "multiple transports", "broken link"])("keeps ambiguous %s evidence unknown", async ambiguity => {
  const reports = selectedStats(); reports.delete("transport");
  if (ambiguity === "nominated") { reports.get("chosen").nominated = true; reports.get("unused").nominated = true; }
  if (ambiguity === "multiple transports") {
    reports.set("t1", { type: "transport", selectedCandidatePairId: "chosen" });
    reports.set("t2", { type: "transport", selectedCandidatePairId: "unused" });
  }
  if (ambiguity === "broken link") reports.set("t1", { type: "transport", selectedCandidatePairId: "missing" });
  expect(await samplerFor(async () => reports).sample()).toMatchObject({ route: "unknown", transport: "unknown", rttMs: null });
});
test("measures frame deltas and dimensions independently without fabricating freezes", async () => {
  let report = selectedStats({}, {}, { bytesReceived: 1000, framesDecoded: 20, timestamp: 1000, frameWidth: 1280, frameHeight: 720 });
  const sampler = samplerFor(async () => report, { sourceDimensions: () => ({ width: 1600, height: 900 }) });
  await sampler.sample();
  report = selectedStats({}, {}, { bytesReceived: 1001000, framesDecoded: 50, timestamp: 2000, frameWidth: 1280, frameHeight: 720 });
  expect(await sampler.sample()).toMatchObject({ sourceWidth: 1600, sourceHeight: 900, decodedWidth: 1280, decodedHeight: 720, decodedFps: 30, framesDecoded: 50, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null });
});
test("counter reset or selected pair replacement clears both interval baselines", async () => {
  let report = selectedStats({}, {}, { bytesReceived: 1000000, framesDecoded: 100, timestamp: 1000 });
  const sampler = samplerFor(async () => report); await sampler.sample();
  report = selectedStats({}, {}, { bytesReceived: 10, framesDecoded: 1, timestamp: 2000 });
  expect(await sampler.sample()).toMatchObject({ bitrateMbps: null, decodedFps: null });
  report = selectedStats({}, {}, { bytesReceived: 1000010, framesDecoded: 31, timestamp: 3000 });
  expect(await sampler.sample()).toMatchObject({ bitrateMbps: 8, decodedFps: 30 });
  report.get("transport").selectedCandidatePairId = "unused";
  expect(await sampler.sample()).toMatchObject({ bitrateMbps: null, decodedFps: null });
});
test("preserves measured native freezes and absent dimensions independently", async () => {
  expect(await samplerFor(async () => selectedStats({}, {}, { freezeCount: 2, totalFreezesDuration: 1.7, maxFreezeDuration: 1.2 })).sample()).toMatchObject({ sourceWidth: null, sourceHeight: null, decodedWidth: null, decodedHeight: null, freezeCount: 2, totalFreezeSeconds: 1.7, maxFreezeSeconds: 1.2 });
});
