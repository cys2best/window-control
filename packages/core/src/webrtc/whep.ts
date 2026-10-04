import type { InputSender } from "../input/inputChannel";
import type { IceServer } from "../api/client";
import { connectPeer, peerError, type PeerNegotiator } from "./peer";
export { waitForIceGatheringComplete } from "./peer";

export type WhepSession = {
  pc: any;
  input: InputSender;
  close(): Promise<void>;
};

export type ConnectWhepOpts = {
  whepUrl: string;
  whepToken: string;
  iceServers: IceServer[];
  onStream: (stream: any) => void;
  onInputRtt: (ms: number) => void;
  onState: (state: "connecting" | "connected" | "disconnected") => void;
  RTCImpl: any;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  deadline?: number;
  signal?: AbortSignal;
};

export function connectWhep(opts: ConnectWhepOpts): Promise<WhepSession> {
  const doFetch = opts.fetchImpl || fetch;
  let resourceUrl: string | undefined;
  let canceled = false;
  let deletion: Promise<void> | undefined;
  const remove = (): Promise<void> => {
    if (!resourceUrl) return Promise.resolve();
    if (!deletion) deletion = Promise.resolve().then(() => doFetch(resourceUrl!, { method: "DELETE" })).then(() => {}).catch(() => {});
    return deletion;
  };
  const negotiator: PeerNegotiator = {
    async exchange(offer, _deadline, signal) {
      const response = await doFetch(opts.whepUrl, {
        method: "POST", headers: { "Content-Type": "application/sdp", "Authorization": `Bearer ${opts.whepToken}` }, body: offer, signal,
      });
      if (!response?.ok) throw peerError("whep-failed");
      const location = response.headers?.get?.("Location") ?? response.headers?.get?.("location");
      if (!location) throw peerError("missing-location");
      try { resourceUrl = new URL(location, opts.whepUrl).toString(); } catch { resourceUrl = location; }
      if (canceled || signal.aborted) { void remove(); throw peerError("canceled"); }
      // Location is owned even if reading the answer fails or never finishes.
      try { return { answer: await response.text(), resourceId: resourceUrl! }; }
      catch (error) { void remove(); throw error; }
    },
    close: () => remove(),
    cancel: () => { canceled = true; return remove(); },
  };
  const timeoutMs = opts.timeoutMs ?? 8000;
  const legacyNoTimer = opts.deadline === undefined && timeoutMs <= 0;
  return connectPeer({ ...opts, negotiator, deadline: opts.deadline ?? (legacyNoTimer ? Number.MAX_SAFE_INTEGER : performance.now() + timeoutMs) }, legacyNoTimer ? 0 : undefined);
}
