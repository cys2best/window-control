import { connectWhep } from "./whep";
import { connectRemote } from "./remote";
import { peerError } from "./peer";
import type { RemoteApiClient } from "../remote/client";
import type { InputSender } from "../input/inputChannel";
import type { SelectResp, Selection } from "../api/client";

export type SessionState = "connecting" | "connected" | "disconnected";

export type EngineSession = {
  kind: "local" | "remote";
  stream?: any;
  input: InputSender;
  pc?: any;
  close: () => Promise<void>;
};

export type ConnectEngineSessionOpts = {
  selection: Selection | SelectResp;
  client?: RemoteApiClient;
  deadline?: number;
  signal?: AbortSignal;
  RTCImpl?: any;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  onStream?: (stream: any) => void;
  onInputRtt?: (ms: number) => void;
  onState?: (state: SessionState) => void;
  startLocalImpl?: (opts: ConnectEngineSessionOpts) => Promise<EngineSession>;
  connectWhepImpl?: typeof connectWhep;
};

async function defaultStartLocal(
  opts: ConnectEngineSessionOpts,
  onStream: (stream: any) => void,
  onInputRtt: (ms: number) => void,
  onState: (state: SessionState) => void
): Promise<EngineSession> {
  const selection = opts.selection as SelectResp;
  let capturedStream: any = null;
  const connectFn = opts.connectWhepImpl || connectWhep;
  const whepSession = await connectFn({
    whepUrl: selection.whep_url,
    whepToken: selection.whep_token || "",
    iceServers: selection.ice_servers || [],
    RTCImpl: opts.RTCImpl || (globalThis as any).RTCPeerConnection,
    fetchImpl: opts.fetchImpl,
    timeoutMs: opts.timeoutMs,
    deadline: opts.deadline,
    signal: opts.signal,
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

export async function connectEngineSession(opts: ConnectEngineSessionOpts): Promise<EngineSession> {
  const remote = opts.selection && "kind" in opts.selection && opts.selection.kind === "remote" ? opts.selection : null;
  if (!remote && !(opts.selection as SelectResp)?.whep_url) {
    throw new Error("No engine session transport is configured");
  }

  let lastState: SessionState | null = null;
  const safeState = (state: SessionState) => {
    if (lastState === state) return;
    lastState = state;
    opts.onState?.(state);
  };

  safeState("connecting");

  // Until the session is handed to the caller, the transport's own callbacks
  // stay private: the caller sees one "connected" and the adopted stream,
  // not the intermediate events of a negotiation that may still fail.
  let adopted = false;
  const callbacks = {
    onStream: (stream: any) => { if (adopted) opts.onStream?.(stream); },
    onInputRtt: (ms: number) => { if (adopted) opts.onInputRtt?.(ms); },
    onState: (state: SessionState) => { if (adopted) safeState(state); },
  };
  let winner: EngineSession;
  if (remote) {
    if (!opts.client || opts.deadline === undefined) throw new Error("Remote setup requires a client and deadline");
    winner = Object.assign(await connectRemote({ ...callbacks, selection: remote, client: opts.client, deadline: opts.deadline, signal: opts.signal, RTCImpl: opts.RTCImpl || (globalThis as any).RTCPeerConnection }), { kind: "remote" as const });
  } else {
    winner = opts.startLocalImpl ? await opts.startLocalImpl(opts) : await defaultStartLocal(opts, callbacks.onStream, callbacks.onInputRtt, callbacks.onState);
  }
  if (opts.signal?.aborted || (opts.deadline !== undefined && performance.now() >= opts.deadline)) {
    void winner.close().catch(() => {});
    throw peerError(opts.signal?.aborted ? "canceled" : "timeout");
  }
  adopted = true;

  if (winner.stream) opts.onStream?.(winner.stream);
  safeState("connected");

  let closing: Promise<void> | undefined;
  return {
    kind: winner.kind,
    pc: winner.pc,
    input: winner.input,
    get stream() {
      return winner.stream;
    },
    close: () => {
      if (!closing) {
        adopted = false;
        closing = Promise.resolve().then(() => winner.close());
        safeState("disconnected");
      }
      return closing;
    },
  };
}
