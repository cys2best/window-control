import { RTCPeerConnection as RN_RTC } from "react-native-webrtc";

export function waitForIceGatheringComplete(pc: any, capMs = 4000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      pc.removeEventListener("icegatheringstatechange", check);
      pc.removeEventListener("icecandidate", onCand);
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === "complete") finish(); };
    const onCand = (e: any) => {
      if (e.candidate && e.candidate.candidate && e.candidate.candidate.includes("typ srflx")) finish();
    };
    pc.addEventListener("icegatheringstatechange", check);
    pc.addEventListener("icecandidate", onCand);
    setTimeout(finish, capMs);
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
  opts.onState("connecting");

  pc.addEventListener?.("track", (e: any) => {
    opts.onStream(e.streams ? e.streams[0] : e.stream);
    opts.onState("connected");
  });
  pc.ontrack = (e: any) => { opts.onStream(e.streams ? e.streams[0] : e.stream); opts.onState("connected"); };
  pc.addEventListener?.("iceconnectionstatechange", () => {
    const s = pc.iceConnectionState;
    if (s === "failed" || s === "closed") opts.onState("failed");
    else if (s === "connected" || s === "completed") opts.onState("connected");
  });

  (async () => {
    try {
      pc.addTransceiver("video", { direction: "recvonly" });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(pc);
      const r = await doFetch(opts.whepUrl, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription.sdp,
      } as any);
      if (!r || !(r as any).ok) { opts.onState("failed"); return; }
      const sdp = await (r as any).text();
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch {
      opts.onState("failed");
    }
  })();

  return { pc, close: () => { try { pc.close(); } catch {} } };
}
