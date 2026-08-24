import { RTCPeerConnection as RN_RTC } from "react-native-webrtc";

// Fast path: as soon as a candidate of fastPathType is in, we can send the
// offer. 'srflx' (default) fires fast for the local/Tailscale path -- but
// for the public path (connectPublicWhep), a relay (TURN) candidate is the
// load-bearing one: it typically arrives AFTER the srflx candidate (TURN
// allocation is a slower round-trip than a plain STUN query), so resolving
// on srflx there would send the offer before the one candidate type that
// can actually reach a NAT'd PC ever gets gathered. signaling_bridge.py's
// protocol is non-trickle (single recv/send), so a candidate missing from
// the offer is gone for good -- see publicWhep.ts, which passes 'relay'.
export function waitForIceGatheringComplete(pc: any, capMs = 4000, fastPathType = "srflx"): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      pc.removeEventListener("icegatheringstatechange", check);
      pc.removeEventListener("icecandidate", onCand);
      clearTimeout(capTimer);
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === "complete") finish(); };
    const onCand = (e: any) => {
      if (e.candidate && e.candidate.candidate && e.candidate.candidate.includes(`typ ${fastPathType}`)) finish();
    };
    pc.addEventListener("icegatheringstatechange", check);
    pc.addEventListener("icecandidate", onCand);
    const capTimer = setTimeout(finish, capMs);
  });
}

type Opts = {
  whepUrl: string; stunUrl: string;
  onStream: (s: any) => void;
  onState: (s: "connecting" | "connected" | "failed") => void;
  fetchImpl?: typeof fetch; RTCImpl?: any;
};

export function connectWhep(opts: Opts) {
  const RTC = opts.RTCImpl || RN_RTC;
  const doFetch = opts.fetchImpl || fetch;
  const pc: any = new RTC({ iceServers: opts.stunUrl ? [{ urls: opts.stunUrl }] : [] });
  let closed = false;
  let resourceUrl: string | null = null;
  const onState = (s: "connecting" | "connected" | "failed") => { if (!closed) opts.onState(s); };
  onState("connecting");

  const onTrack = (e: any) => { opts.onStream(e.streams ? e.streams[0] : e.stream); onState("connected"); };
  const onIceChange = () => {
    const s = pc.iceConnectionState;
    if (s === "failed" || s === "closed") onState("failed");
    else if (s === "connected" || s === "completed") onState("connected");
  };
  pc.addEventListener?.("track", onTrack);
  pc.ontrack = onTrack;
  pc.addEventListener?.("iceconnectionstatechange", onIceChange);

  (async () => {
    const t0 = Date.now();
    try {
      pc.addTransceiver("video", { direction: "recvonly" });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      console.log(`[whep] offer set +${Date.now() - t0}ms`);
      // Tailscale has no NAT to traverse, so the STUN reflexive candidate the
      // 4s web-client cap waits for never arrives here — react-native-webrtc's
      // host candidate already carries the real Tailscale IP, which mediamtx
      // can reach directly. A short grace period is enough to let that host
      // candidate land before sending the offer.
      await waitForIceGatheringComplete(pc, 300);
      console.log(`[whep] ice gathering done +${Date.now() - t0}ms (state=${pc.iceGatheringState})`);
      const r = await doFetch(opts.whepUrl, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription.sdp,
      } as any);
      console.log(`[whep] whep POST answered +${Date.now() - t0}ms (ok=${r && (r as any).ok})`);
      if (!r || !(r as any).ok) { onState("failed"); return; }
      // WHEP resource URL for this session (spec: Location header on the POST
      // response). DELETEing it on close tells mediamtx to tear the session
      // down immediately — without this, closing the local peer connection
      // alone leaves a zombie session server-side pushing video into a full
      // write queue until it eventually times out on its own.
      const location = (r as any).headers?.get?.("Location") ?? (r as any).headers?.get?.("location");
      console.log(`[whep] Location header =`, location);
      if (location) {
        try { resourceUrl = new URL(location, opts.whepUrl).toString(); }
        catch { resourceUrl = location; }
      }
      console.log(`[whep] resourceUrl resolved =`, resourceUrl, "closed already?", closed);
      // close() may already have run while this POST was in flight (a
      // superseded instance switch): it couldn't DELETE then because
      // resourceUrl wasn't known yet. Finish the job now instead of leaving
      // this session's write queue stuck open server-side forever.
      if (closed) {
        if (resourceUrl) {
          console.log(`[whep] late DELETE firing ->`, resourceUrl);
          doFetch(resourceUrl, { method: "DELETE" } as any)
            .then((dr: any) => console.log(`[whep] late DELETE result ok=${dr?.ok} status=${dr?.status}`))
            .catch((e) => console.log(`[whep] late DELETE failed`, e));
        } else {
          console.log(`[whep] closed but no resourceUrl — cannot DELETE, session will leak server-side`);
        }
        try { pc.close(); } catch {}
        return;
      }
      const sdp = await (r as any).text();
      await pc.setRemoteDescription({ type: "answer", sdp });
      console.log(`[whep] remote description set +${Date.now() - t0}ms`);
    } catch (err) {
      console.log(`[whep] error +${Date.now() - t0}ms`, err);
      onState("failed");
    }
  })();

  return {
    pc,
    close: () => {
      console.log(`[whep] close() called, resourceUrl=`, resourceUrl, "already closed?", closed);
      closed = true;
      pc.removeEventListener?.("track", onTrack);
      pc.removeEventListener?.("iceconnectionstatechange", onIceChange);
      pc.ontrack = null;
      if (resourceUrl) {
        console.log(`[whep] immediate DELETE firing ->`, resourceUrl);
        doFetch(resourceUrl, { method: "DELETE" } as any)
          .then((dr: any) => console.log(`[whep] immediate DELETE result ok=${dr?.ok} status=${dr?.status}`))
          .catch((e) => console.log(`[whep] immediate DELETE failed`, e));
      } else {
        console.log(`[whep] close() with no resourceUrl yet — deferred to late-DELETE path if POST hasn't resolved`);
      }
      try { pc.close(); } catch {}
    },
  };
}
