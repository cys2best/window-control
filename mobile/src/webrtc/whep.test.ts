import { connectWhep } from "./whep";

function fakePc() {
  const listeners: Record<string, Function[]> = {};
  return {
    iceGatheringState: "complete",
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

test("posts the offer SDP and applies the answer", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => ({ ok: true, text: async () => "ANSWER" })) as any;
  connectWhep({
    whepUrl: "http://h/whep", stunUrl: "stun:h:3478",
    onStream: () => {}, onState: () => {},
    RTCImpl: function () { return pc; } as any, fetchImpl,
  });
  await new Promise((r) => setTimeout(r, 0));
  expect(fetchImpl).toHaveBeenCalledWith("http://h/whep", expect.objectContaining({ method: "POST", body: "OFFER" }));
  expect(pc.setRemoteDescription).toHaveBeenCalled();
});
