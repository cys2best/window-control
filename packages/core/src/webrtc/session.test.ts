import { connectEngineSession } from "./session";
import * as core from "../index";

const localSelection = {
  whep_url: "http://192.168.1.10:8080/whep",
  whep_token: "tokLocal",
  ice_servers: [],
} as any;

const fakeInput = () => ({ close: () => {}, send: () => {} }) as any;

describe("connectEngineSession", () => {
  test("connects over the local transport", async () => {
    const session = await connectEngineSession({
      selection: localSelection,
      startLocalImpl: async () => ({
        kind: "local",
        stream: { id: "local-vid" } as any,
        input: fakeInput(),
        close: async () => {},
      }),
    });

    expect(session.kind).toBe("local");
    expect(session.stream).toEqual({ id: "local-vid" });
  });

  test("rejects when no transport is configured", async () => {
    await expect(connectEngineSession({
      selection: { whep_url: "", whep_token: "", ice_servers: [] } as any,
    })).rejects.toThrow("No engine session transport is configured");
  });

  test("rejects with the local transport's own error", async () => {
    const states: string[] = [];
    await expect(connectEngineSession({
      selection: localSelection,
      onState: (state) => states.push(state),
      startLocalImpl: async () => { throw new Error("Local failed"); },
    })).rejects.toThrow("Local failed");
    expect(states).toEqual(["connecting"]);
  });

  test("forwards callbacks and fires disconnected on close", async () => {
    const states: string[] = [];
    const streams: any[] = [];
    const rtts: number[] = [];
    let capturedRttCb: ((ms: number) => void) | null = null;
    let capturedStateCb: ((state: any) => void) | null = null;

    const session = await connectEngineSession({
      selection: localSelection,
      onState: (st) => states.push(st),
      onStream: (st) => streams.push(st),
      onInputRtt: (ms) => rtts.push(ms),
      connectWhepImpl: async (opts) => {
        capturedRttCb = opts.onInputRtt;
        capturedStateCb = opts.onState;
        opts.onStream({ id: "local-vid" });
        return { pc: {} as any, input: fakeInput(), close: async () => {} };
      },
    });

    expect(session.kind).toBe("local");
    expect(states).toEqual(["connecting", "connected"]);
    expect(streams).toEqual([{ id: "local-vid" }]);

    capturedRttCb!(42);
    expect(rtts).toEqual([42]);

    capturedStateCb!("disconnected");
    expect(states).toEqual(["connecting", "connected", "disconnected"]);

    await session.close();
    expect(states).toEqual(["connecting", "connected", "disconnected"]);
  });

  test("close fires disconnected exactly once", async () => {
    const states: string[] = [];
    const session = await connectEngineSession({
      selection: localSelection,
      onState: (st) => states.push(st),
      connectWhepImpl: async () => ({ pc: {} as any, input: fakeInput(), close: async () => {} }),
    });
    await session.close();
    await session.close();
    expect(states).toEqual(["connecting", "connected", "disconnected"]);
  });

  test("passes the selection's WHEP details to connectWhep", async () => {
    let captured: any = null;
    const session = await connectEngineSession({
      selection: { ...localSelection, ice_servers: [{ urls: "stun:192.168.1.10:3478" }] },
      connectWhepImpl: async (opts) => {
        captured = opts;
        opts.onStream({ id: "whep-stream" });
        return { pc: {} as any, input: fakeInput(), close: async () => {} };
      },
    });

    expect(captured.whepUrl).toBe("http://192.168.1.10:8080/whep");
    expect(captured.whepToken).toBe("tokLocal");
    expect(captured.iceServers).toEqual([{ urls: "stun:192.168.1.10:3478" }]);
    expect(session.stream).toEqual({ id: "whep-stream" });
  });

  test("falls back to globalThis.RTCPeerConnection when RTCImpl is omitted", async () => {
    let capturedRTC: any = null;
    const origRTC = (globalThis as any).RTCPeerConnection;
    try {
      (globalThis as any).RTCPeerConnection = function FakeGlobalRTC() {};
      await connectEngineSession({
        selection: localSelection,
        connectWhepImpl: async (opts) => {
          capturedRTC = opts.RTCImpl;
          return { pc: {} as any, input: fakeInput(), close: async () => {} };
        },
      });
      expect(capturedRTC).toBe((globalThis as any).RTCPeerConnection);
    } finally {
      (globalThis as any).RTCPeerConnection = origRTC;
    }
  });

  test("the public signaling client is not exported", () => {
    expect((core as any).connectSignalingViewer).toBeUndefined();
  });
});
