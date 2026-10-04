import { connectPeer, PeerNegotiator } from "./peer";
import { fakePc, fireReady, flush } from "./peer.testUtils";

function transport(): PeerNegotiator {
  return { exchange: jest.fn(async () => ({ answer: "ANSWER", resourceId: "resource" })), close: jest.fn(async () => {}), cancel: jest.fn(async () => {}) };
}
afterEach(() => jest.useRealTimers());
test("an exhausted setup deadline cannot start another negotiation budget", async () => {
  const negotiator = transport(), RTCImpl = jest.fn();
  await expect(connectPeer({ iceServers: [], negotiator, RTCImpl, deadline: performance.now() - 1 })).rejects.toThrow("timeout");
  expect(RTCImpl).not.toHaveBeenCalled();
  expect(negotiator.exchange).not.toHaveBeenCalled();
  expect(negotiator.cancel).toHaveBeenCalledTimes(1);
});
test("gates video and connected on answer adoption with all ICE candidates and closes once", async () => {
  const pc = fakePc(), negotiator = transport();
  let adopt!: () => void;
  pc.setRemoteDescription.mockImplementation(() => new Promise<void>(r => { adopt = r; }));
  const RTCImpl = jest.fn(function () { return pc; });
  const onStream = jest.fn(), onState = jest.fn();
  const iceServers = [{ urls: ["turn:relay"], username: "u", credential: "c" }];
  const promise = connectPeer({ iceServers, negotiator, RTCImpl, deadline: performance.now()+30000, onStream, onState });
  await flush(); fireReady(pc);
  expect(onStream).not.toHaveBeenCalled(); expect(onState).not.toHaveBeenCalledWith("connected");
  adopt(); const session = await promise;
  expect(RTCImpl).toHaveBeenCalledWith({ iceServers, iceTransportPolicy: "all" });
  expect(pc.calls.filter((x: string) => x === "addTransceiver")).toHaveLength(1);
  expect(pc.calls.indexOf("createDataChannel:input")).toBeLessThan(pc.calls.indexOf("createOffer"));
  expect(onStream).toHaveBeenCalledTimes(1); expect(onState.mock.calls.filter(([s]) => s === "connected")).toHaveLength(1);
  await session.close(); await session.close();
  expect(pc.close).toHaveBeenCalledTimes(1); expect(pc.dc.closed).toBe(true); expect(negotiator.close).toHaveBeenCalledTimes(1);
});
test.each(["createOffer", "setLocalDescription", "gathering", "exchange", "setRemoteDescription", "readiness"])("abort at %s releases the peer without exposing stale callbacks", async boundary => {
  const pc = fakePc(), negotiator = transport(), abort = new AbortController();
  let release!: (value?: any) => void;
  const blocked = new Promise<any>(r => { release = r; });
  if (boundary === "gathering") pc.iceGatheringState = "gathering";
  else if (boundary === "exchange") (negotiator.exchange as jest.Mock).mockReturnValue(blocked);
  else if (boundary !== "readiness") pc[boundary] = jest.fn(() => blocked);
  const onStream = jest.fn(), onState = jest.fn();
  const promise = connectPeer({ iceServers: [], negotiator, RTCImpl: function () { return pc; }, deadline: performance.now()+30000, signal: abort.signal, onStream, onState });
  const rejection = expect(promise).rejects.toThrow("canceled");
  await flush(); abort.abort(); await rejection;
  expect(pc.close).toHaveBeenCalledTimes(1); expect(pc.dc.closed).toBe(true);
  release(boundary === "exchange" ? {answer: "LATE", resourceId: "late"} : {type:"offer", sdp:"OFFER"});
  pc.iceGatheringState = "complete"; pc._fire("icegatheringstatechange", {}); fireReady(pc); await flush();
  expect(onStream).not.toHaveBeenCalled(); expect(onState).not.toHaveBeenCalledWith("connected");
  if (boundary === "exchange") expect(negotiator.close).toHaveBeenCalledWith("late");
  if (["createOffer", "setLocalDescription", "gathering", "exchange"].includes(boundary)) expect(negotiator.cancel).toHaveBeenCalledTimes(1);
});
test("deadline rejects promptly even when transport cancellation never completes", async () => {
  jest.useFakeTimers(); const pc = fakePc(), negotiator = transport();
  (negotiator.exchange as jest.Mock).mockReturnValue(new Promise(() => {}));
  (negotiator.cancel as jest.Mock).mockReturnValue(new Promise(() => {}));
  const promise = connectPeer({ iceServers: [], negotiator, RTCImpl: function () { return pc; }, deadline: performance.now()+30000 });
  const rejected = expect(promise).rejects.toThrow("timeout"); await flush();
  jest.advanceTimersByTime(30000); await rejected; expect(pc.close).toHaveBeenCalledTimes(1);
});
