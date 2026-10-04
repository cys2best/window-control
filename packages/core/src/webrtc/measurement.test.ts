import { makeMeasurementRecorder } from "./measurement";
import type { StreamTelemetry } from "./telemetry";
const sample = (overrides: Partial<StreamTelemetry> = {}): StreamTelemetry => ({
  rttMs: null, loss: null, decodeMs: null, networkMs: null, inputMs: null, jitterMs: null,
  bitrateMbps: 8, droppedFrames: null, transport: "relay", route: "relay", addressFamily: "IPv6", relayProtocol: "tls",
  sourceWidth: 1920, sourceHeight: 1080, decodedWidth: 1280, decodedHeight: 720, decodedFps: 30,
  framesDecoded: 30, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null, ...overrides,
});
test("records only sanitized measured evidence and preserves null and initial cumulative counters", () => {
  let now = 1000;
  const recorder = makeMeasurementRecorder({ targetBitrateMbps: 8, capacityMbps: 15, expectedRoute: "turn_tls" }, () => now);
  recorder.add(sample({ bitrateMbps: null, decodedFps: null }), 2); // interval baseline, not a measured sample
  now = 2000;
  recorder.add({ ...sample({ sourceWidth: null, sourceHeight: null, totalFreezeSeconds: 2, maxFreezeSeconds: 1.5 }), token: "secret-token", handle: "secret-handle", code: "123456", credential: "secret-credential", sdp: "v=0 complete SDP", address: "192.0.2.123" } as any, 2);
  now = 3000; recorder.add(sample({ framesDecoded: 60, totalFreezeSeconds: null, maxFreezeSeconds: null }), 3);
  expect(recorder.exportRun()).toEqual({ capacity_mbps: 15, target_bitrate_mbps: 8, expected_route: "turn_tls", samples: [
    { elapsed_s: 0, source_width: null, source_height: null, decoded_width: 1280, decoded_height: 720, decoded_fps: 30, target_bitrate_mbps: 8, bitrate_mbps: 8, adaptive_downgrades: 2, total_freeze_s: 2, max_freeze_s: 1.5, route: "relay", address_family: "IPv6", relay_protocol: "tls" },
    { elapsed_s: 1, source_width: 1920, source_height: 1080, decoded_width: 1280, decoded_height: 720, decoded_fps: 30, target_bitrate_mbps: 8, bitrate_mbps: 8, adaptive_downgrades: 3, total_freeze_s: null, max_freeze_s: null, route: "relay", address_family: "IPv6", relay_protocol: "tls" },
  ] });
  const json = JSON.stringify(recorder.exportRun());
  for (const secret of ["secret-token", "secret-handle", "123456", "secret-credential", "complete SDP", "192.0.2.123"]) expect(json).not.toContain(secret);
  recorder.exportRun().samples.pop(); expect(recorder.exportRun().samples).toHaveLength(2);
});
test("does not splice another route into a measurement window", () => {
  let now = 0; const recorder = makeMeasurementRecorder({ targetBitrateMbps: 8, capacityMbps: null, expectedRoute: "direct" }, () => now);
  recorder.add(sample(), 0); now = 1000;
  recorder.add(sample({ route: "direct", transport: "direct", relayProtocol: "unknown" }), 0); now = 2000;
  recorder.add(sample(), 0);
  expect(recorder.exportRun().samples).toHaveLength(1);
});
test("counter reset remains null evidence and ends the current epoch", () => {
  let now = 0; const recorder = makeMeasurementRecorder({ targetBitrateMbps: 8, capacityMbps: null, expectedRoute: "unknown" }, () => now);
  recorder.add(sample(), 4); now = 1000;
  recorder.add(sample({ bitrateMbps: null, decodedFps: null, framesDecoded: 1 }), 4); now = 2000;
  recorder.add(sample(), 4);
  expect(recorder.exportRun().samples).toHaveLength(2);
  expect(recorder.exportRun().samples[1]).toMatchObject({ bitrate_mbps: null, decoded_fps: null });
});
test("invalid numeric measurements remain null and timestamps/counts stay finite", () => {
  let now = 0; const recorder = makeMeasurementRecorder({ targetBitrateMbps: NaN, capacityMbps: Infinity, expectedRoute: "unknown" }, () => now);
  recorder.add(sample({ decodedWidth: NaN, totalFreezeSeconds: -1, maxFreezeSeconds: Infinity }), 0);
  now = NaN; recorder.add(sample(), Infinity);
  expect(recorder.exportRun()).toMatchObject({ capacity_mbps: null, target_bitrate_mbps: null, samples: [{ elapsed_s: 0, decoded_width: null, total_freeze_s: null, max_freeze_s: null, adaptive_downgrades: 0 }] });
});

test("unrecognized expected-route text cannot leak operator secrets into evidence", () => {
  const recorder = makeMeasurementRecorder({ targetBitrateMbps: 8, capacityMbps: null, expectedRoute: "credential=private-secret" });
  recorder.add(sample(), 0);
  expect(recorder.exportRun().expected_route).toBe("unknown");
  expect(JSON.stringify(recorder.exportRun())).not.toContain("private-secret");
});
