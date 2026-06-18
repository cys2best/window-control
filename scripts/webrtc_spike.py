"""
WebRTC spike test with trickle ICE.

Usage:
  1. Edit SERIAL below
  2. uv run python scripts/webrtc_spike.py
  3. Open http://<tailscale-ip>:9000 on iPhone Safari, tap Connect
"""

import asyncio
import fractions
import subprocess
import sys
import threading
import traceback
import uuid

import os
_ADB_CANDIDATES = [
    r"C:\LDPlayer\LDPlayer4.0\adb.exe",
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\LDPlayer\OSLink\bin\adb.exe",
    r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
]
ADB    = next((p for p in _ADB_CANDIDATES if os.path.exists(p)), "adb")
SERIAL = "emulator-5554"
PORT   = 9000

if sys.version_info >= (3, 12):
    try:
        import nest_asyncio; nest_asyncio.apply()
        print("[spike] nest_asyncio applied")
    except ImportError:
        print("[spike] WARNING: install nest-asyncio")

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    import av
    from aiohttp import web
except ImportError as e:
    print(f"IMPORT ERROR: {e}\nRun: uv add aiortc aiohttp"); sys.exit(1)

# ── NAL parser ────────────────────────────────────────────────────────────────
_S4, _S3 = b"\x00\x00\x00\x01", b"\x00\x00\x01"

def _iter_nalus(pipe):
    buf = b""
    count = 0
    while True:
        chunk = pipe.read(65536)
        if not chunk: break
        buf += chunk
        while True:
            i4 = buf.find(_S4, 1); i3 = buf.find(_S3, 1)
            cands = [x for x in [i4, i3] if x > 0]
            if not cands: break
            i = min(cands)
            nalu = buf[4:i] if buf[:4] == _S4 else buf[3:i]
            if nalu:
                count += 1
                if count <= 5 or count % 100 == 0:
                    print(f"[spike] NAL #{count} type={nalu[0]&0x1F} len={len(nalu)}")
                yield nalu
            buf = buf[i:]

# ── Track ─────────────────────────────────────────────────────────────────────
class H264SpikeTrack(VideoStreamTrack):
    kind = "video"
    def __init__(self, pipe, loop):
        super().__init__()
        self._q = asyncio.Queue(maxsize=60)
        self._loop = loop
        self._pts = 0
        threading.Thread(target=self._read, args=(pipe,), daemon=True).start()

    def _read(self, pipe):
        try:
            for nalu in _iter_nalus(pipe):
                asyncio.run_coroutine_threadsafe(self._q.put(nalu), self._loop)
        except Exception:
            print(f"[spike] reader: {traceback.format_exc()[:200]}")

    async def recv(self):
        nalu = await self._q.get()
        pkt = av.Packet(nalu)
        self._pts += int(90000 / 30)
        pkt.pts = pkt.dts = self._pts
        pkt.time_base = fractions.Fraction(1, 90000)
        return pkt

# ── SDP patch ─────────────────────────────────────────────────────────────────
def _patch_sdp(sdp):
    # aiortc produces \r\n — split keeping delimiter to avoid mangling
    lines = sdp.split("\r\n")
    out = []
    for line in lines:
        out.append(line)
        if "H264/90000" in line and "a=rtpmap" in line:
            pt = line.split(":")[1].split(" ")[0]
            if not any(f"a=fmtp:{pt}" in l for l in lines):
                fmtp = f"a=fmtp:{pt} level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f"
                out.append(fmtp)
                print(f"[spike] injected fmtp: {fmtp}")
    result = "\r\n".join(out)
    # Debug: print first 20 lines to verify SDP structure
    print("[spike] SDP answer (first 20 lines):")
    for l in result.split("\r\n")[:20]:
        print(f"  {repr(l)}")
    return result

# ── State: one PC per session ID ──────────────────────────────────────────────
_sessions = {}  # session_id → RTCPeerConnection

# ── Handlers ──────────────────────────────────────────────────────────────────
async def handle_offer(request):
    params = await request.json()
    loop = asyncio.get_event_loop()
    session_id = str(uuid.uuid4())

    proc = subprocess.Popen(
        [ADB, "-s", SERIAL, "exec-out",
         "screenrecord --output-format=h264 --bit-rate=2000000 -"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    print(f"[spike] screenrecord pid={proc.pid}")

    track = H264SpikeTrack(proc.stdout, loop)
    pc = RTCPeerConnection()
    _sessions[session_id] = pc

    @pc.on("iceconnectionstatechange")
    async def _ice():
        print(f"[spike] ICE → {pc.iceConnectionState}")

    @pc.on("icegatheringstatechange")
    def _gather():
        print(f"[spike] gathering → {pc.iceGatheringState}")

    pc.addTrack(track)
    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Don't wait for gathering — return answer immediately and use trickle ICE
    # Client will POST candidates via /ice-candidate as they arrive
    print(f"[spike] answer sent (session={session_id[:8]})")
    return web.json_response({
        "sdp": _patch_sdp(pc.localDescription.sdp),
        "type": pc.localDescription.type,
        "session_id": session_id,
    })

async def handle_ice_candidate(request):
    """Receive trickle ICE candidates from client."""
    params = await request.json()
    session_id = params.get("session_id")
    pc = _sessions.get(session_id)
    if pc is None:
        return web.json_response({"error": "unknown session"}, status=404)
    candidate = params.get("candidate")
    if candidate:
        from aiortc.sdp import candidate_from_sdp
        try:
            # candidate string is like "candidate:xxx udp ..."
            cand_str = candidate.get("candidate", "")
            if cand_str:
                print(f"[spike] remote candidate: {cand_str[:80]}")
                rtc_cand = candidate_from_sdp(cand_str.replace("candidate:", "", 1))
                rtc_cand.sdpMid = candidate.get("sdpMid")
                rtc_cand.sdpMLineIndex = candidate.get("sdpMLineIndex")
                await pc.addIceCandidate(rtc_cand)
        except Exception as e:
            print(f"[spike] addIceCandidate error: {e}")
    else:
        print("[spike] end-of-candidates signal")
    return web.json_response({"ok": True})

async def handle_index(request):
    return web.Response(content_type="text/html", text="""<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WebRTC Spike</title>
  <style>
    body { background:#000; margin:0; display:flex; flex-direction:column; align-items:center; }
    video { width:100vw; max-width:800px; background:#111; min-height:200px; }
    button { margin:12px; padding:12px 32px; font-size:18px; border-radius:8px; }
    #log { color:#0f0; font-family:monospace; font-size:12px; padding:8px;
           white-space:pre-wrap; max-height:300px; overflow-y:auto; width:95%; }
  </style>
</head>
<body>
  <video id="v" autoplay playsinline muted></video>
  <button onclick="connect()">Connect</button>
  <div id="log">Ready.</div>
  <script>
    let _sessionId = null;
    function log(s) {
      const el = document.getElementById('log');
      el.textContent += '\\n' + s;
      el.scrollTop = el.scrollHeight;
      console.log(s);
    }

    async function connect() {
      log('Creating RTCPeerConnection...');
      const pc = new RTCPeerConnection({ iceServers: [] });

      pc.ontrack = e => {
        log('✅ Track received! Setting video src...');
        document.getElementById('v').srcObject = e.streams[0];
      };
      pc.oniceconnectionstatechange = () => log('ICE state: ' + pc.iceConnectionState);
      pc.onicegatheringstatechange  = () => log('Gathering: ' + pc.iceGatheringState);

      // Trickle ICE: send candidates as they arrive
      pc.onicecandidate = async e => {
        if (!_sessionId) return;
        log('Local candidate: ' + (e.candidate ? e.candidate.candidate.slice(0,60) : 'end'));
        await fetch('/ice-candidate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            session_id: _sessionId,
            candidate: e.candidate  // null = end of candidates
          })
        });
      };

      pc.addTransceiver('video', { direction: 'recvonly' });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      log('Sending offer...');

      const r = await fetch('/offer', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ sdp: offer.sdp, type: offer.type })
      });
      const ans = await r.json();
      _sessionId = ans.session_id;
      log('Got answer (session=' + _sessionId.slice(0,8) + '), setting remote desc...');
      await pc.setRemoteDescription({ sdp: ans.sdp, type: ans.type });
      log('Negotiation done — waiting for ICE + video...');
    }
  </script>
</body>
</html>""")

app = web.Application()
app.router.add_get('/', handle_index)
app.router.add_post('/offer', handle_offer)
app.router.add_post('/ice-candidate', handle_ice_candidate)

print(f"[spike] Python {sys.version}")
print(f"[spike] ADB: {ADB}")
print(f"[spike] Serial: {SERIAL}")
print(f"[spike] http://0.0.0.0:{PORT}")
web.run_app(app, port=PORT)
