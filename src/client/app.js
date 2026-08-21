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
let _publicModeActive = false; // true when the active stream was negotiated via initWebRTCPublic

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
  };
  ws.onmessage = ev => {
    try {
      const m = JSON.parse(ev.data);
      if (m.type === 'echo' && m.t) _inputRttMs = Date.now() - m.t;
    } catch (_) {}
  };
  ws.onclose = () => { if (_echoTimer) { clearInterval(_echoTimer); _echoTimer = null; } scheduleWSReconnect(); };
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

function initStream() {
  clearInterval(window._streamPoll);
  _streamGeneration++;
  const gen = _streamGeneration;

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
  newImg.onload = () => { if (gen === _streamGeneration) { clearUnavailable(); } };

  newImg.onerror = () => {
    if (gen !== _streamGeneration) return;
    clearInterval(window._streamPoll);
    setTimeout(() => { if (gen === _streamGeneration) initStream(); }, 2000);
  };

  // Staleness: if img stops loading (server died), onerror fires.
  // Don't poll naturalWidth — it doesn't change per-frame for MJPEG.
  // Lock polling handles reinit on lock→unlock. No stale-reinit needed here.
}

function clearUnavailable() {
  document.getElementById('unavailable-overlay').classList.remove('show');
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
function waitForIceGatheringComplete(pc, capMs = 4000) {
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
    // Fast path: as soon as the reflexive candidate is in, we can POST.
    const onCand = e => {
      if (e.candidate && e.candidate.candidate &&
          e.candidate.candidate.includes('typ srflx')) {
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

async function initWebRTCPublic(windowId, signalingUrl, instanceName, serial) {
  // Structurally parallel to initWebRTC() above -- same _pc lifecycle,
  // same ontrack/oniceconnectionstatechange handlers, same in-flight-
  // negotiation race guards (if (_pc !== thisPc) return). Differs only in
  // negotiation transport: WS + raw SDP text instead of one-shot HTTP POST,
  // matching signaling_bridge.py's relay_one_instance() protocol exactly
  // (proven end-to-end against the real VPS + mediamtx during manual
  // testing) -- {signalingUrl}/?session={instanceName}&role=viewer, no
  // JSON envelope, raw SDP text both directions.
  if (_pc) { try { _pc.close(); } catch(_) {} _pc = null; }
  if (_webrtcInProgress) {
    _webrtcInProgress = false;
    await new Promise(r => setTimeout(r, 50));
  }
  _webrtcInProgress = true;
  setNetStatus('warn', 'Connecting (public)…');
  _activeWindowId = windowId;
  _currentSerial = serial || _currentSerial;

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
      _pc = new RTCPeerConnection({ iceServers: [] });
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

      _pc.oniceconnectionstatechange = () => {
        const s = _pc ? _pc.iceConnectionState : '';
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
          } else if (_pc === thisPc && _activeWindowId === windowId) {
            // Was connected and then dropped after we'd already resolved
            // success: actively fall back to local WHEP (using the
            // last-known local whep/stun params) instead of leaving a
            // dead stream up with just a "Disconnected" status.
            initWebRTC(windowId, _whepUrl, _stunUrl, _currentSerial);
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
          await waitForIceGatheringComplete(thisPc);
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

function normalizeCoords(clientX, clientY) {
  const r = getStreamRect();
  const el = _activeStreamEl();

  // Account for object-fit:contain letterboxing.
  // The element box may be larger than the actual rendered content.
  let contentW = r.width, contentH = r.height, offsetX = 0, offsetY = 0;
  if (el && el.naturalWidth && el.naturalHeight) {
    // img: use naturalWidth/naturalHeight
    const scale = Math.min(r.width / el.naturalWidth, r.height / el.naturalHeight);
    contentW = el.naturalWidth * scale;
    contentH = el.naturalHeight * scale;
    offsetX = (r.width - contentW) / 2;
    offsetY = (r.height - contentH) / 2;
  } else if (el && el.videoWidth && el.videoHeight) {
    // video: use videoWidth/videoHeight
    const scale = Math.min(r.width / el.videoWidth, r.height / el.videoHeight);
    contentW = el.videoWidth * scale;
    contentH = el.videoHeight * scale;
    offsetX = (r.width - contentW) / 2;
    offsetY = (r.height - contentH) / 2;
  }

  return {
    x: Math.max(0, Math.min(1, (clientX - r.left - offsetX) / contentW)),
    y: Math.max(0, Math.min(1, (clientY - r.top - offsetY) / contentH)),
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

document.addEventListener('DOMContentLoaded', () => {
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
    initStream();
  });

  // Reconnect stream + WS when app returns from background (iOS Safari suspends both)
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        wsRetryDelay = 1000;
        connectWS();
      }
      if (document.getElementById('screen-stream').classList.contains('active')) {
        if (_publicModeActive && _activeWindowId && _signalingUrl && _instanceName) {
          // The active session was negotiated via the public signaling path
          // (VPS relay), not local WHEP -- resume through the same path
          // instead of renegotiating against a stale/unreachable _whepUrl.
          initWebRTCPublic(_activeWindowId, _signalingUrl, _instanceName, _currentSerial);
        } else if (_webrtcActive && _activeWindowId) {
          initWebRTC(_activeWindowId, _whepUrl, undefined, _currentSerial);
        } else {
          initStream();
        }
      }
    }
  });
});
