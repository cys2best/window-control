import { createInputSender, type InputSender } from "../input/inputChannel";
import type { IceServer } from "../api/client";

export type PeerNegotiator = {
  exchange(offer: string, deadline: number, signal: AbortSignal): Promise<{ answer: string; resourceId: string }>;
  close(resourceId: string): Promise<void>;
  cancel(): Promise<void>;
};
export type PeerSession = { pc: any; input: InputSender; stream?: any; close(): Promise<void> };
export type ConnectPeerOpts = {
  iceServers: IceServer[]; negotiator: PeerNegotiator; deadline: number; signal?: AbortSignal; RTCImpl: any;
  onStream?: (stream: any) => void;
  onState?: (state: "connecting" | "connected" | "disconnected") => void;
  onInputRtt?: (ms: number) => void;
};
export function peerError(code: string): Error {
  return Object.assign(new Error(code), { code });
}

export function waitForIceGatheringComplete(pc: any, capMs = 4000, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(peerError("canceled"));
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", check);
      signal?.removeEventListener("abort", abort);
      error ? reject(error) : resolve();
    };
    const check = () => { if (pc.iceGatheringState === "complete") finish(); };
    const abort = () => finish(peerError("canceled"));
    const timer = setTimeout(() => finish(peerError("ice-gathering-timeout")), Math.max(0, capMs));
    pc.addEventListener("icegatheringstatechange", check);
    signal?.addEventListener("abort", abort, { once: true });
    check();
  });
}

// The second argument is reserved for the WHEP wrapper's legacy timeoutMs<=0
// behavior. Remote callers always use the finite absolute deadline alone.
export function connectPeer(opts: ConnectPeerOpts, legacyLocalGatheringCapMs?: number): Promise<PeerSession> {
  const controller = new AbortController();
  let pc: any, channel: any, input: InputSender;
  let closed = false, adopted = false, answerApplied = false, channelReady = false;
  let videoStream: any, resourceId: string | undefined;
  let cleanup: Promise<void> | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let resolve!: (session: PeerSession) => void, reject!: (error: Error) => void;
  const ready = new Promise<PeerSession>((yes, no) => { resolve = yes; reject = no; });
  const safeState = (state: "connecting" | "connected" | "disconnected") => { try { opts.onState?.(state); } catch {} };
  const invoke = (fn: () => Promise<void>) => { try { return Promise.resolve(fn()); } catch (error) { return Promise.reject(error); } };
  function close(): Promise<void> {
    if (cleanup) return cleanup;
    closed = true;
    clearTimeout(timer);
    opts.signal?.removeEventListener("abort", abort);
    // Publish the cleanup promise before abort can synchronously call listeners.
    cleanup = Promise.resolve().then(() => resourceId === undefined ? opts.negotiator.cancel() : opts.negotiator.close(resourceId));
    controller.abort();
    input?.close();
    try { channel?.close(); } catch {}
    try { pc?.close(); } catch {}
    if (adopted) safeState("disconnected");
    else reject(peerError("canceled"));
    return cleanup;
  }
  function fail(error: Error) {
    if (closed) return;
    reject(error);
    void close().catch(() => {});
  }
  const abort = () => fail(peerError("canceled"));
  const checkActive = () => {
    if (closed) return false;
    if (performance.now() >= opts.deadline) { fail(peerError("timeout")); return false; }
    return true;
  };
  function checkReady() {
    if (adopted || !checkActive() || !answerApplied || !videoStream || !channelReady) return;
    if (!["connected", "completed"].includes(pc.iceConnectionState)) return;
    adopted = true;
    clearTimeout(timer);
    try { opts.onStream?.(videoStream); } catch {}
    safeState("connected");
    resolve({ pc, input, get stream() { return videoStream; }, close });
  }
  function listen(target: any, type: string, handler: (event?: any) => void) {
    if (typeof target.addEventListener === "function") target.addEventListener(type, handler);
    else target[`on${type}`] = handler;
  }
  if (!Number.isFinite(opts.deadline) || opts.deadline <= performance.now()) { fail(peerError("timeout")); return ready; }
  if (opts.signal?.aborted) { abort(); return ready; }
  opts.signal?.addEventListener("abort", abort, { once: true });
  if (legacyLocalGatheringCapMs === undefined) timer = setTimeout(() => fail(peerError("timeout")), Math.max(0, opts.deadline - performance.now()));
  try {
    pc = new opts.RTCImpl({ iceServers: opts.iceServers, iceTransportPolicy: "all" });
    listen(pc, "track", event => {
      if (closed || event.track?.kind !== "video" || !event.streams?.[0]) return;
      videoStream = event.streams[0];
      if (adopted) { try { opts.onStream?.(videoStream); } catch {} }
      else checkReady();
    });
    listen(pc, "iceconnectionstatechange", () => {
      if (closed) return;
      if (["failed", "closed"].includes(pc.iceConnectionState)) fail(peerError("ice-failed"));
      else checkReady();
    });
    pc.addTransceiver("video", { direction: "recvonly" });
    channel = pc.createDataChannel("input", { ordered: true });
    input = createInputSender(channel);
    channelReady = channel.readyState === "open";
    listen(channel, "open", () => { if (!closed) { channelReady = true; checkReady(); } });
    listen(channel, "message", event => {
      if (closed || !adopted) return;
      try { const msg = JSON.parse(event.data); if (msg?.type === "echo" && typeof msg.t === "number") opts.onInputRtt?.(Date.now() - msg.t); } catch {}
    });
    listen(channel, "close", () => fail(peerError("input-closed")));
    listen(channel, "error", () => fail(peerError("input-failed")));
    safeState("connecting");
    void (async () => {
      try {
        const offer = await pc.createOffer();
        if (!checkActive()) return;
        await pc.setLocalDescription(offer);
        if (!checkActive()) return;
        await waitForIceGatheringComplete(pc, legacyLocalGatheringCapMs ?? opts.deadline - performance.now(), controller.signal);
        if (!checkActive()) return;
        const result = await opts.negotiator.exchange(pc.localDescription.sdp, opts.deadline, controller.signal);
        if (!checkActive()) { void invoke(() => opts.negotiator.close(result.resourceId)).catch(() => {}); return; }
        resourceId = result.resourceId;
        await pc.setRemoteDescription({ type: "answer", sdp: result.answer });
        if (!checkActive()) return;
        answerApplied = true;
        checkReady();
      } catch (error: any) {
        // Neither SDP nor arbitrary platform/network messages belong in UI errors.
        const code = typeof error?.code === "string" ? error.code : "failed";
        fail(peerError(code));
      }
    })();
  } catch { fail(peerError("failed")); }
  return ready;
}
