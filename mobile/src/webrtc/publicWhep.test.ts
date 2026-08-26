import { connectPublicWhep } from "./publicWhep";

function fakePc(iceGatheringState: string = "complete") {
  const listeners: Record<string, Function[]> = {};
  return {
    iceGatheringState,
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

function candidateEvent(typ: string) {
  return { candidate: { candidate: `candidate:1 1 udp 1 1.2.3.4 1234 typ ${typ}` } };
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

test("waits for a relay candidate (not merely srflx) before sending the offer over the public path", async () => {
  // TURN allocation is a slower round-trip than a plain STUN query, so the
  // relay candidate typically lands AFTER srflx. signaling_bridge.py is
  // non-trickle (one recv, one send) -- if the offer went out as soon as
  // srflx arrived, the one candidate type that can reach a NAT'd viewer
  // would be gathered too late and lost for the session. Start gathering
  // "incomplete" so the fast path actually has to wait on candidate events
  // instead of short-circuiting immediately.
  const pc = fakePc("gathering");
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
  ws.onopen(); // don't await -- it's pending on ICE gathering below
  await new Promise((r) => setTimeout(r, 0));

  // A srflx candidate alone must NOT resolve the wait for the public path.
  pc._fire("icecandidate", candidateEvent("srflx"));
  await new Promise((r) => setTimeout(r, 0));
  expect(ws.send).not.toHaveBeenCalled();

  // The relay candidate is the one that unblocks sending the offer.
  pc._fire("icecandidate", candidateEvent("relay"));
  await new Promise((r) => setTimeout(r, 0));
  expect(ws.send).toHaveBeenCalledWith("OFFER");
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

test("once ICE reports connected, the signaling socket is closed and a later onclose/onerror is a no-op (finding #2)", async () => {
  // Mirrors app.js's `settled` flag: once negotiation has actually
  // succeeded, the WS has done its job (and is closed here, freeing the
  // VPS's one-role-slot) -- a LATER ws.onclose/onerror (including the one
  // we just triggered ourselves by calling ws.close()) must not be
  // mistaken for the stream dying.
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

  pc.iceConnectionState = "connected";
  pc._fire("iceconnectionstatechange", {});

  expect(states).toEqual(["connecting", "connected"]);
  // Negotiation succeeded -- the signaling socket is no longer needed.
  expect(ws.close).toHaveBeenCalledTimes(1);

  // A later close/error on the (now-closed) socket must not be reported as
  // a stream failure -- media has already moved off the signaling path.
  ws.onclose();
  ws.onerror();

  expect(states).toEqual(["connecting", "connected"]);
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
