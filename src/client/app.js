// app.js — stream display, touch→input, reconnect, stats

let ws = null;
let wsRetryDelay = 1000;

// WebRTC state
let _pc = null;
let _webrtcActive = false;
let _activeWindowId = null;
let _whepUrl = null;           // mediamtx WHEP endpoint for active instance
let _stunUrl = null;           // STUN server bound to Tailscale IP (from server)
let _webrtcInProgress = false; // prevent concurrent initWebRTC calls
let _currentSerial = null;     // serial of active instance (for adaptive quality)
let _signalingUrl = null;      // VPS signaling relay URL for the active public session
let _instanceName = null;      // instance name (rendezvous key) for the active public session
let _iceServers = [];          // STUN/TURN servers for the public path (from server, see ice_config.py)
let _publicModeActive = false; // true when the active stream was negotiated via initWebRTCPublic
let _raceGen = 0;              // bumped by initWebRTC/initWebRTCPublic/initWebRTCRace on every call,
                                // so a stale initWebRTCRace() can detect it's been superseded (see below)
let _streamFailCount = 0;      // consecutive MJPEG load failures for the current _streamGeneration --
                                // caps the retry loop so a permanently-unsupported path (e.g. /stream
                                // over the public tunnel, which always 501s) can't retry forever

// ── Quality tiers ──────────────────────────────────────────────────────────────
const _TIER_ORDER = ["480", "720", "1080", "1440"];
// Preferred quality, persisted. "auto" = let the server's default (720) stand
// and only adapt DOWN on congestion — this is the safe default: it does NOT
// POST a tier change on connect, so it can't collide with the just-started
// scrcpy session (a tier POST during connect restarts scrcpy and races the
// initial start → truncated handshake → black screen). A specific tier
// (480/720/1080/1440) is a deliberate pin the user chose.
let _preferredTier = localStorage.getItem('wc_tier') || "auto";
let _currentTier = "720";       // server session's starting tier
let _tierManualUntil = 0;       // ms epoch; manual pin suspends auto-downgrade
let _adaptiveTimer = null;
let _badStreak = 0;             // consecutive congested samples (for step-down debounce)
let _lastTierChange = 0;
// ms epoch until which a tier change is "expected". A tier change restarts
// scrcpy server-side (resolution is baked into the encoder args), so the RTSP
// publish drops and republishes on the SAME mediamtx path. That transient can
// bounce ICE to 'disconnected'/'failed'. Inside this window we must NOT tear
// down the PeerConnection and re-negotiate WHEP: the same PC recovers on the
// republish while the browser holds the last decoded frame (a frozen frame, not
// a black screen), and a full renegotiation would blank the video and cost the
// whole ICE round-trip again — the exact 5s freeze we're removing.
let _tierSwitchUntil = 0;
let _adaptiveSerial = null;

// Apply a quality the user picked. "auto" stops pinning (adaptation resumes);
// a tier value pins it, persists, and POSTs — but NOT during the connect window
// (the caller gates that). Never fires on the initial connect.
async function setPreferredTier(tier) {
  if (tier !== "auto" && !_TIER_ORDER.includes(tier)) return;
  _preferredTier = tier;
  localStorage.setItem('wc_tier', tier);
  _updateQualityLabel();
  if (tier === "auto") { _tierManualUntil = 0; return; }
  _tierManualUntil = Date.now() + 60000;  // user pin wins over auto for 60s
  _currentTier = tier;
  _lastTierChange = Date.now();
  _tierSwitchUntil = Date.now() + 8000;   // expect a restart bounce; don't re-negotiate
  if (_adaptiveSerial) {
    try {
      await fetch(`/instances/${_adaptiveSerial}/quality`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tier }),
      });
    } catch (_) {}
  }
}

function _updateQualityLabel() {
  const btn = document.getElementById('quality-btn');
  if (!btn) return;
  btn.textContent = _preferredTier === "auto" ? "AUTO" :
    _preferredTier === "480" ? "SD" :
    _preferredTier === "720" ? "HD" : _preferredTier + "p";
}

function _stepTier(dir) {
  const i = _TIER_ORDER.indexOf(_currentTier);
  const j = Math.max(0, Math.min(_TIER_ORDER.length - 1, i + dir));
  return _TIER_ORDER[j];
}

async function _applyTier(tier) {
  if (tier === _currentTier || !_adaptiveSerial) return;
  _currentTier = tier;
  _lastTierChange = Date.now();
  _tierSwitchUntil = Date.now() + 8000;   // expect a restart bounce; don't re-negotiate
  try {
    await fetch(`/instances/${_adaptiveSerial}/quality`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tier }),
    });
  } catch (_) {}
}

async function _sampleAndAdapt() {
  if (!_pc || Date.now() < _tierManualUntil) return;
  if (Date.now() - _lastTierChange < 10000) return;  // cooldown
  let loss = 0, rtt = 0, seen = false;
  const stats = await _pc.getStats();
  stats.forEach(r => {
    if (r.type === 'inbound-rtp' && r.kind === 'video') {
      const recv = r.packetsReceived || 0, lost = r.packetsLost || 0;
      if (recv + lost > 0) loss = lost / (recv + lost);
      seen = true;
    }
    if (r.type === 'candidate-pair' && r.state === 'succeeded' && r.currentRoundTripTime != null) {
      rtt = r.currentRoundTripTime * 1000;  // → ms
    }
  });
  if (!seen) return;
  // Downgrade-only adaptation. Each tier change RESTARTS scrcpy (resolution is
  // set at the encoder), which drops the stream for ~2s and re-negotiates the
  // 'active' source. Auto-UPGRADE on a healthy link therefore caused a restart
  // storm — the stream stepped 720→1080→1280→… every ~15s and never settled,
  // making game control unusable. So we only ever step DOWN, and only under
  // real, sustained congestion. Upgrades are a deliberate manual action.
  if (loss > 0.08 || rtt > 400) {
    _badStreak += 1;
    if (_badStreak >= 3) {   // ~15s sustained before we pay a restart
      _badStreak = 0;
      await _applyTier(_stepTier(-1));
    }
  } else {
    _badStreak = 0;
  }
}

function startAdaptiveQuality(serial) {
  _adaptiveSerial = serial;
  _badStreak = 0;
  stopAdaptiveQuality();
  // If the user PINNED a tier other than the server default, apply it — but not
  // during the connect window. A tier POST restarts scrcpy; doing that while the
  // just-started session is still coming up races two starts onto the same port
  // (truncated handshake → black screen). Wait 4s so the initial stream is live
  // and its stream thread has settled before we trigger a restart.
  if (_preferredTier !== "auto" && _preferredTier !== "720") {
    const target = _adaptiveSerial;
    setTimeout(() => {
      if (_adaptiveSerial === target) _applyPreferredTierToServer();
    }, 4000);
  }
  _adaptiveTimer = setInterval(_sampleAndAdapt, 5000);
}

function _applyPreferredTierToServer() {
  if (!_adaptiveSerial || _preferredTier === "auto") return;
  _currentTier = _preferredTier;
  _tierManualUntil = Date.now() + 60000;
  fetch(`/instances/${_adaptiveSerial}/quality`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tier: _preferredTier }),
  }).catch(() => {});
}

function stopAdaptiveQuality() {
  if (_adaptiveTimer) { clearInterval(_adaptiveTimer); _adaptiveTimer = null; }
}

// Retarget adaptive-quality + reconnect to a new instance serial on switch.
function setAdaptiveSerial(serial) {
  _currentSerial = serial;
  _adaptiveSerial = serial;
  _currentTier = "720";  // fresh server session starts here
  _badStreak = 0;
}

// Drag state
let _dragActive = false;
let _dragStartX = 0;
let _dragStartY = 0;
let _dragMoved = false;
let _lastDragSendTime = 0;   // throttle drag_move sends
let _lastScrollSendTime = 0; // suppress drag_end after scroll

// Two-finger scroll state
let _twoFingerLastY = null;

let _inputRttMs = 0;      // measured input-WS round-trip (client→server→client)
let _echoTimer = null;

// Decode-health signal → client-driven IDR repair. The scrcpy-side heartbeat
// backed off from 2s to 8s (see project_copy_mux_idr) to cut bitrate tax, so
// on real loss (PLI/freeze/dropped frames) we now ask for a fresh keyframe
// over /input instead of waiting up to 8s for the next heartbeat tick.
let _idrPrev = { pli: 0, freeze: 0, dropped: 0 };
let _idrLastSent = 0;
let _decodeHealthTimer = null;
const FRAME_DROP_THRESHOLD = 5; // framesDropped delta per poll before treating as decode trouble
// A single freeze per poll happens naturally on idle/low-motion screens (the
// device encoder emits frames sparsely, so the jitter buffer's wait for the
// next one can itself register as one freeze tick) -- confirmed in practice:
// an idle static screen produced ~1 freeze every ~7s with no real playback
// problem, which drove near-continuous idr requests and defeated Task 1.4's
// heartbeat backoff (2s -> 8s, done specifically to cut IDR bitrate tax).
// Require multiple freezes in the same 1s poll window before treating it as
// real decode trouble. pliCount is left unthresholded: it's an explicit
// "decoder needs a keyframe" signal from the browser itself, not a passive
// timing artifact, so any PLI should still repair immediately.
const FREEZE_THRESHOLD = 2; // freezeCount delta per poll before treating as decode trouble

async function pollDecodeHealth(pc, sock) {
  if (!pc || pc.connectionState !== 'connected') return;
  const stats = await pc.getStats();
  stats.forEach(r => {
    if (r.type !== 'inbound-rtp' || r.kind !== 'video') return;
    const d = {
      pli:     (r.pliCount     ?? 0) - _idrPrev.pli,
      freeze:  (r.freezeCount  ?? 0) - _idrPrev.freeze,
      dropped: (r.framesDropped ?? 0) - _idrPrev.dropped,
    };
    _idrPrev = {
      pli: r.pliCount ?? 0,
      freeze: r.freezeCount ?? 0,
      dropped: r.framesDropped ?? 0,
    };
    const now = performance.now();
    if ((d.pli > 0 || d.freeze >= FREEZE_THRESHOLD || d.dropped > FRAME_DROP_THRESHOLD)
        && now - _idrLastSent > 1000 && sock && sock.readyState === WebSocket.OPEN) {
      _idrLastSent = now;
      sock.send(JSON.stringify({ type: 'idr' }));
    }
  });
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/input`);
  ws.onopen = () => {
    wsRetryDelay = 1000;
    // Probe input-WS round-trip every 2s. This isolates input transport
    // latency from video-feedback latency: if this stays low (~RTT) while taps
    // still feel late, the delay is video, not input.
    if (_echoTimer) clearInterval(_echoTimer);
    _echoTimer = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'echo', t: Date.now() }));
      }
    }, 2000);
    if (_decodeHealthTimer) clearInterval(_decodeHealthTimer);
    _decodeHealthTimer = setInterval(() => pollDecodeHealth(_pc, ws), 1000);
  };
  ws.onmessage = ev => {
    try {
      const m = JSON.parse(ev.data);
      if (m.type === 'echo' && m.t) _inputRttMs = Date.now() - m.t;
    } catch (_) {}
  };
  ws.onclose = () => {
    if (_echoTimer) { clearInterval(_echoTimer); _echoTimer = null; }
    if (_decodeHealthTimer) { clearInterval(_decodeHealthTimer); _decodeHealthTimer = null; }
    scheduleWSReconnect();
  };
  ws.onerror = () => ws.close();
}

function scheduleWSReconnect() {
  setTimeout(() => connectWS(), wsRetryDelay);
  wsRetryDelay = Math.min(wsRetryDelay * 2, 30000);
  setNetStatus('bad', 'Reconnecting…');
}

// Network status dot: 'good' (green), 'warn' (yellow), 'bad' (red).
function setNetStatus(state, label) {
  const el = document.getElementById('net-status');
  if (!el) return;
  el.classList.remove('net-good', 'net-warn', 'net-bad');
  el.classList.add('net-' + state);
  if (label) el.title = label;
}

function sendInput(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

let _streamGeneration = 0;

const MAX_STREAM_RETRIES = 3;

function initStream() {
  clearInterval(window._streamPoll);
  _streamGeneration++;
  _streamFailCount = 0;
  _loadStreamFrame(_streamGeneration);
}

function _loadStreamFrame(gen) {
  // Remove and recreate img to force browser to drop the TCP connection.
  // Setting img.src='' is not enough — Safari keeps the socket open.
  const oldImg = document.getElementById('stream-img');
  const newImg = document.createElement('img');
  newImg.id = 'stream-img';
  newImg.className = oldImg.className;
  newImg.style.cssText = oldImg.style.cssText;
  oldImg.replaceWith(newImg);

  newImg.src = '/stream?' + Date.now();

  // MJPEG img onload fires once on first frame only — not per-frame
  newImg.onload = () => { if (gen === _streamGeneration) { _streamFailCount = 0; clearUnavailable(); } };

  newImg.onerror = () => {
    if (gen !== _streamGeneration) return;
    clearInterval(window._streamPoll);
    _streamFailCount++;
    // Some paths (e.g. this browser is off-LAN, reachable only through the
    // public tunnel) will NEVER serve MJPEG -- the tunnel refuses /stream
    // outright (http_tunnel.py, unbounded body) on every single attempt.
    // Retrying forever there just hammers the tunnel and spams its log.
    // Give up after a few tries and surface the manual-reconnect overlay
    // instead of retrying silently forever.
    if (_streamFailCount >= MAX_STREAM_RETRIES) {
      showUnavailable();
      return;
    }
    setTimeout(() => { if (gen === _streamGeneration) _loadStreamFrame(gen); }, 2000);
  };

  // Staleness: if img stops loading (server died), onerror fires.
  // Don't poll naturalWidth — it doesn't change per-frame for MJPEG.
  // Lock polling handles reinit on lock→unlock. No stale-reinit needed here.
}

function clearUnavailable() {
  document.getElementById('unavailable-overlay').classList.remove('show');
}

function showUnavailable() {
  document.getElementById('unavailable-overlay').classList.add('show');
}

// Shared by the reconnect button and the visibilitychange resume path.
// Retries whichever transport already proved it works for this session, or
// -- if we're here because initWebRTCRace fell all the way to MJPEG, which
// then hit the tunnel's permanent /stream refusal -- races both WebRTC paths
// again instead of retrying MJPEG, the one path already proven dead here.
function _reconnectStream() {
  if (_publicModeActive && _activeWindowId && _signalingUrl && _instanceName) {
    initWebRTCPublic(_activeWindowId, _signalingUrl, _instanceName, _currentSerial, _iceServers);
  } else if (_webrtcActive && _activeWindowId) {
    initWebRTC(_activeWindowId, _whepUrl, undefined, _currentSerial);
  } else if (_activeWindowId) {
    initWebRTCRace(_activeWindowId, _whepUrl, _stunUrl, _signalingUrl, _instanceName, _currentSerial, _iceServers);
  } else {
    initStream();
  }
}

function getStreamRect() {
  // Use whichever stream element is currently visible
  const video = document.getElementById('stream-video');
  if (_webrtcActive && video.style.display !== 'none') {
    return video.getBoundingClientRect();
  }
  return document.getElementById('stream-img').getBoundingClientRect();
}

function _activeStreamEl() {
  const video = document.getElementById('stream-video');
  if (_webrtcActive && video.style.display !== 'none') return video;
  return document.getElementById('stream-img');
}

// ── WebRTC via WHEP (mediamtx) ────────────────────────────────────────────────

function _fallbackToMJPEG() {
  _webrtcActive = false;
  _publicModeActive = false;
  stopAdaptiveQuality();
  if (_pc) { try { _pc.close(); } catch(_) {} _pc = null; }
  document.getElementById('stream-video').style.display = 'none';
  document.getElementById('stream-img').style.display = 'block';
  initStream();
}

// Resolve once ICE gathering completes (or after a short cap, so a stuck
// gatherer can't hang the whole negotiation).
// Wait for ICE gathering before POSTing the non-trickle WHEP offer. The offer
// MUST carry a srflx (server-reflexive) candidate — that's the one our embedded
// STUN reflects with the browser's Tailscale IP, the only candidate mediamtx
// can reach. Safari's mDNS .local host candidate is useless over Tailscale.
//
// Old behavior blind-capped at 2s: if the srflx arrived late, the offer went
// out with only the .local candidate → mediamtx "write queue is full" →
// never connects. Now: resolve as soon as we have a srflx (fast path), else
// on 'complete', else a longer hard cap so a slow gather still gets its srflx.
function waitForIceGatheringComplete(pc, capMs = 4000, fastPathType = 'srflx') {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise(resolve => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      pc.removeEventListener('icegatheringstatechange', check);
      pc.removeEventListener('icecandidate', onCand);
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === 'complete') finish(); };
    // Fast path: as soon as a candidate of fastPathType is in, we can POST.
    // 'srflx' (default) fires fast for the local/Tailscale path -- but for
    // the public path (initWebRTCPublic), a relay (TURN) candidate is the
    // load-bearing one: it typically arrives AFTER the srflx candidate
    // (TURN allocation is a slower round-trip than a plain STUN query), so
    // resolving on srflx there would POST the offer before the one
    // candidate type that can actually reach a NAT'd PC ever gets gathered.
    // Non-trickle ICE means a candidate missing from this offer is gone
    // for good. If TURN isn't configured, no 'relay' candidate ever
    // arrives and this correctly falls through to 'complete' or capMs.
    const onCand = e => {
      if (e.candidate && e.candidate.candidate &&
          e.candidate.candidate.includes(`typ ${fastPathType}`)) {
        finish();
      }
    };
    pc.addEventListener('icegatheringstatechange', check);
    pc.addEventListener('icecandidate', onCand);
    setTimeout(finish, capMs);
  });
}

async function initWebRTC(windowId, whepUrl, stunUrl, serial) {
  // Cancel any in-flight negotiation
  _raceGen++; // invalidate any in-flight initWebRTCRace() so it can't clobber us on completion
  if (_pc) { try { _pc.close(); } catch(_) {} _pc = null; }
  if (_webrtcInProgress) {
    _webrtcInProgress = false;
    await new Promise(r => setTimeout(r, 50));
  }
  _webrtcInProgress = true;
  setNetStatus('warn', 'Connecting…');
  _activeWindowId = windowId;
  _whepUrl = whepUrl || _whepUrl;
  _stunUrl = stunUrl || _stunUrl;
  _currentSerial = serial || _currentSerial;
  _publicModeActive = false; // entering (or staying in) local WHEP mode

  if (!_whepUrl) { _fallbackToMJPEG(); _webrtcInProgress = false; return; }

  try {
    // STUN is required: Safari only emits an mDNS (.local) host candidate for
    // privacy and offers no flag to disable it. mediamtx can't resolve .local
    // over Tailscale, so the pair never forms and media never flows
    // ("write queue is full" -> "deadline exceeded").
    //
    // A *public* STUN (Google) doesn't help either: the query exits via the
    // public internet, so the srflx candidate reflects the ISP/WARP public IP
    // (104.x), which mediamtx at 100.x can't reach. We instead use our own STUN
    // bound to the Tailscale IP (server sends stun_url) — the query routes over
    // Tailscale, so the srflx candidate carries the browser's Tailscale IP,
    // which mediamtx can reach directly. We wait for ICE gathering to complete
    // before POSTing the (non-trickle) offer so that candidate is included.
    console.log('[webrtc] stun_url =', _stunUrl || '(none!)');
    _pc = new RTCPeerConnection({
      iceServers: _stunUrl ? [{ urls: _stunUrl }] : [],
    });
    _idrPrev = { pli: 0, freeze: 0, dropped: 0 };

    const video = document.getElementById('stream-video');
    const img   = document.getElementById('stream-img');

    _pc.ontrack = e => {
      console.log('[webrtc] ontrack fired');
      try {
        if (e.receiver && 'playoutDelayHint' in e.receiver) e.receiver.playoutDelayHint = 0;
      } catch (_) {}
      video.srcObject = e.streams[0];
      video.onloadedmetadata = () => console.log('[webrtc] video loadedmetadata');
      video.oncanplay = () => console.log('[webrtc] video canplay');
      video.onplaying = () => console.log('[webrtc] video playing');
      video.style.display = 'block';
      img.style.display = 'none';
      _webrtcActive = true;
      setNetStatus('good', 'Connected');
      startAdaptiveQuality(_currentSerial);
      clearUnavailable();
    };

    _pc.onicecandidate = e => {
      if (e.candidate) console.log('[ice] local candidate:', e.candidate.candidate);
      else console.log('[ice] local gathering complete');
    };

    _pc.oniceconnectionstatechange = () => {
      const s = _pc ? _pc.iceConnectionState : '';
      console.log('[ice] state:', s);
      if (s === 'connected' || s === 'completed') setNetStatus('good', 'Connected');
      else if (s === 'checking' || s === 'new') setNetStatus('warn', 'Connecting…');
      else if (s === 'disconnected') setNetStatus('warn', 'Unstable');
      else if (s === 'failed' || s === 'closed') setNetStatus('bad', 'Disconnected');
      if (s === 'failed' || s === 'closed') {
        const retryId = _activeWindowId;
        const retryPc = _pc;
        // During a tier switch the scrcpy restart drops and republishes the RTSP
        // source on the same mediamtx path, which can bounce ICE to 'failed'
        // transiently. Wait out the switch window before tearing down: the PC
        // usually recovers on the republish with the last frame held, and a
        // reconnect only fires if it's still broken after the restart settles.
        const inSwitch = Date.now() < _tierSwitchUntil;
        const delay = inSwitch ? (_tierSwitchUntil - Date.now()) + 1500 : 2000;
        setTimeout(() => {
          // Recovered on its own during the wait — no renegotiation needed.
          if (_pc === retryPc && (_pc.iceConnectionState === 'connected' ||
                                  _pc.iceConnectionState === 'completed')) {
            return;
          }
          if (_activeWindowId === retryId && _pc === retryPc && !_webrtcInProgress) {
            initWebRTC(retryId, undefined, undefined, _currentSerial);
          }
        }, delay);
      } else if (s === 'disconnected') {
        // mediamtx tearing down its side (e.g. the scrcpy restart during a
        // tier switch destroying its PeerConnection) doesn't always produce
        // a clean 'failed' on the browser side -- some closes leave ICE
        // sitting in 'disconnected' indefinitely instead. A real transient
        // network blip usually self-heals within a few seconds; give it a
        // window, then treat a non-recovery the same as a hard failure.
        const retryId = _activeWindowId;
        const watchdogPc = _pc;
        setTimeout(() => {
          if (_pc !== watchdogPc) return; // superseded already
          const cur = _pc.iceConnectionState;
          if (cur === 'connected' || cur === 'completed') return;
          if (_activeWindowId === retryId && _pc === watchdogPc && !_webrtcInProgress) {
            initWebRTC(retryId, undefined, undefined, _currentSerial);
          }
        }, 5000);
      }
    };

    const thisPc = _pc;
    const videoTx = _pc.addTransceiver('video', { direction: 'recvonly' });
    // Ask WebRTC to keep the receiver jitter buffer as small as possible. The
    // default buffer adds ~100-300ms of latency (its job is smoothness, not
    // interactivity); for remote control we want the freshest frame. Supported
    // on Chromium; Safari ignores it harmlessly.
    try {
      if (videoTx.receiver && 'playoutDelayHint' in videoTx.receiver) {
        videoTx.receiver.playoutDelayHint = 0;
      }
    } catch (_) {}

    const offer = await thisPc.createOffer();
    if (_pc !== thisPc) return;
    await thisPc.setLocalDescription(offer);
    if (_pc !== thisPc) return;

    // WHEP here is non-trickle: the answer is a one-shot HTTP response, so the
    // offer must carry the full candidate list. Wait for ICE gathering to
    // finish before POSTing, otherwise the offer has no candidates and no pair
    // is ever formed (media never flows -> "write queue is full").
    await waitForIceGatheringComplete(thisPc);
    if (_pc !== thisPc) return;

    const r = await fetch(_whepUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: thisPc.localDescription.sdp,
    });
    if (_pc !== thisPc) return;
    if (!r || !r.ok) { _fallbackToMJPEG(); return; }

    const answerSdp = await r.text();
    if (_pc !== thisPc) return;
    await thisPc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
  } catch (err) {
    console.error('[webrtc] initWebRTC error, falling back to MJPEG:', err);
    _fallbackToMJPEG();
  } finally {
    _webrtcInProgress = false;
  }
}

async function initWebRTCPublic(windowId, signalingUrl, instanceName, serial, iceServers) {
  // Structurally parallel to initWebRTC() above -- same _pc lifecycle,
  // same ontrack/oniceconnectionstatechange handlers, same in-flight-
  // negotiation race guards (if (_pc !== thisPc) return). Differs only in
  // negotiation transport: WS + raw SDP text instead of one-shot HTTP POST,
  // matching signaling_bridge.py's relay_one_instance() protocol exactly
  // (proven end-to-end against the real VPS + mediamtx during manual
  // testing) -- {signalingUrl}/?session={instanceName}&role=viewer, no
  // JSON envelope, raw SDP text both directions.
  _raceGen++; // invalidate any in-flight initWebRTCRace() so it can't clobber us on completion
  if (_pc) { try { _pc.close(); } catch(_) {} _pc = null; }
  if (_webrtcInProgress) {
    _webrtcInProgress = false;
    await new Promise(r => setTimeout(r, 50));
  }
  _webrtcInProgress = true;
  setNetStatus('warn', 'Connecting (public)…');
  _activeWindowId = windowId;
  _currentSerial = serial || _currentSerial;
  // Persist for the visibility-resume path, which calls this function again
  // without re-fetching /select (see the visibilitychange handler below).
  _iceServers = iceServers || _iceServers;

  return new Promise((resolve) => {
    let settled = false;
    let ws = null; // hoisted so finish() can close it on every terminal path
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      _webrtcInProgress = false;
      try { ws.close(); } catch(_) {}
      resolve(ok);
    };

    try {
      // A NAT'd PC has no publicly reachable ICE candidate on its own --
      // without a TURN relay here, ICE fails after signaling succeeds and
      // the caller silently falls back to local WHEP (unreachable off the
      // PC's LAN/Tailscale). _iceServers comes from the server's
      // get_ice_servers() (STUN always, TURN when TURN_HOST is configured).
      _pc = new RTCPeerConnection({ iceServers: _iceServers });
      _idrPrev = { pli: 0, freeze: 0, dropped: 0 };
      const thisPc = _pc;

      const video = document.getElementById('stream-video');
      const img = document.getElementById('stream-img');

      _pc.ontrack = e => {
        // Attach the media stream only -- do NOT signal success here.
        // ontrack fires at SDP-processing time, before ICE has actually
        // connected; resolving finish(true) here means a viewer whose ICE
        // never connects gets a permanent black screen with no fallback
        // (see finding 3). Success is signalled from
        // oniceconnectionstatechange below, once ICE actually reports
        // connected/completed.
        video.srcObject = e.streams[0];
        video.style.display = 'block';
        img.style.display = 'none';
      };

      // Shared by the 'failed'/'closed' branch and the 'disconnected'
      // watchdog below: wait out a tier-switch window and retry the SAME
      // public path, or fall back to local WHEP if we weren't mid-switch.
      // A quality-tier change restarts scrcpy, which bounces ICE
      // transiently on the underlying media source regardless of which
      // path negotiated it -- retrying the same path (not jumping to
      // local) matters because a user actually on the public path is
      // very likely off Tailscale/LAN in the first place, so local WHEP
      // is unreachable and that jump previously left the stream stuck.
      const retryPublicOrFallbackLocal = () => {
        if (!(_pc === thisPc && _activeWindowId === windowId)) return;
        const inSwitch = Date.now() < _tierSwitchUntil;
        if (inSwitch) {
          const retryPc = _pc;
          const delay = (_tierSwitchUntil - Date.now()) + 1500;
          setTimeout(() => {
            if (_pc === retryPc && (_pc.iceConnectionState === 'connected' ||
                                    _pc.iceConnectionState === 'completed')) {
              return; // recovered on its own during the wait
            }
            if (_activeWindowId === windowId && _pc === retryPc && !_webrtcInProgress) {
              initWebRTCPublic(windowId, signalingUrl, instanceName, _currentSerial, iceServers);
            }
          }, delay);
        } else {
          // Dropped outside any tier-switch window: actively fall back to
          // local WHEP (using the last-known local whep/stun params)
          // instead of leaving a dead stream up with just "Disconnected".
          initWebRTC(windowId, _whepUrl, _stunUrl, _currentSerial);
        }
      };

      _pc.oniceconnectionstatechange = () => {
        const s = _pc ? _pc.iceConnectionState : '';
        console.log('[ice-public] state:', s);
        if (s === 'connected' || s === 'completed') {
          _webrtcActive = true;
          setNetStatus('good', 'Connected (public)');
          startAdaptiveQuality(_currentSerial);
          clearUnavailable();
          // Record which mode negotiated this session so the
          // visibility-resume path can route back through the public
          // path instead of a stale/local WHEP renegotiation.
          _signalingUrl = signalingUrl;
          _instanceName = instanceName;
          _publicModeActive = true;
          finish(true);
        } else if (s === 'failed' || s === 'closed') {
          setNetStatus('bad', 'Disconnected');
          if (!settled) {
            // Never connected -- let the caller fall back to local WHEP.
            finish(false);
          } else {
            retryPublicOrFallbackLocal();
          }
        } else if (s === 'disconnected') {
          setNetStatus('warn', 'Unstable');
          if (settled) {
            // Over a TURN relay, ICE connectivity checks are between the
            // browser and the RELAY -- if mediamtx silently tears down
            // its side (e.g. the scrcpy restart during a tier switch
            // destroying its PeerConnection) the relay itself is still
            // reachable, so this can sit in 'disconnected' forever and
            // never reach 'failed' at all. A real transient network blip
            // usually self-heals within a few seconds; give it a window,
            // then treat a non-recovery the same as a hard failure.
            const watchdogPc = _pc;
            setTimeout(() => {
              if (_pc !== watchdogPc) return; // superseded already
              const cur = _pc.iceConnectionState;
              if (cur === 'connected' || cur === 'completed') return;
              retryPublicOrFallbackLocal();
            }, 5000);
          }
        }
      };

      ws = new WebSocket(
        `${signalingUrl}/?session=${encodeURIComponent(instanceName)}&role=viewer`
      );

      ws.onopen = async () => {
        try {
          if (_pc !== thisPc) return;
          thisPc.addTransceiver('video', { direction: 'recvonly' });
          const offer = await thisPc.createOffer();
          if (_pc !== thisPc) return;
          await thisPc.setLocalDescription(offer);
          if (_pc !== thisPc) return;
          await waitForIceGatheringComplete(thisPc, 4000, 'relay');
          if (_pc !== thisPc) return;
          ws.send(thisPc.localDescription.sdp);
        } catch (err) {
          console.error('[webrtc-public] offer setup error:', err);
          finish(false);
        }
      };

      ws.onmessage = async (event) => {
        try {
          if (_pc !== thisPc) return;
          await thisPc.setRemoteDescription({ type: 'answer', sdp: event.data });
        } catch (err) {
          console.error('[webrtc-public] setRemoteDescription error:', err);
          finish(false);
        }
      };

      ws.onerror = () => { finish(false); };
      ws.onclose = () => { finish(false); };

      // Bound how long we wait for the public path before giving up and
      // letting the caller fall back to local WHEP -- mirrors the timeout
      // shape already used by waitForIceGatheringComplete's capMs.
      setTimeout(() => finish(false), 8000);
    } catch (err) {
      console.error('[webrtc-public] init error:', err);
      finish(false);
    }
  });
}

// ── Hybrid: race local WHEP against the public path ─────────────────────────
// Tries both connection paths concurrently and uses whichever's ICE actually
// connects first -- gives LAN/Tailscale users the low-latency local path
// with zero added delay when off-network (the public probe just wins the
// race instead of the caller waiting out a fixed local-first timeout).
//
// Each probe below is deliberately minimal and self-contained: its own
// throwaway RTCPeerConnection (and WebSocket, for the public probe), no DOM
// writes, no global _pc/_webrtcActive mutation. That's what makes racing
// them safe -- neither can corrupt the other's negotiation, and if the user
// switches windows/instances mid-race, a stale winner is caught by
// raceGen below instead of clobbering the new selection. Once a winner is
// picked, the loser's probe is left to close itself out (its own timeout or
// failure path) rather than being force-aborted -- simpler, and harmless
// since it never touched shared state.
//
// The cost: the winning path renegotiates from scratch via the real
// initWebRTC()/initWebRTCPublic() below (one extra handshake, typically
// well under 1s) instead of reusing the probe connection directly. That
// keeps this function additive -- it doesn't touch either proven function's
// internals, so their existing retry/tier-switch/fallback behavior is
// exactly as before whether reached via a race or called directly.

function _probeLocalWhep(whepUrl, stunUrl, myGen) {
  return new Promise(resolve => {
    if (!whepUrl) { resolve(false); return; }
    let settled = false;
    const pc = new RTCPeerConnection({ iceServers: stunUrl ? [{ urls: stunUrl }] : [] });
    const finish = ok => {
      if (settled) return;
      settled = true;
      clearInterval(supersededCheck);
      try { pc.close(); } catch (_) {}
      resolve(ok);
    };
    // A newer initWebRTCRace() call (a faster instance switch than this
    // probe's own 6s timeout) bumps _raceGen -- without this check, this
    // probe's real WHEP POST + RTCPeerConnection stay alive on mediamtx's
    // side for the rest of its own timeout, accumulating one abandoned
    // session per switch (confirmed live: rapid switching piled up dozens
    // of "write queue is full" sessions that only cleared via mediamtx's
    // own ~10s "deadline exceeded" timeout, one per switch).
    const supersededCheck = setInterval(() => {
      if (myGen !== _raceGen) finish(false);
    }, 250);
    pc.oniceconnectionstatechange = () => {
      const s = pc.iceConnectionState;
      if (s === 'connected' || s === 'completed') finish(true);
      else if (s === 'failed' || s === 'closed') finish(false);
    };
    pc.addTransceiver('video', { direction: 'recvonly' });
    (async () => {
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await waitForIceGatheringComplete(pc);
        if (myGen !== _raceGen) { finish(false); return; }
        const r = await fetch(whepUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/sdp' },
          body: pc.localDescription.sdp,
        });
        if (!r || !r.ok) { finish(false); return; }
        const answerSdp = await r.text();
        await pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
      } catch (_) { finish(false); }
    })();
    setTimeout(() => finish(false), 6000);
  });
}

function _probePublicSignaling(signalingUrl, instanceName, iceServers, myGen) {
  return new Promise(resolve => {
    if (!signalingUrl) { resolve(false); return; }
    let settled = false;
    const pc = new RTCPeerConnection({ iceServers: iceServers || [] });
    let ws = null;
    const finish = ok => {
      if (settled) return;
      settled = true;
      clearInterval(supersededCheck);
      try { pc.close(); } catch (_) {}
      try { ws && ws.close(); } catch (_) {}
      resolve(ok);
    };
    // See the matching comment in _probeLocalWhep -- same fix, same reason.
    const supersededCheck = setInterval(() => {
      if (myGen !== _raceGen) finish(false);
    }, 250);
    // Temporary debug visibility: this probe's 8s black-box timeout was
    // firing with no signal on WHERE ICE got stuck (no candidate pairs at
    // all vs. stuck in "checking" vs. actually reaching "connected" too
    // late). Remove once the public-path connectivity issue is diagnosed.
    console.debug('[whep-public] gathering own candidates...');
    pc.onicecandidate = e => {
      if (e.candidate) console.debug('[whep-public] local candidate:', e.candidate.candidate);
      else console.debug('[whep-public] local gathering complete');
    };
    pc.oniceconnectionstatechange = () => {
      const s = pc.iceConnectionState;
      console.debug('[whep-public] iceConnectionState ->', s);
      if (s === 'connected' || s === 'completed') finish(true);
      else if (s === 'failed' || s === 'closed') finish(false);
    };
    pc.addTransceiver('video', { direction: 'recvonly' });
    ws = new WebSocket(`${signalingUrl}/?session=${encodeURIComponent(instanceName)}&role=viewer`);
    ws.onopen = async () => {
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await waitForIceGatheringComplete(pc, 4000, 'relay');
        if (myGen !== _raceGen) { finish(false); return; }
        ws.send(pc.localDescription.sdp);
      } catch (_) { finish(false); }
    };
    ws.onmessage = async (event) => {
      try { await pc.setRemoteDescription({ type: 'answer', sdp: event.data }); }
      catch (_) { finish(false); }
    };
    ws.onerror = () => finish(false);
    ws.onclose = () => finish(false);
    setTimeout(() => finish(false), 8000);
  });
}

async function initWebRTCRace(windowId, whepUrl, stunUrl, signalingUrl, instanceName, serial, iceServers) {
  const myGen = ++_raceGen;
  setNetStatus('warn', 'Connecting…');
  // Stash params unconditionally (mirrors what initWebRTC/initWebRTCPublic do
  // for themselves): if BOTH probes fail below, neither of those functions
  // ever runs, so without this a manual reconnect after MJPEG fallback would
  // retry with stale/null params instead of the ones this call was actually
  // given.
  _activeWindowId = windowId;
  _whepUrl = whepUrl || _whepUrl;
  _stunUrl = stunUrl || _stunUrl;
  _signalingUrl = signalingUrl || _signalingUrl;
  _instanceName = instanceName || _instanceName;
  _currentSerial = serial || _currentSerial;
  _iceServers = iceServers || _iceServers;

  const winner = await new Promise(resolve => {
    let localDone = false, publicDone = false, resolved = false;
    const settle = kind => { if (!resolved) { resolved = true; resolve(kind); } };
    _probeLocalWhep(whepUrl, stunUrl, myGen).then(ok => {
      localDone = true;
      if (ok) settle('local');
      else if (publicDone) settle(null);
    });
    _probePublicSignaling(signalingUrl, instanceName, iceServers, myGen).then(ok => {
      publicDone = true;
      if (ok) settle('public');
      else if (localDone) settle(null);
    });
  });

  // A newer race (or a direct call) superseded this one while probing --
  // whatever that call started owns the connection now, abandon silently.
  if (myGen !== _raceGen) return;

  if (winner === 'local') {
    initWebRTC(windowId, whepUrl, stunUrl, serial);
  } else if (winner === 'public') {
    initWebRTCPublic(windowId, signalingUrl, instanceName, serial, iceServers);
  } else {
    _fallbackToMJPEG();
  }
}

// True when the CSS forced-landscape fallback in style.css is rotating
// #stream-container (see the comment there). getBoundingClientRect() on a
// rotated element returns the screen-space AABB, which for an exact 90deg
// rotation reports portrait dimensions again -- not usable for object-fit
// math. When active, normalizeCoords() works in the element's pre-rotation
// local box instead of trusting the rect.
function _forcedLandscapeActive() {
  return window.matchMedia('(max-width: 900px) and (orientation: portrait)').matches;
}

function normalizeCoords(clientX, clientY) {
  const el = _activeStreamEl();

  let boxW, boxH, x, y;
  if (_forcedLandscapeActive()) {
    // #stream-container is fixed at (100vh - 52px) x 100vw (pre-rotation
    // local box -- the 52px is #right-toolbar's reserved strip, see
    // style.css), rotated 90deg + translateY(-100%). Inverting that
    // transform: a screen-space tap (clientX, clientY) maps to local
    // (clientY, boxH - clientX).
    boxW = window.innerHeight - 52;
    boxH = window.innerWidth;
    x = clientY;
    y = boxH - clientX;
  } else {
    const r = getStreamRect();
    boxW = r.width;
    boxH = r.height;
    x = clientX - r.left;
    y = clientY - r.top;
  }

  // Account for object-fit:contain letterboxing.
  // The element box may be larger than the actual rendered content.
  let contentW = boxW, contentH = boxH, offsetX = 0, offsetY = 0;
  if (el && el.naturalWidth && el.naturalHeight) {
    // img: use naturalWidth/naturalHeight
    const scale = Math.min(boxW / el.naturalWidth, boxH / el.naturalHeight);
    contentW = el.naturalWidth * scale;
    contentH = el.naturalHeight * scale;
    offsetX = (boxW - contentW) / 2;
    offsetY = (boxH - contentH) / 2;
  } else if (el && el.videoWidth && el.videoHeight) {
    // video: use videoWidth/videoHeight
    const scale = Math.min(boxW / el.videoWidth, boxH / el.videoHeight);
    contentW = el.videoWidth * scale;
    contentH = el.videoHeight * scale;
    offsetX = (boxW - contentW) / 2;
    offsetY = (boxH - contentH) / 2;
  }

  return {
    x: Math.max(0, Math.min(1, (x - offsetX) / contentW)),
    y: Math.max(0, Math.min(1, (y - offsetY) / contentH)),
  };
}

function initTouch() {
  const container = document.getElementById('stream-container');

  container.addEventListener('touchstart', e => {
    if (e.target.closest('#right-toolbar')) return;
    e.preventDefault();
    if (e.touches.length === 1) {
      const t = e.touches[0];
      _dragStartX = t.clientX;
      _dragStartY = t.clientY;
      _dragMoved = false;
      _lastDragSendTime = 0;
      _dragActive = true;
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      sendInput({ type: 'drag_start', x, y });
    } else if (e.touches.length === 2) {
      if (_dragActive) {
        const { x, y } = normalizeCoords(_dragStartX, _dragStartY);
        sendInput({ type: 'drag_end', x, y });
        _dragActive = false;
      }
      _twoFingerLastY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
    }
  }, { passive: false });

  container.addEventListener('touchmove', e => {
    e.preventDefault();
    if (e.touches.length === 1 && _dragActive) {
      const t = e.touches[0];
      const dx = t.clientX - _dragStartX;
      const dy = t.clientY - _dragStartY;
      if (Math.hypot(dx, dy) > 8) _dragMoved = true;
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      const scrollDominant = Math.abs(dy) > Math.abs(dx) * 1.5;
      const now = Date.now();
      if (now - _lastDragSendTime >= 16) {
        sendInput({ type: 'drag_move', x, y, scroll: scrollDominant });
        _lastDragSendTime = now;
        if (scrollDominant) _lastScrollSendTime = now;
      }
    } else if (e.touches.length === 2 && _twoFingerLastY !== null) {
      const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
      const dy = midY - _twoFingerLastY;
      if (Math.abs(dy) > 2) {
        const { x, y } = normalizeCoords(midX, midY);
        sendInput({ type: 'scroll', x, y, dy: dy > 0 ? -1 : 1 });
        _twoFingerLastY = midY;
      }
    }
  }, { passive: false });

  container.addEventListener('touchend', e => {
    e.preventDefault();
    if (_dragActive && e.touches.length === 0) {
      const t = e.changedTouches[0];
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      if (!_dragMoved) {
        sendInput({ type: 'click', x, y });
      } else {
        sendInput({ type: 'drag_end', x, y });
      }
      _dragActive = false;
    }
    if (e.touches.length < 2) _twoFingerLastY = null;
  }, { passive: false });
}

function initMouse() {
  const container = document.getElementById('stream-container');
  let _mouseDown = false;
  let _mouseMoved = false;
  let _mouseStartX = 0;
  let _mouseStartY = 0;
  let _mouseLastSendTime = 0;
  let _mouseLastScrollSendTime = 0;

  container.addEventListener('mousedown', e => {
    if (e.target.closest('#right-toolbar')) return;
    if (e.button !== 0) return;
    e.preventDefault();
    _mouseDown = true;
    _mouseMoved = false;
    _mouseStartX = e.clientX;
    _mouseStartY = e.clientY;
    _mouseLastSendTime = 0;
    const { x, y } = normalizeCoords(e.clientX, e.clientY);
    sendInput({ type: 'drag_start', x, y });
  });

  container.addEventListener('mousemove', e => {
    if (!_mouseDown) return;
    const dx = e.clientX - _mouseStartX;
    const dy = e.clientY - _mouseStartY;
    if (Math.hypot(dx, dy) > 8) _mouseMoved = true;
    const { x, y } = normalizeCoords(e.clientX, e.clientY);
    const scrollDominant = Math.abs(dy) > Math.abs(dx) * 1.5;
    const now = Date.now();
    if (now - _mouseLastSendTime >= 30) {
      sendInput({ type: 'drag_move', x, y, scroll: scrollDominant });
      _mouseLastSendTime = now;
      if (scrollDominant) _mouseLastScrollSendTime = now;
    }
  });

  const _mouseUp = e => {
    if (e.button !== 0 || !_mouseDown) return;
    _mouseDown = false;
    const { x, y } = normalizeCoords(e.clientX, e.clientY);
    if (!_mouseMoved) {
      sendInput({ type: 'click', x, y });
    } else if (Date.now() - _mouseLastScrollSendTime > 300) {
      sendInput({ type: 'drag_end', x, y });
    }
  };

  container.addEventListener('mouseup', _mouseUp);
  container.addEventListener('mouseleave', e => {
    if (_mouseDown) {
      _mouseDown = false;
      const { x, y } = normalizeCoords(e.clientX, e.clientY);
      sendInput({ type: 'drag_end', x, y });
    }
  });
}

function initKeyboard() {
  const btn = document.getElementById('keyboard-btn');
  const input = document.getElementById('keyboard-input');

  btn.addEventListener('click', () => {
    input.style.pointerEvents = 'auto';
    input.focus();
  });

  input.addEventListener('blur', () => {
    input.style.pointerEvents = 'none';
  });

  input.addEventListener('keydown', e => {
    e.preventDefault();
    sendInput({ type: 'key', key: e.key });
  });
}

function initFPS() {}

// ── Live WebRTC stats overlay ───────────────────────────────────────────────────
let _statsTimer = null;
let _statsPrev = null;   // {bytes, ts} for bitrate delta

function _startStatsOverlay() {
  const el = document.getElementById('stats-overlay');
  if (el) el.style.display = 'block';
  _statsPrev = null;
  if (_statsTimer) clearInterval(_statsTimer);
  _statsTimer = setInterval(_sampleStats, 1000);
  _sampleStats();
}

function _stopStatsOverlay() {
  const el = document.getElementById('stats-overlay');
  if (el) el.style.display = 'none';
  if (_statsTimer) { clearInterval(_statsTimer); _statsTimer = null; }
}

async function _sampleStats() {
  const el = document.getElementById('stats-overlay');
  if (!el || !_pc) return;
  let w = 0, h = 0, fps = 0, kbps = 0, rtt = 0, lossPct = 0, jbMs = 0;
  let recvPerS = 0, dropPerS = 0;
  const stats = await _pc.getStats();
  const now = Date.now();
  stats.forEach(r => {
    if (r.type === 'inbound-rtp' && r.kind === 'video') {
      w = r.frameWidth || w;
      h = r.frameHeight || h;
      fps = r.framesPerSecond || fps;
      const recv = r.packetsReceived || 0, lost = r.packetsLost || 0;
      if (recv + lost > 0) lossPct = (lost / (recv + lost)) * 100;
      const bytes = r.bytesReceived || 0, ts = r.timestamp || 0;
      if (_statsPrev && ts > _statsPrev.ts) {
        kbps = ((bytes - _statsPrev.bytes) * 8) / (ts - _statsPrev.ts);  // bytes*8/ms = kbps
      }
      // frames received vs dropped by the DECODER, per second. If received≈30
      // but on-screen fps≈12, the iOS decoder can't keep up (decode-bound). If
      // received≈12 too, frames are lost before the decoder (mediamtx/network).
      const fr = r.framesReceived || 0, fd = r.framesDropped || 0;
      if (_statsPrev && _statsPrev.wallT && now > _statsPrev.wallT) {
        const dt = (now - _statsPrev.wallT) / 1000;
        recvPerS = (fr - (_statsPrev.fr || 0)) / dt;
        dropPerS = (fd - (_statsPrev.fd || 0)) / dt;
      }
      _statsPrev = { bytes, ts, fr, fd, wallT: now };
      // Average jitter-buffer delay per frame (s→ms).
      if (r.jitterBufferDelay != null && r.jitterBufferEmittedCount) {
        jbMs = (r.jitterBufferDelay / r.jitterBufferEmittedCount) * 1000;
      }
    }
    if (r.type === 'candidate-pair' && r.state === 'succeeded' && r.currentRoundTripTime != null) {
      rtt = r.currentRoundTripTime * 1000;
    }
  });
  el.innerHTML =
    `${w}×${h} · ${Math.round(fps)}fps (rx ${Math.round(recvPerS)} drop ${Math.round(dropPerS)})<br>` +
    `${(kbps / 1000).toFixed(1)} Mbps<br>` +
    `net RTT ${Math.round(rtt)}ms · loss ${lossPct.toFixed(1)}%<br>` +
    `input ${Math.round(_inputRttMs)}ms · jitter ${Math.round(jbMs)}ms<br>` +
    `tier ${_currentTier}`;
}

async function _startApp() {
  connectWS();
  initTouch();
  initMouse();
  initKeyboard();
  initFPS();
  initDrawer();
  startWindowsPolling();

  // ── Settings popup (gear) ──────────────────────────────────────────────────
  const setBtn = document.getElementById('settings-btn');
  const setOv = document.getElementById('settings-overlay');
  const setClose = document.getElementById('settings-close');
  const qOpts = document.getElementById('quality-opts');
  const statsToggle = document.getElementById('stats-toggle');

  function _markSelectedTier() {
    if (!qOpts) return;
    qOpts.querySelectorAll('.q-opt').forEach(b =>
      b.classList.toggle('sel', b.dataset.tier === _preferredTier));
  }

  if (setBtn && setOv) {
    setBtn.addEventListener('click', () => {
      _markSelectedTier();
      if (statsToggle) statsToggle.checked =
        document.getElementById('stats-overlay').style.display !== 'none';
      setOv.style.display = 'flex';
    });
  }
  if (setClose) setClose.addEventListener('click', () => { setOv.style.display = 'none'; });
  if (setOv) setOv.addEventListener('click', e => { if (e.target === setOv) setOv.style.display = 'none'; });

  if (qOpts) {
    qOpts.querySelectorAll('.q-opt').forEach(b => {
      b.addEventListener('click', () => {
        setPreferredTier(b.dataset.tier);
        _markSelectedTier();
      });
    });
  }
  if (statsToggle) {
    statsToggle.addEventListener('change', () => {
      if (statsToggle.checked) _startStatsOverlay();
      else _stopStatsOverlay();
    });
  }

  document.getElementById('reconnect-btn').addEventListener('click', () => {
    clearUnavailable();
    _reconnectStream();
  });

  // Reconnect stream + WS when app returns from background (iOS Safari suspends both)
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        wsRetryDelay = 1000;
        connectWS();
      }
      if (document.getElementById('screen-stream').classList.contains('active')) {
        _reconnectStream();
      }
    }
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  const ok = await (window.wcAuthReady || Promise.resolve(true));
  if (!ok) {
    window.addEventListener('wc-authenticated', _startApp, { once: true });
    return;
  }
  _startApp();
});
