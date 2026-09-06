import { connectWhep, waitForIceGatheringComplete } from "./whep";
import { connectSignalingViewer } from "./signaling";
import type { SignalingResult } from "./signaling";
import { createInputSender } from "../input/inputChannel";
import type { InputSender } from "../input/inputChannel";
import type { SelectResp } from "../api/client";

export type SessionState = "connecting" | "connected" | "disconnected";

export type EngineSession = {
  kind: "local" | "public";
  stream?: any;
  input: InputSender;
  pc?: any;
  close: () => Promise<void>;
};

export type ConnectEngineSessionOpts = {
  selection: SelectResp;
  authToken?: string | null;
  RTCImpl?: any;
  WebSocketImpl?: any;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  onStream?: (stream: any) => void;
  onInputRtt?: (ms: number) => void;
  onState?: (state: SessionState) => void;
  startLocalImpl?: (opts: ConnectEngineSessionOpts) => Promise<EngineSession>;
  startPublicImpl?: (opts: ConnectEngineSessionOpts) => Promise<EngineSession>;
  connectWhepImpl?: typeof connectWhep;
  connectSignalingViewerImpl?: typeof connectSignalingViewer;
};

async function defaultStartLocal(
  opts: ConnectEngineSessionOpts,
  onStream: (stream: any) => void,
  onInputRtt: (ms: number) => void,
  onState: (state: SessionState) => void
): Promise<EngineSession> {
  const selection = opts.selection;
  if (!selection.whep_url) {
    throw new Error("Local transport not configured");
  }

  let capturedStream: any = null;
  const connectFn = opts.connectWhepImpl || connectWhep;
  const whepSession = await connectFn({
    whepUrl: selection.whep_url,
    whepToken: selection.whep_token || "",
    iceServers: selection.ice_servers || [],
    RTCImpl: opts.RTCImpl,
    fetchImpl: opts.fetchImpl,
    timeoutMs: opts.timeoutMs,
    onStream: (stream: any) => {
      capturedStream = stream;
      onStream(stream);
    },
    onInputRtt,
    onState,
  });

  return {
    kind: "local",
    pc: whepSession.pc,
    input: whepSession.input,
    get stream() {
      return capturedStream;
    },
    close: whepSession.close,
  };
}

async function defaultStartPublic(
  opts: ConnectEngineSessionOpts,
  onStream: (stream: any) => void,
  onInputRtt: (ms: number) => void,
  onState: (state: SessionState) => void
): Promise<EngineSession> {
  const selection = opts.selection;
  if (!selection.signaling_url || !selection.public_session) {
    throw new Error("Public transport not configured");
  }

  const RTC = opts.RTCImpl || (globalThis as any).RTCPeerConnection;
  if (!RTC) {
    throw new Error("RTCPeerConnection implementation not found");
  }

  const timeoutMs = opts.timeoutMs ?? 8000;
  const deadline = Date.now() + timeoutMs;
  const pc: any = new RTC({ iceServers: selection.ice_servers || [] });

  let closed = false;
  let channel: any = null;
  let input: InputSender;
  let videoStream: any = null;
  let iceReady = pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed";
  let channelReady = false;
  let readyResolve!: (session: EngineSession) => void;
  let readyReject!: (error: Error) => void;
  let timeoutTimer: any = null;
  let settled = false;
  let signalingResult: SignalingResult | null = null;

  const ready = new Promise<EngineSession>((resolve, reject) => {
    readyResolve = resolve;
    readyReject = reject;
  });

  function listen(target: any, type: string, listener: (e?: any) => void) {
    if (target && typeof target.addEventListener === "function") target.addEventListener(type, listener);
    else if (target) target[`on${type}`] = listener;
  }

  async function close(): Promise<void> {
    if (closed) return;
    closed = true;
    if (timeoutTimer !== null) clearTimeout(timeoutTimer);
    onState("disconnected");
    if (input) input.close();
    if (channel && typeof channel.close === "function") {
      try {
        channel.close();
      } catch {}
    }
    if (signalingResult && typeof signalingResult.close === "function") {
      try {
        signalingResult.close();
      } catch {}
    }
    if (pc && typeof pc.close === "function") {
      try {
        pc.close();
      } catch {}
    }
  }

  function fail(error: Error) {
    if (settled) {
      if (!closed) {
        onState("disconnected");
        close();
      }
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
    if (timeoutTimer !== null) clearTimeout(timeoutTimer);
    if (signalingResult && typeof signalingResult.close === "function") {
      try {
        signalingResult.close();
      } catch {}
    }
    onState("connected");
    readyResolve({
      kind: "public",
      pc,
      input,
      get stream() {
        return videoStream;
      },
      close,
    });
  }

  listen(pc, "track", (event: any) => {
    if (closed) return;
    if (!event.track || event.track.kind !== "video") return;
    const stream = event.streams && event.streams[0];
    if (!stream) return;
    videoStream = stream;
    onStream(stream);
    checkReady();
  });

  listen(pc, "iceconnectionstatechange", () => {
    if (closed) return;
    const state = pc.iceConnectionState;
    if (state === "connected" || state === "completed") {
      iceReady = true;
      checkReady();
    } else if (state === "failed" || state === "closed") {
      fail(new Error(`ICE connection ${state}`));
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
        onInputRtt(Date.now() - msg.t);
      }
    } catch {}
  });
  listen(channel, "close", () => {
    fail(new Error("Input channel closed"));
  });
  listen(channel, "error", () => {
    fail(new Error("Input channel failed"));
  });

  if (timeoutMs > 0) {
    timeoutTimer = setTimeout(() => {
      fail(new Error("Public session timed out"));
    }, timeoutMs);
  }

  (async () => {
    try {
      const offer = await pc.createOffer();
      if (closed) return;
      await pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(pc, Math.max(0, deadline - Date.now()));
      if (closed) return;

      const connectSignaling = opts.connectSignalingViewerImpl || connectSignalingViewer;
      signalingResult = await connectSignaling({
        signalingUrl: selection.signaling_url!,
        sessionId: selection.public_session!,
        token: opts.authToken || "",
        offerSdp: pc.localDescription.sdp,
        WebSocketImpl: opts.WebSocketImpl,
        timeoutMs: Math.max(0, deadline - Date.now()),
      });
      if (closed) {
        signalingResult.close();
        return;
      }
      await pc.setRemoteDescription({ type: "answer", sdp: signalingResult.answerSdp });
    } catch (err: any) {
      fail(err instanceof Error ? err : new Error(String(err)));
    }
  })();

  return ready;
}

export async function connectEngineSession(opts: ConnectEngineSessionOpts): Promise<EngineSession> {
  const selection = opts.selection;
  const localConfigured = Boolean(selection?.whep_url);
  const publicConfigured = Boolean(selection?.signaling_url && selection?.public_session);

  if (!localConfigured && !publicConfigured) {
    throw new Error("No engine session transport is configured");
  }

  opts.onState?.("connecting");

  let adoptedKind: "local" | "public" | null = null;

  const onAttemptStream = (kind: "local" | "public", stream: any) => {
    if (adoptedKind === kind) {
      opts.onStream?.(stream);
    }
  };

  const onAttemptInputRtt = (kind: "local" | "public", ms: number) => {
    if (adoptedKind === kind) {
      opts.onInputRtt?.(ms);
    }
  };

  const onAttemptState = (kind: "local" | "public", state: SessionState) => {
    if (adoptedKind === kind) {
      opts.onState?.(state);
    }
  };

  const attemptPromises: Promise<EngineSession>[] = [];

  if (localConfigured) {
    const localAttempt = (async () => {
      if (opts.startLocalImpl) {
        return opts.startLocalImpl(opts);
      }
      return defaultStartLocal(
        opts,
        (s) => onAttemptStream("local", s),
        (ms) => onAttemptInputRtt("local", ms),
        (st) => onAttemptState("local", st)
      );
    })();
    attemptPromises.push(localAttempt);
  }

  if (publicConfigured) {
    const publicAttempt = (async () => {
      if (opts.startPublicImpl) {
        return opts.startPublicImpl(opts);
      }
      return defaultStartPublic(
        opts,
        (s) => onAttemptStream("public", s),
        (ms) => onAttemptInputRtt("public", ms),
        (st) => onAttemptState("public", st)
      );
    })();
    attemptPromises.push(publicAttempt);
  }

  return new Promise<EngineSession>((resolve, reject) => {
    let resolved = false;
    let failures = 0;
    const errors: Error[] = [];

    const handleSuccess = async (winner: EngineSession) => {
      if (resolved) {
        // Another transport already won; cleanly close this slower loser
        try {
          await winner.close();
        } catch {}
        return;
      }

      resolved = true;
      adoptedKind = winner.kind;

      if (winner.stream) {
        opts.onStream?.(winner.stream);
      }
      opts.onState?.("connected");

      const session: EngineSession = {
        kind: winner.kind,
        pc: winner.pc,
        input: winner.input,
        get stream() {
          return winner.stream;
        },
        close: async () => {
          adoptedKind = null;
          await winner.close();
        },
      };

      resolve(session);
    };

    const handleFailure = (err: Error) => {
      errors.push(err);
      failures++;
      if (resolved) return;

      if (failures === attemptPromises.length) {
        resolved = true;
        if (attemptPromises.length === 1) {
          reject(err);
        } else {
          const combined = new Error("All engine session attempts failed");
          (combined as any).errors = errors;
          reject(combined);
        }
      }
    };

    for (const p of attemptPromises) {
      p.then(handleSuccess, handleFailure);
    }
  });
}
