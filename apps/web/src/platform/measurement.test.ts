import { makeMeasurementRecorder } from "@wc/core";
import { exportMeasurement } from "./measurement";
test("downloads a JSON evidence file without media credentials and releases its object URL", async () => {
  const recorder = makeMeasurementRecorder({ targetBitrateMbps: 8, capacityMbps: null, expectedRoute: "direct" });
  recorder.add({ bitrateMbps: 8, decodedFps: 30, sourceWidth: null, sourceHeight: null, decodedWidth: 1280, decodedHeight: 720, route: "direct", addressFamily: "IPv4", relayProtocol: "unknown", totalFreezeSeconds: null, maxFreezeSeconds: null, token: "private-token", sdp: "private-sdp", address: "192.0.2.123" } as any, 0);
  let blob!: Blob;
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: jest.fn((value: Blob) => { blob = value; return "blob:evidence"; }) });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: jest.fn() });
  let filename = "", href = "";
  const click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { filename = this.download; href = this.href; });
  await exportMeasurement(recorder.exportRun());
  expect(filename).toBe("remote-measurement.json"); expect(href).toBe("blob:evidence");
  expect(blob.type).toBe("application/json");
  const json = await new Promise<string>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob); });
  expect(JSON.parse(json)).toMatchObject({ capacity_mbps: null, expected_route: "direct", samples: [{ source_width: null, decoded_fps: 30, max_freeze_s: null }] });
  expect(json).not.toMatch(/private-token|private-sdp|192\.0\.2\.123/);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:evidence");
  expect(document.querySelector("a[download]")).toBeNull(); click.mockRestore();
});
