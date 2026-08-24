import { connectPublicWhep } from "./publicWhep";

function fakePc() {
  const listeners: Record<string, Function[]> = {};
  return {
    iceGatheringState: "complete",
    iceConnectionState: "new",
    localDescription: { sdp: "OFFER" },
    addEventListener: (k: string, f: Function) => { (listeners[k] ||= []).push(f); },
    removeEventListener: () => {},
    addTransceiver: () => ({ receiver: {} }),
    createOffer: async () => ({ type: "offer", sdp: "OFFER" }),
    setLocalDescription: async () => {},
    setRemoteDescription: jest.fn(async () => {}),
    close: jest.fn(),
    _fire: (k: string, e: any) => (listeners[k] || []).forEach((f) => f(e)),
  } as any;
}

function fakeWs() {
  const ws: any = {
    sent: [] as string[],
    onopen: null,
    onmessage: null,
    onerror: null,
    onclose: null,
    send: jest.fn((s: string) => ws.sent.push(s)),
    close: jest.fn(),
  };
  return ws;
}

test("sends the offer SDP over the socket after ICE gathering settles, then applies the answer", async () => {
  const pc = fakePc();
  let ws: any;
  const WsImpl = function (this: any, _url: string) {
    ws = fakeWs();
    return ws;
  } as any;

  connectPublicWhep({
    signalingUrl: "wss://relay.example", instanceName: "inst-1", iceServers: [],
    onStream: () => {}, onState: () => {},
    RTCImpl: function () { return pc; } as any, WsImpl,
  });

  await new Promise((r) => setTimeout(r, 0));
  expect(ws).toBeDefined();
  // ws.onopen triggers offer creation + send
  await ws.onopen();
  await new Promise((r) => setTimeout(r, 0));

  expect(ws.send).toHaveBeenCalledWith("OFFER");

  await ws.onmessage({ data: "ANSWER" });
  expect(pc.setRemoteDescription).toHaveBeenCalledWith({ type: "answer", sdp: "ANSWER" });
});

test("builds the signaling URL with session + role query params", async () => {
  const pc = fakePc();
  let capturedUrl = "";
  const WsImpl = function (this: any, url: string) {
    capturedUrl = url;
    return fakeWs();
  } as any;

  connectPublicWhep({
    signalingUrl: "wss://relay.example", instanceName: "my instance", iceServers: [],
    onStream: () => {}, onState: () => {},
    RTCImpl: function () { return pc; } as any, WsImpl,
  });

  expect(capturedUrl).toBe("wss://relay.example/?session=my%20instance&role=viewer");
});

test("onState('connected') fires only on iceConnectionState connected/completed, not merely on onmessage", async () => {
  const pc = fakePc();
  let ws: any;
  const WsImpl = function (this: any, _url: string) {
    ws = fakeWs();
    return ws;
  } as any;
  const states: string[] = [];

  connectPublicWhep({
    signalingUrl: "wss://relay.example", instanceName: "inst-1", iceServers: [],
    onStream: () => {}, onState: (s) => states.push(s),
    RTCImpl: function () { return pc; } as any, WsImpl,
  });

  await new Promise((r) => setTimeout(r, 0));
  await ws.onopen();
  await new Promise((r) => setTimeout(r, 0));
  await ws.onmessage({ data: "ANSWER" });

  expect(states).toEqual(["connecting"]);

  pc.iceConnectionState = "connected";
  pc._fire("iceconnectionstatechange", {});

  expect(states).toEqual(["connecting", "connected"]);
});

test("onState('failed') fires on iceConnectionState failed/closed", async () => {
  const pc = fakePc();
  const WsImpl = function (this: any, _url: string) { return fakeWs(); } as any;
  const states: string[] = [];

  connectPublicWhep({
    signalingUrl: "wss://relay.example", instanceName: "inst-1", iceServers: [],
    onStream: () => {}, onState: (s) => states.push(s),
    RTCImpl: function () { return pc; } as any, WsImpl,
  });

  pc.iceConnectionState = "failed";
  pc._fire("iceconnectionstatechange", {});

  expect(states).toEqual(["connecting", "failed"]);
});

test("close() closes both the socket and the peer connection, and suppresses further state callbacks", async () => {
  const pc = fakePc();
  let ws: any;
  const WsImpl = function (this: any, _url: string) {
    ws = fakeWs();
    return ws;
  } as any;
  const states: string[] = [];

  const { close } = connectPublicWhep({
    signalingUrl: "wss://relay.example", instanceName: "inst-1", iceServers: [],
    onStream: () => {}, onState: (s) => states.push(s),
    RTCImpl: function () { return pc; } as any, WsImpl,
  });

  close();

  expect(ws.close).toHaveBeenCalled();
  expect(pc.close).toHaveBeenCalled();

  pc.iceConnectionState = "connected";
  pc._fire("iceconnectionstatechange", {});
  expect(states).toEqual(["connecting"]); // no "connected" after close
});
