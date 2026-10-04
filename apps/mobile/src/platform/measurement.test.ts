import { Share } from "react-native";
import { makeMeasurementRecorder } from "@wc/core";
import { exportMeasurement } from "./measurement";
test("shares sanitized JSON text through the native app adapter", async () => {
  const recorder = makeMeasurementRecorder({ targetBitrateMbps: 4, capacityMbps: null, expectedRoute: "turn_tcp" });
  recorder.add({ bitrateMbps: 4, decodedFps: 30, sourceWidth: null, sourceHeight: null, decodedWidth: 1280, decodedHeight: 720, route: "relay", addressFamily: "IPv6", relayProtocol: "tcp", totalFreezeSeconds: null, maxFreezeSeconds: null, token: "private-token", sdp: "private-sdp", address: "2001:db8::1" } as any, 2);
  const share = jest.spyOn(Share, "share").mockResolvedValue({ action: Share.sharedAction });
  await exportMeasurement(recorder.exportRun());
  const content = share.mock.calls[0][0];
  expect(content.title).toBe("Remote measurement JSON");
  expect(JSON.parse(content.message!)).toMatchObject({ expected_route: "turn_tcp", target_bitrate_mbps: 4, samples: [{ adaptive_downgrades: 2, route: "relay", max_freeze_s: null }] });
  expect(content.message).not.toMatch(/private-token|private-sdp|2001:db8::1/); share.mockRestore();
});
