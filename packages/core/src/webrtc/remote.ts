import { connectPeer, peerError, type ConnectPeerOpts, type PeerNegotiator } from "./peer";
import type { RemoteSelection } from "../remote/protocol";
import type { RemoteApiClient } from "../remote/client";

export type ConnectRemoteOpts = Omit<ConnectPeerOpts, "iceServers" | "negotiator"> & { selection: RemoteSelection; client: RemoteApiClient };
export function connectRemote({ selection, client, ...peerOptions }: ConnectRemoteOpts) {
  let closing: Promise<void> | undefined;
  const close = () => closing ??= Promise.resolve().then(() => client.closeSession(selection));
  const negotiator: PeerNegotiator = {
    async exchange(offer, deadline, signal) {
      const reply = await client.negotiate(selection, offer, { deadline, signal });
      if (reply.session_id !== selection.session_id || reply.generation !== selection.generation) throw Object.assign(new Error("stale_generation"), { code: "stale_generation" });
      return { answer: reply.answer, resourceId: selection.session_id };
    },
    async close(resourceId) {
      if (resourceId !== selection.session_id) throw new Error("stale_generation");
      await close();
    },
    cancel: close,
  };
  return connectPeer({ ...peerOptions, iceServers: selection.ice_servers, negotiator }).catch(error => {
    if (!selection.relay_available && error?.code === "ice-failed") throw peerError("relay_unavailable");
    throw error;
  });
}
