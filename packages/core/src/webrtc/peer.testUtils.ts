export function fakePc() {
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

export function fireReady(pc: any) {
  pc._fire("track", { track: { kind: "video" }, streams: [{ toURL: () => "x" }] });
  pc.iceConnectionState = "connected";
  pc._fire("iceconnectionstatechange", {});
  pc.dc._fire("open", {});
}

export async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
