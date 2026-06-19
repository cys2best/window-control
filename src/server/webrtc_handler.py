"""
WebRTC video streaming handler.

Pipeline:
  ADB screenrecord (Annex B H.264) → AnnexBParser → H264StreamTrack
  → aiortc RTCPeerConnection → WebRTC → Safari <video>

Input (touch/keyboard) stays on WebSocket — DataChannel not used.
MJPEG path kept as fallback if WebRTC negotiation fails.
"""

import asyncio
import sys
import threading
import time
import traceback

# Python 3.12 event loop compatibility with uvicorn
if sys.version_info >= (3, 12):
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass


def _log(msg: str):
    import os
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


# ── Annex B NAL unit parser ───────────────────────────────────────────────────

_START4 = b"\x00\x00\x00\x01"
_START3 = b"\x00\x00\x01"


def _iter_nalus(pipe):
    """Read Annex B H.264 stream from pipe, yield raw NAL unit bytes (no start code)."""
    buf = b""
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            break
        buf += chunk
        while True:
            # Find next start code after position 1
            i4 = buf.find(_START4, 1)
            i3 = buf.find(_START3, 1)
            candidates = [x for x in [i4, i3] if x > 0]
            if not candidates:
                break
            i = min(candidates)
            # Strip leading start code from current NAL
            if buf[:4] == _START4:
                nalu = buf[4:i]
            elif buf[:3] == _START3:
                nalu = buf[3:i]
            else:
                nalu = buf[:i]
            if nalu:
                yield nalu
            buf = buf[i:]


# ── H.264 VideoStreamTrack ────────────────────────────────────────────────────

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    import av
    _AIORTC_AVAILABLE = True
except ImportError:
    _AIORTC_AVAILABLE = False
    _log("[webrtc] aiortc not installed — WebRTC unavailable")


if _AIORTC_AVAILABLE:
    class H264StreamTrack(VideoStreamTrack):
        """Decode ADB H.264 NAL units into VideoFrames for aiortc.

        aiortc recv() must return av.VideoFrame — raw av.Packet doesn't work.
        Decode via av.CodecContext, aiortc re-encodes for WebRTC transport.
        """

        kind = "video"

        def __init__(self, pipe, loop: asyncio.AbstractEventLoop):
            super().__init__()
            self._queue: asyncio.Queue = asyncio.Queue(maxsize=4)
            self._loop = loop
            self._stopped = False
            t = threading.Thread(target=self._read_thread, args=(pipe,), daemon=True)
            t.start()

        def _read_thread(self, pipe):
            codec = av.CodecContext.create("h264", "r")
            try:
                for nalu in _iter_nalus(pipe):
                    if self._stopped:
                        break
                    pkt = av.Packet(b"\x00\x00\x00\x01" + nalu)
                    try:
                        for frame in codec.decode(pkt):
                            if self._queue.full():
                                try:
                                    self._queue.get_nowait()
                                except Exception:
                                    pass
                            asyncio.run_coroutine_threadsafe(
                                self._queue.put(frame), self._loop
                            )
                    except Exception:
                        pass
            except Exception:
                _log(f"[webrtc] NAL reader error: {traceback.format_exc()[:300]}")

        async def recv(self):
            frame = await self._queue.get()
            pts, time_base = await self.next_timestamp()
            frame.pts = pts
            frame.time_base = time_base
            return frame

        def stop(self):
            self._stopped = True
            super().stop()


# ── SDP patching for Safari H.264 compatibility ───────────────────────────────

def _patch_sdp_for_safari(sdp: str) -> str:
    """Ensure H.264 Baseline 3.1 profile in SDP answer — required by Safari."""
    lines = sdp.split("\r\n")
    out = []
    for line in lines:
        out.append(line)
        if "H264/90000" in line and "a=rtpmap" in line:
            pt = line.split(":")[1].split(" ")[0]
            has_fmtp = any(f"a=fmtp:{pt}" in l for l in lines)
            if not has_fmtp:
                out.append(
                    f"a=fmtp:{pt} level-asymmetry-allowed=1;"
                    f"packetization-mode=1;profile-level-id=42e01f"
                )
    return "\r\n".join(out)


# ── WebRTC session and manager ────────────────────────────────────────────────

class WebRTCSession:
    def __init__(self, pc, track, raw_session):
        self.pc = pc
        self.track = track
        self.raw_session = raw_session

    def stop(self):
        try:
            if self.track:
                self.track.stop()
        except Exception:
            pass
        try:
            if self.raw_session:
                self.raw_session.stop()
        except Exception:
            pass


class WebRTCManager:
    def __init__(self):
        self._session: WebRTCSession | None = None
        self._lock = asyncio.Lock()
        # Pre-warmed state from /select — avoids cold-start delay on /webrtc/offer
        self._warm_raw: object | None = None
        self._warm_track: object | None = None
        self._warm_serial: str | None = None
        self._warm_pc: object | None = None

    @property
    def available(self) -> bool:
        return _AIORTC_AVAILABLE

    async def prepare(self, serial: str, w: int, h: int):
        """Pre-start RawH264Session + bind ICE sockets during /select so offer is fast."""
        if not _AIORTC_AVAILABLE:
            return
        async with self._lock:
            # Discard previous warm state
            if self._warm_raw:
                try: self._warm_raw.stop()
                except Exception: pass
            if self._warm_pc:
                try: await self._warm_pc.close()
                except Exception: pass

            from server.adb_manager import RawH264Session
            raw = RawH264Session(serial, w, h)
            if not raw.start():
                self._warm_raw = self._warm_track = self._warm_serial = self._warm_pc = None
                return

            loop = asyncio.get_event_loop()
            track = H264StreamTrack(raw.stdout, loop)

            # Create RTCPeerConnection and do a dummy offer to bind UDP sockets now.
            # When real offer arrives, sockets already bound → setLocalDescription is fast.
            pc = RTCPeerConnection()
            pc.addTrack(track)
            try:
                dummy_offer = await pc.createOffer()
                await pc.setLocalDescription(dummy_offer)
                _log(f"[webrtc] pre-warmed serial={serial} — ICE sockets bound")
            except Exception:
                _log(f"[webrtc] pre-warm ICE bind failed: {traceback.format_exc()[:200]}")

            self._warm_raw = raw
            self._warm_track = track
            self._warm_pc = pc
            self._warm_serial = serial

    async def offer(
        self,
        offer_sdp: str,
        offer_type: str,
        serial: str,
        w: int,
        h: int,
        client_ip: str | None = None,
    ) -> tuple[str, str]:
        """Handle WebRTC offer. Returns (answer_sdp, answer_type)."""
        if not _AIORTC_AVAILABLE:
            raise RuntimeError("aiortc not installed")

        async with self._lock:
            await self._close_session()

            # Use pre-warmed session if serial matches, else start fresh
            if self._warm_raw and self._warm_serial == serial:
                raw = self._warm_raw
                track = self._warm_track
                # Close dummy pc — real negotiation uses a fresh one below
                if self._warm_pc:
                    try: await self._warm_pc.close()
                    except Exception: pass
                self._warm_raw = self._warm_track = self._warm_serial = self._warm_pc = None
                _log(f"[webrtc] using pre-warmed session serial={serial}")
            else:
                from server.adb_manager import RawH264Session
                raw = RawH264Session(serial, w, h)
                if not raw.start():
                    raise RuntimeError("Could not start RawH264Session")
                loop = asyncio.get_event_loop()
                track = H264StreamTrack(raw.stdout, loop)

            pc = RTCPeerConnection()

            @pc.on("iceconnectionstatechange")
            async def _on_ice():
                _log(f"[webrtc] ICE state: {pc.iceConnectionState}")
                if pc.iceConnectionState in ("failed", "closed"):
                    await self._close_session()

            @pc.on("icegatheringstatechange")
            async def _on_gather():
                _log(f"[webrtc] gathering: {pc.iceGatheringState}")

            @pc.on("icecandidate")
            async def _on_local_cand(candidate):
                if candidate:
                    _log(f"[webrtc] local candidate: {str(candidate.candidate)[:80]}")
                else:
                    _log("[webrtc] local gathering complete")

            pc.addTrack(track)
            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=offer_sdp, type=offer_type)
            )
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            # Return answer immediately — trickle ICE via add_ice_candidate()
            patched_sdp = _patch_sdp_for_safari(pc.localDescription.sdp)
            local_cands = [l for l in pc.localDescription.sdp.split("\r\n") if l.startswith("a=candidate")]
            _log(f"[webrtc] local SDP candidates ({len(local_cands)}): {local_cands[:3]}")
            self._session = WebRTCSession(pc, track, raw)
            _log(f"[webrtc] session started serial={serial} client_ip={client_ip}")
            return patched_sdp, pc.localDescription.type

    async def add_ice_candidate(self, candidate: dict):
        """Add a trickle ICE candidate from the client."""
        session = self._session
        if session is None:
            return
        cand_str = candidate.get("candidate", "")
        if not cand_str:
            return  # end-of-candidates signal, ignore
        # Skip mDNS candidates — aiortc cannot resolve *.local hostnames
        if ".local" in cand_str:
            _log(f"[webrtc] skipped mDNS candidate: {cand_str[:80]}")
            return
        try:
            from aiortc.sdp import candidate_from_sdp
            rtc_cand = candidate_from_sdp(cand_str.replace("candidate:", "", 1))
            rtc_cand.sdpMid = candidate.get("sdpMid")
            rtc_cand.sdpMLineIndex = candidate.get("sdpMLineIndex")
            await session.pc.addIceCandidate(rtc_cand)
            _log(f"[webrtc] added remote candidate: {cand_str[:80]}")
        except Exception:
            _log(f"[webrtc] addIceCandidate error: {traceback.format_exc()[:200]}")

    async def close(self):
        async with self._lock:
            await self._close_session()

    async def _close_session(self):
        if self._session:
            s = self._session
            self._session = None
            s.stop()
            try:
                await s.pc.close()
            except Exception:
                pass
            _log("[webrtc] session closed")
