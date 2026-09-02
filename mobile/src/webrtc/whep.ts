import {
  RTCPeerConnection as RN_RTC,
  type MediaStream,
  type RTCPeerConnection,
} from "react-native-webrtc";
import { createInputSender } from "../input/inputChannel";
import type { InputSender } from "../input/inputChannel";
import type { IceServer } from "../api/client";

export function waitForIceGatheringComplete(pc: any, capMs = 4000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve, reject) => {
    let done = false;
    let timer: any = null;
    const cleanup = () => {
      if (timer !== null) clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", check);
    };
    const finish = () => {
      if (done) return;
      done = true;
      cleanup();
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === "complete") finish(); };
    const expire = () => {
      if (done) return;
      done = true;
      cleanup();
      reject(whepError("ice-gathering-timeout", "ICE gathering timed out"));
    };
    pc.addEventListener("icegatheringstatechange", check);
    timer = setTimeout(expire, Math.max(0, capMs));
    check();
  });
}

function whepError(code: string, message: string): Error {
  const error = new Error(message);
  (error as any).code = code;
  return error;
}

export type WhepSession = {
  pc: RTCPeerConnection;
  input: InputSender;
  close(): Promise<void>;
};

type ConnectWhepOpts = {
  whepUrl: string;
  whepToken: string;
  iceServers: IceServer[];
  onStream: (stream: MediaStream) => void;
  onInputRtt: (ms: number) => void;
  onState: (state: "connecting" | "connected" | "disconnected") => void;
  fetchImpl?: typeof fetch;
  RTCImpl?: any;
  timeoutMs?: number;
};

export function connectWhep(opts: ConnectWhepOpts): Promise<WhepSession> {
  const RTC = opts.RTCImpl || RN_RTC;
  const doFetch = opts.fetchImpl || fetch;
  const timeoutMs = opts.timeoutMs === undefined ? 8000 : opts.timeoutMs;
  const deadline = Date.now() + timeoutMs;
  const pc: any = new RTC({ iceServers: opts.iceServers || [] });

  let closed = false;
  let resourceUrl: string | null = null;
  let resourceDeleted = false;
  let channel: any = null;
  let input: InputSender;
  let videoStream: MediaStream | null = null;
  let iceReady = pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed";
  let channelReady = false;
  let readyResolve!: (session: WhepSession) => void;
  let readyReject!: (error: Error) => void;
  let timeout: any = null;
  let settled = false;

  const ready = new Promise<WhepSession>((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });

  function listen(target: any, type: string, listener: (e?: any) => void) {
    if (target && typeof target.addEventListener === "function") target.addEventListener(type, listener);
    else if (target) target[`on${type}`] = listener;
  }

  function deleteResource(): Promise<void> {
    if (!resourceUrl || resourceDeleted) return Promise.resolve();
    resourceDeleted = true;
    return Promise.resolve(doFetch(resourceUrl, { method: "DELETE" } as any))
      .then(() => {})
      .catch(() => {});
  }

  async function close(): Promise<void> {
    if (closed) {
      await deleteResource();
      return;
    }
    closed = true;
    if (timeout !== null) clearTimeout(timeout);
    safeState("disconnected");
    if (input) input.close();
    if (channel && typeof channel.close === "function") { try { channel.close(); } catch {} }
    if (pc && typeof pc.close === "function") { try { pc.close(); } catch {} }
    await deleteResource();
  }

  function safeState(state: "connecting" | "connected" | "disconnected") {
    try { opts.onState(state); } catch {}
  }

  function fail(error: Error) {
    if (settled) {
      // Already resolved/adopted: surface as a state transition rather than
      // rejecting a promise nobody is listening to any more.
      if (!closed) { safeState("disconnected"); close(); }
      return;
    }
    settled = true;
    readyReject(error);
    close();
  }

  function checkReady() {
    if (closed || settled) return;
    if (!iceReady || !videoStream || !channelReady) return;
    settled = true;
    if (timeout !== null) clearTimeout(timeout);
    safeState("connected");
    readyResolve({ pc, input, close });
  }

  listen(pc, "track", (event: any) => {
    if (closed) return;
    if (!event.track || event.track.kind !== "video") return;
    const stream = event.streams && event.streams[0];
    if (!stream) return;
    videoStream = stream;
    try { opts.onStream(stream); } catch {}
    checkReady();
  });
  listen(pc, "iceconnectionstatechange", () => {
    if (closed) return;
    const state = pc.iceConnectionState;
    if (state === "connected" || state === "completed") {
      iceReady = true;
      checkReady();
    } else if (state === "failed" || state === "closed") {
      fail(whepError("ice-failed", `ICE connection ${state}`));
    }
  });

  pc.addTransceiver("video", { direction: "recvonly" });
  channel = pc.createDataChannel("input", { ordered: true });
  input = createInputSender(channel);
  channelReady = channel.readyState === "open";
  listen(channel, "open", () => {
    if (closed) return;
    channelReady = true;
    checkReady();
  });
  listen(channel, "message", (event: any) => {
    if (closed) return;
    try {
      const msg = JSON.parse(event.data);
      if (msg && msg.type === "echo" && typeof msg.t === "number") {
        opts.onInputRtt(Date.now() - msg.t);
      }
    } catch {
      // ignore malformed messages
    }
  });
  listen(channel, "close", () => { fail(whepError("input-closed", "Input channel closed")); });
  listen(channel, "error", () => { fail(whepError("input-failed", "Input channel failed")); });

  if (timeoutMs > 0) {
    timeout = setTimeout(() => { fail(whepError("timeout", "WHEP session timed out")); }, timeoutMs);
  }

  safeState("connecting");

  (async () => {
    try {
      const offer = await pc.createOffer();
      if (closed) return;
      await pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(pc, Math.max(0, deadline - Date.now()));
      if (closed) return;
      const response: any = await doFetch(opts.whepUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/sdp",
          "Authorization": `Bearer ${opts.whepToken}`,
        },
        body: pc.localDescription.sdp,
      } as any);
      if (!response || !response.ok) {
        throw whepError("whep-failed", `WHEP POST failed (${response ? response.status : "no response"})`);
      }
      const location = response.headers && (response.headers.get?.("Location") ?? response.headers.get?.("location"));
      if (!location) {
        throw whepError("missing-location", "WHEP response omitted Location");
      }
      try { resourceUrl = new URL(location, opts.whepUrl).toString(); }
      catch { resourceUrl = location; }
      if (closed) {
        // A superseded switch may have closed us while the POST was in
        // flight, before resourceUrl was known. Finish cleanup now instead
        // of leaking the server-side session.
        await deleteResource();
        return;
      }
      const sdp = await response.text();
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch (error) {
      fail(error instanceof Error ? error : whepError("failed", String(error)));
    }
  })();

  return ready;
}
