import { RTCPeerConnection as RN_RTC } from "react-native-webrtc";
import { waitForIceGatheringComplete } from "./whep";

// Public (off-Tailscale) fallback path: signals over a WebSocket exchanging
// raw SDP text with signaling_bridge.py on the VPS, instead of WHEP's
// one-shot HTTP POST. Ported from src/client/app.js's initWebRTCPublic --
// same negotiation shape (offer -> wait for ICE gathering -> send raw SDP
// -> receive raw SDP answer -> setRemoteDescription), same
// iceconnectionstatechange semantics. Deliberately omits app.js's
// tier-switch retry window and disconnected-watchdog (out of scope for v1):
// onState('failed') on a genuine drop is enough here -- Stream.tsx's
// existing reconnect/error UI takes it from there, same as any other
// connection failure today.
type PublicOpts = {
  signalingUrl: string; instanceName: string; iceServers: RTCIceServer[];
  onStream: (s: any) => void;
  onState: (s: "connecting" | "connected" | "failed") => void;
  WsImpl?: any; RTCImpl?: any;
};

export function connectPublicWhep(opts: PublicOpts) {
  const RTC = opts.RTCImpl || RN_RTC;
  const Ws = opts.WsImpl || WebSocket;
  const pc: any = new RTC({ iceServers: opts.iceServers });
  let closed = false;
  // True once ICE has connected/completed at least once -- mirrors app.js's
  // initWebRTCPublic `settled` flag. Once negotiation has actually
  // succeeded, the signaling WebSocket's job is done (it's closed below,
  // matching app.js's finish() closing `ws` on every terminal path) and any
  // LATER ws.onerror/onclose (a relay restart, the VPS's one-role-slot
  // kicking this connection for a new one, a transient blip on the
  // signaling channel alone -- including the close we ourselves just
  // triggered) must be a no-op. Per signaling_bridge.py's own docstring,
  // media leaves the relay/signaling path entirely once negotiation is
  // done, so a socket-only event past this point does not mean the stream
  // died.
  let negotiated = false;
  const onState = (s: "connecting" | "connected" | "failed") => { if (!closed) opts.onState(s); };
  onState("connecting");

  const onTrack = (e: any) => { opts.onStream(e.streams ? e.streams[0] : e.stream); };
  const onIceChange = () => {
    const s = pc.iceConnectionState;
    if (s === "failed" || s === "closed") onState("failed");
    else if (s === "connected" || s === "completed") {
      if (!negotiated) {
        negotiated = true;
        // Negotiation is done -- stop occupying the VPS's one-role-slot
        // for the rest of the session instead of holding the socket open
        // the whole time.
        try { ws.close(); } catch {}
      }
      onState("connected");
    }
  };
  pc.addEventListener?.("track", onTrack);
  pc.ontrack = onTrack;
  pc.addEventListener?.("iceconnectionstatechange", onIceChange);

  const url = `${opts.signalingUrl}/?session=${encodeURIComponent(opts.instanceName)}&role=viewer`;
  const ws = new Ws(url);

  ws.onopen = async () => {
    if (closed) return;
    try {
      pc.addTransceiver("video", { direction: "recvonly" });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      // 'relay' (not the default 'srflx'): over the public/TURN path the
      // relay candidate is the load-bearing one and typically arrives after
      // srflx (TURN allocation is a slower round-trip) -- resolving on
      // srflx here would send the offer before the candidate that can
      // actually reach a NAT'd PC over signaling_bridge.py's non-trickle
      // protocol ever gets gathered. See whep.ts's waitForIceGatheringComplete.
      await waitForIceGatheringComplete(pc, 4000, "relay");
      if (closed) return;
      ws.send(pc.localDescription.sdp);
    } catch (err) {
      console.log(`[publicWhep] offer setup error`, err);
      onState("failed");
    }
  };

  ws.onmessage = async (event: any) => {
    if (closed) return;
    try {
      await pc.setRemoteDescription({ type: "answer", sdp: event.data });
    } catch (err) {
      console.log(`[publicWhep] setRemoteDescription error`, err);
      onState("failed");
    }
  };

  ws.onerror = () => { if (!negotiated) onState("failed"); };
  ws.onclose = () => { if (!negotiated) onState("failed"); };

  return {
    pc,
    close: () => {
      closed = true;
      pc.removeEventListener?.("track", onTrack);
      pc.removeEventListener?.("iceconnectionstatechange", onIceChange);
      pc.ontrack = null;
      try { ws.close(); } catch {}
      try { pc.close(); } catch {}
    },
  };
}
