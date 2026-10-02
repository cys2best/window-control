import { connectWhep } from "./whep";
import type { InputSender } from "../input/inputChannel";
import type { SelectResp } from "../api/client";

export type SessionState = "connecting" | "connected" | "disconnected";

export type EngineSession = {
  kind: "local";
  stream?: any;
  input: InputSender;
  pc?: any;
  close: () => Promise<void>;
};

export type ConnectEngineSessionOpts = {
  selection: SelectResp;
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
  const selection = opts.selection;
  let capturedStream: any = null;
  const connectFn = opts.connectWhepImpl || connectWhep;
  const whepSession = await connectFn({
    whepUrl: selection.whep_url,
    whepToken: selection.whep_token || "",
    iceServers: selection.ice_servers || [],
    RTCImpl: opts.RTCImpl || (globalThis as any).RTCPeerConnection,
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

export async function connectEngineSession(opts: ConnectEngineSessionOpts): Promise<EngineSession> {
  if (!opts.selection?.whep_url) {
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
  const winner = opts.startLocalImpl
    ? await opts.startLocalImpl(opts)
    : await defaultStartLocal(
        opts,
        (stream) => { if (adopted) opts.onStream?.(stream); },
        (ms) => { if (adopted) opts.onInputRtt?.(ms); },
        (state) => { if (adopted) safeState(state); }
      );
  adopted = true;

  if (winner.stream) opts.onStream?.(winner.stream);
  safeState("connected");

  return {
    kind: "local",
    pc: winner.pc,
    input: winner.input,
    get stream() {
      return winner.stream;
    },
    close: async () => {
      try {
        await winner.close();
      } finally {
        safeState("disconnected");
        adopted = false;
      }
    },
  };
}
