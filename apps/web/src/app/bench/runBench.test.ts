import { runBench } from "./runBench";

jest.mock("@wc/core", () => {
  const actual = jest.requireActual("@wc/core");
  return {
    ...actual,
    // The stream connects, then drops before the engine answers anything.
    connectWhep: jest.fn(async (opts: any) => {
      setTimeout(() => opts.onState("disconnected"), 10);
      return { pc: { getStats: async () => new Map() }, input: { send: () => {} }, close: async () => {} };
    }),
  };
});

test("a stream that drops mid-run fails with a disconnect, not a bench-mode error", async () => {
  HTMLCanvasElement.prototype.getContext = jest.fn(() => ({})) as any;
  const video = { play: async () => {}, requestVideoFrameCallback: jest.fn() } as any;
  await expect(runBench({
    select: async () => ({ whep_url: "http://host/whep", whep_token: "t", ice_servers: [] }) as any,
    serial: "emulator-5556",
    count: 5,
    warmup: 1,
    video,
    onStatus: () => {},
  })).rejects.toThrow("stream disconnected mid-run");
});

test("sets the requested quality tier before selecting the instance", async () => {
  HTMLCanvasElement.prototype.getContext = jest.fn(() => ({})) as any;
  const calls: string[] = [];
  const video = { play: async () => {}, requestVideoFrameCallback: jest.fn() } as any;
  await runBench({
    setQuality: async (serial: string, tier: string) => { calls.push(`quality:${serial}:${tier}`); },
    select: async () => { calls.push("select"); return { whep_url: "http://host/whep", whep_token: "t", ice_servers: [] } as any; },
    serial: "emulator-5556",
    tier: "360",
    count: 5,
    warmup: 1,
    video,
    onStatus: () => {},
  }).catch(() => {});
  expect(calls.slice(0, 2)).toEqual(["quality:emulator-5556:360", "select"]);
});

test("clears the target page's timings before measuring", async () => {
  HTMLCanvasElement.prototype.getContext = jest.fn(() => ({})) as any;
  const calls: string[] = [];
  const video = { play: async () => {}, requestVideoFrameCallback: jest.fn() } as any;
  await runBench({
    select: async () => { calls.push("select"); return { whep_url: "http://host/whep", whep_token: "t", ice_servers: [] } as any; },
    targetTimings: { clear: async () => { calls.push("clear"); }, list: async () => [] },
    serial: "emulator-5556",
    count: 5,
    warmup: 1,
    video,
    onStatus: () => {},
  }).catch(() => {});
  expect(calls).toEqual(["select", "clear"]);
});
