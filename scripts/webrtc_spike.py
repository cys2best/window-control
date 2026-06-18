"""
WebRTC spike test — validate aiortc + ADB screenrecord pipeline before full integration.

Usage:
  1. Edit SERIAL below to match your LDPlayer device (run: adb devices)
  2. uv run python scripts/webrtc_spike.py
  3. Open http://<your-tailscale-ip>:9000 on iPhone Safari
  4. Tap "Connect"

Expected: video appears within 3-5 seconds.

Failures and what they mean:
  - ImportError on aiortc/av  → run: uv add aiortc
  - Black video, no error     → SDP profile mismatch; check _patch_sdp_for_safari()
  - "ICE failed"              → network issue; check Tailscale is connected on both devices
  - Python 3.12 RuntimeError  → nest_asyncio not applied; check sys.version_info block
  - No NAL units logged       → ADB path wrong or screenrecord not supported on this emulator
"""

import asyncio
import fractions
import subprocess
import sys
import threading
import traceback

# ── Config ────────────────────────────────────────────────────────────────────
SERIAL = "emulator-5554"   # CHANGE THIS — run `adb devices` to find your serial
PORT = 9000

# Try common LDPlayer adb paths
import os
_ADB_CANDIDATES = [
    r"C:\LDPlayer\LDPlayer4.0\adb.exe",
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\LDPlayer\OSLink\bin\adb.exe",
    r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
]
ADB = next((p for p in _ADB_CANDIDATES if os.path.exists(p)), "adb")

# ── Python 3.12 event loop fix ────────────────────────────────────────────────
if sys.version_info >= (3, 12):
    try:
        import nest_asyncio
        nest_asyncio.apply()
        print("[spike] nest_asyncio applied for Python 3.12")
    except ImportError:
        print("[spike] WARNING: Python 3.12 detected but nest_asyncio not installed")
        print("        Run: uv add nest-asyncio")

# ── Imports ───────────────────────────────────────────────────────────────────
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    import av
    from aiohttp import web
    print("[spike] aiortc + av + aiohttp imports OK")
except ImportError as e:
    print(f"[spike] IMPORT ERROR: {e}")
    print("        Run: uv add aiortc aiohttp")
    sys.exit(1)

# ── NAL parser ────────────────────────────────────────────────────────────────
_START4 = b"\x00\x00\x00\x01"
_START3 = b"\x00\x00\x01"

def _iter_nalus(pipe):
    buf = b""
    count = 0
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            print("[spike] pipe EOF")
            break
        buf += chunk
        while True:
            i4 = buf.find(_START4, 1)
            i3 = buf.find(_START3, 1)
            candidates = [x for x in [i4, i3] if x > 0]
            if not candidates:
                break
            i = min(candidates)
            nalu = buf[4:i] if buf[:4] == _START4 else buf[3:i]
            if nalu:
                count += 1
                if count <= 5 or count % 100 == 0:
                    nalu_type = nalu[0] & 0x1F
                    print(f"[spike] NAL #{count} type={nalu_type} len={len(nalu)}")
                yield nalu
            buf = buf[i:]

# ── VideoStreamTrack ──────────────────────────────────────────────────────────
class H264SpikeTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self, pipe, loop):
        super().__init__()
        self._queue = asyncio.Queue(maxsize=60)
        self._loop = loop
        self._pts = 0
        t = threading.Thread(target=self._read, args=(pipe,), daemon=True)
        t.start()

    def _read(self, pipe):
        try:
            for nalu in _iter_nalus(pipe):
                asyncio.run_coroutine_threadsafe(self._queue.put(nalu), self._loop)
        except Exception:
            print(f"[spike] reader error: {traceback.format_exc()}")

    async def recv(self):
        nalu = await self._queue.get()
        packet = av.Packet(nalu)
        self._pts += int(90000 / 30)
        packet.pts = self._pts
        packet.dts = self._pts
        packet.time_base = fractions.Fraction(1, 90000)
        return packet

# ── SDP patch ────────────────────────────────────────────────────────────────
def _patch_sdp(sdp: str) -> str:
    lines = sdp.splitlines()
    out = []
    for line in lines:
        out.append(line)
        if "H264/90000" in line and "a=rtpmap" in line:
            pt = line.split(":")[1].split(" ")[0]
            has_fmtp = any(f"a=fmtp:{pt}" in l for l in lines)
            if not has_fmtp:
                fmtp = f"a=fmtp:{pt} level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f"
                out.append(fmtp)
                print(f"[spike] injected SDP fmtp: {fmtp}")
    return "\r\n".join(out)

# ── aiohttp handlers ──────────────────────────────────────────────────────────
pcs = set()

async def handle_offer(request):
    params = await request.json()
    loop = asyncio.get_event_loop()

    proc = subprocess.Popen(
        [ADB, "-s", SERIAL, "exec-out",
         "screenrecord --output-format=h264 --bit-rate=2000000 -"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    print(f"[spike] screenrecord started pid={proc.pid}")

    track = H264SpikeTrack(proc.stdout, loop)
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("iceconnectionstatechange")
    async def on_ice():
        print(f"[spike] ICE state: {pc.iceConnectionState}")

    pc.addTrack(track)
    await pc.setRemoteDescription(RTCSessionDescription(
        sdp=params["sdp"], type=params["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    patched = _patch_sdp(pc.localDescription.sdp)
    print("[spike] SDP answer sent")
    return web.json_response({"sdp": patched, "type": pc.localDescription.type})

async def handle_index(request):
    return web.Response(content_type="text/html", text=f"""<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WebRTC Spike</title>
  <style>
    body {{ background:#000; margin:0; display:flex; flex-direction:column; align-items:center; }}
    video {{ width:100vw; max-width:800px; background:#111; }}
    button {{ margin:12px; padding:12px 32px; font-size:18px; border-radius:8px; }}
    #status {{ color:#fff; font-family:monospace; padding:8px; }}
  </style>
</head>
<body>
  <video id="v" autoplay playsinline muted></video>
  <button onclick="connect()">Connect</button>
  <div id="status">Not connected</div>
  <script>
    function log(s) {{ document.getElementById('status').textContent = s; console.log(s); }}
    async function connect() {{
      log('Creating offer...');
      const pc = new RTCPeerConnection({{ iceServers: [] }});
      pc.ontrack = e => {{
        log('Track received — setting video source');
        document.getElementById('v').srcObject = e.streams[0];
      }};
      pc.oniceconnectionstatechange = () => log('ICE: ' + pc.iceConnectionState);
      pc.addTransceiver('video', {{ direction: 'recvonly' }});
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      log('Sending offer to server...');
      const r = await fetch('/offer', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{ sdp: offer.sdp, type: offer.type }})
      }});
      const ans = await r.json();
      log('Got answer, setting remote description...');
      await pc.setRemoteDescription(ans);
      log('WebRTC negotiation complete — waiting for video...');
    }}
  </script>
</body>
</html>""")

# ── Main ──────────────────────────────────────────────────────────────────────
app = web.Application()
app.router.add_get('/', handle_index)
app.router.add_post('/offer', handle_offer)

print(f"[spike] Starting spike server on http://0.0.0.0:{PORT}")
print(f"[spike] ADB path: {ADB}")
print(f"[spike] Device serial: {SERIAL}")
print(f"[spike] Python: {sys.version}")
print(f"[spike] Open http://<tailscale-ip>:{PORT} on iPhone Safari and tap Connect")

web.run_app(app, port=PORT)
