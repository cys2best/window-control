import { connectWhep } from "./whep";

function fakePc() {
  const listeners: Record<string, Function[]> = {};
  const dc = {
    readyState: "connecting",
    bufferedAmount: 0,
    sent: [] as string[],
    closed: false,
    listeners: {} as Record<string, Function[]>,
    send(payload: string) { this.sent.push(payload); },
    close() { this.closed = true; this.readyState = "closed"; },
    addEventListener(type: string, fn: Function) { (this.listeners[type] ||= []).push(fn); },
    _fire(type: string, e?: any) { (this.listeners[type] || []).forEach((f) => f(e)); },
  };
  const pc = {
    iceConnectionState: "new",
    iceGatheringState: "complete",
    localDescription: { sdp: "OFFER" },
    dc,
    calls: [] as string[],
    listeners,
    addEventListener: (k: string, f: Function) => { (listeners[k] ||= []).push(f); },
    removeEventListener: () => {},
    addTransceiver: (...args: any[]) => { pc.calls.push("addTransceiver"); return { receiver: {} }; },
    createDataChannel: (...args: any[]) => { pc.calls.push("createDataChannel:" + args[0]); return dc; },
    createOffer: async () => { pc.calls.push("createOffer"); return { type: "offer", sdp: "OFFER" }; },
    setLocalDescription: async () => { pc.calls.push("setLocalDescription"); },
    setRemoteDescription: jest.fn(async () => { pc.calls.push("setRemoteDescription"); }),
    close: jest.fn(),
    _fire: (k: string, e: any) => (listeners[k] || []).forEach((f) => f(e)),
  } as any;
  return pc;
}

function whepResponse(overrides: any = {}) {
  return {
    ok: true,
    status: 201,
    headers: { get: (k: string) => (k.toLowerCase() === "location" ? "/whep/abc123" : null) },
    text: async () => "ANSWER",
    ...overrides,
  };
}

function fireReady(pc: any) {
  pc._fire("track", { track: { kind: "video" }, streams: [{ toURL: () => "x" }] });
  pc.iceConnectionState = "connected";
  pc._fire("iceconnectionstatechange", {});
  pc.dc._fire("open", {});
}

test("creates the input channel before the offer and posts with bearer auth", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => whepResponse()) as any;
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "fresh-token",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();
  fireReady(pc);
  await promise;

  expect(pc.calls.indexOf("createDataChannel:input")).toBeLessThan(pc.calls.indexOf("createOffer"));
  expect(fetchImpl).toHaveBeenCalledWith("http://host/whep", expect.objectContaining({
    method: "POST",
    headers: expect.objectContaining({
      "Content-Type": "application/sdp",
      "Authorization": "Bearer fresh-token",
    }),
    body: "OFFER",
  }));
});

test("applies iceServers from selection directly", async () => {
  let capturedConfig: any = null;
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => whepResponse()) as any;
  const iceServers = [{ urls: "stun:h:3478" }, { urls: "turn:h:3478", username: "u", credential: "c" }];
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers,
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    RTCImpl: function (config: any) { capturedConfig = config; return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();
  fireReady(pc);
  await promise;
  expect(capturedConfig).toEqual({ iceServers });
});

test("does not POST a partial offer before ICE gathering is complete", async () => {
  jest.useFakeTimers();
  try {
    const pc = fakePc();
    pc.iceGatheringState = "gathering";
    const fetchImpl = jest.fn(async () => whepResponse()) as any;
    const promise = connectWhep({
      whepUrl: "http://host/whep",
      whepToken: "tok",
      iceServers: [],
      onStream: () => {},
      onInputRtt: () => {},
      onState: () => {},
      RTCImpl: function () { return pc; } as any,
      fetchImpl,
      timeoutMs: 8000,
    });
    await Promise.resolve();
    await Promise.resolve();

    pc._fire("icecandidate", {
      candidate: { candidate: "candidate:1 1 UDP 1 203.0.113.2 5000 typ srflx" },
    });
    jest.advanceTimersByTime(300);
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchImpl).not.toHaveBeenCalled();

    pc.iceGatheringState = "complete";
    pc._fire("icegatheringstatechange", {});
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchImpl).toHaveBeenCalledWith(
      "http://host/whep",
      expect.objectContaining({ method: "POST", body: "OFFER" }),
    );
    fireReady(pc);
    await promise;
  } finally {
    jest.useRealTimers();
  }
});

test("resolves relative Location against whepUrl and DELETEs without bearer on close", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async (url: string, init: any) => {
    if (init && init.method === "DELETE") return { ok: true };
    return whepResponse();
  }) as any;
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();
  fireReady(pc);
  const session = await promise;
  await session.close();
  expect(fetchImpl).toHaveBeenCalledWith("http://host/whep/abc123", expect.objectContaining({ method: "DELETE" }));
  const deleteCall = fetchImpl.mock.calls.find((c: any) => c[1] && c[1].method === "DELETE");
  expect(deleteCall[1].headers).toBeUndefined();
});

test("rejects a successful WHEP response without Location", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => whepResponse({
    headers: { get: () => null },
  })) as any;
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
    timeoutMs: 20,
  });

  await expect(promise).rejects.toMatchObject({ code: "missing-location" });
  expect(pc.close).toHaveBeenCalledTimes(1);
});

test("readiness requires ICE, video track, and input channel conjunction", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => whepResponse()) as any;
  const states: string[] = [];
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: (s) => states.push(s),
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();

  // Only ICE ready: not resolved yet.
  pc.iceConnectionState = "connected";
  pc._fire("iceconnectionstatechange", {});
  expect(states).not.toContain("connected");

  // Track arrives too: still missing channel.
  pc._fire("track", { track: { kind: "video" }, streams: [{ toURL: () => "x" }] });
  expect(states).not.toContain("connected");

  // Channel opens last: now ready.
  pc.dc._fire("open", {});
  const session = await promise;
  expect(session.pc).toBe(pc);
  expect(states[states.length - 1]).toBe("connected");
});

test("rejects after the timeout when readiness is never reached", async () => {
  jest.useFakeTimers();
  try {
    const pc = fakePc();
    const fetchImpl = jest.fn(async () => whepResponse()) as any;
    const promise = connectWhep({
      whepUrl: "http://host/whep",
      whepToken: "tok",
      iceServers: [],
      onStream: () => {},
      onInputRtt: () => {},
      onState: () => {},
      RTCImpl: function () { return pc; } as any,
      fetchImpl,
      timeoutMs: 8000,
    });
    const assertion = expect(promise).rejects.toThrow();
    await Promise.resolve();
    await Promise.resolve();
    jest.advanceTimersByTime(8000);
    await assertion;
  } finally {
    jest.useRealTimers();
  }
});

test("closed input channel before readiness fails the session", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => whepResponse()) as any;
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();
  pc.dc._fire("close", {});
  await expect(promise).rejects.toThrow();
});

test("delivers echo RTT without logging raw payload", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => whepResponse()) as any;
  const rtts: number[] = [];
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: (ms) => rtts.push(ms),
    onState: () => {},
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();
  fireReady(pc);
  await promise;
  const now = Date.now();
  pc.dc._fire("message", { data: JSON.stringify({ type: "echo", t: now - 42 }) });
  expect(rtts.length).toBe(1);
  expect(rtts[0]).toBeGreaterThanOrEqual(40);
});

test("close is idempotent and DELETEs even if invoked before the POST resolves", async () => {
  const pc = fakePc();
  let resolvePost: (v: any) => void;
  const postPromise = new Promise((resolve) => { resolvePost = resolve; });
  const fetchImpl = jest.fn(async (url: string, init: any) => {
    if (init && init.method === "DELETE") return { ok: true };
    await postPromise;
    return whepResponse();
  }) as any;
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  promise.catch(() => {});
  // Let execution reach and enter the POST (fetchImpl called, awaiting
  // postPromise) before the ICE failure fires close() — this exercises the
  // race where close() runs while resourceUrl is still unknown, and the
  // WHEP resource is only discovered after cleanup already started.
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  expect(fetchImpl).toHaveBeenCalledWith("http://host/whep", expect.objectContaining({ method: "POST" }));

  pc.iceConnectionState = "failed";
  pc._fire("iceconnectionstatechange", {});
  await expect(promise).rejects.toThrow();

  resolvePost!(undefined);
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  const deleteCall = fetchImpl.mock.calls.find((c: any) => c[1] && c[1].method === "DELETE");
  expect(deleteCall).toBeTruthy();
});

test("resource cleanup on superseded instance switch closes and DELETEs", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async (url: string, init: any) => {
    if (init && init.method === "DELETE") return { ok: true };
    return whepResponse();
  }) as any;
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();
  fireReady(pc);
  const session = await promise;
  await session.close();
  await session.close(); // idempotent
  expect(pc.close).toHaveBeenCalledTimes(1);
  const deleteCalls = fetchImpl.mock.calls.filter((c: any) => c[1] && c[1].method === "DELETE");
  expect(deleteCalls.length).toBe(1);
});
