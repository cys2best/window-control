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

// ── Adaptive quality (WebRTC getStats hysteresis) ──────────────────────────────
const _TIER_ORDER = ["480", "720", "1080", "1440"];
let _currentTier = "720";
let _tierManualUntil = 0;      // ms epoch; manual override wins until then
let _adaptiveTimer = null;
let _goodStreak = 0;           // consecutive good samples (for step-up debounce)
let _lastTierChange = 0;
let _adaptiveSerial = null;

function _stepTier(dir) {
  const i = _TIER_ORDER.indexOf(_currentTier);
  const j = Math.max(0, Math.min(_TIER_ORDER.length - 1, i + dir));
  return _TIER_ORDER[j];
}

async function _applyTier(tier) {
  if (tier === _currentTier || !_adaptiveSerial) return;
  _currentTier = tier;
  _lastTierChange = Date.now();
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
  if (loss > 0.03 || rtt > 250) {
    _goodStreak = 0;
    await _applyTier(_stepTier(-1));
  } else if (loss < 0.01 && rtt < 120) {
    _goodStreak += 1;
    if (_goodStreak >= 3) {  // ~15s at 5s cadence
      _goodStreak = 0;
      await _applyTier(_stepTier(+1));
    }
  } else {
    _goodStreak = 0;
  }
}

function startAdaptiveQuality(serial) {
  _adaptiveSerial = serial;
  _goodStreak = 0;
  stopAdaptiveQuality();
  _adaptiveTimer = setInterval(_sampleAndAdapt, 5000);
}

function stopAdaptiveQuality() {
  if (_adaptiveTimer) { clearInterval(_adaptiveTimer); _adaptiveTimer = null; }
}

// Retarget the live PC's serial without re-negotiating WebRTC. Used when
// switching instances while a PC is already connected to the `active` mux:
// the server has already repointed the mux, so we only need to update which
// instance the reconnect path and adaptive-quality POSTs target.
function setAdaptiveSerial(serial) {
  _currentSerial = serial;
  _adaptiveSerial = serial;
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

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/input`);
  ws.onopen = () => { wsRetryDelay = 1000; };
  ws.onclose = () => scheduleWSReconnect();
  ws.onerror = () => ws.close();
}

function scheduleWSReconnect() {
  setTimeout(() => connectWS(), wsRetryDelay);
  wsRetryDelay = Math.min(wsRetryDelay * 2, 30000);
  const pill = document.getElementById('status-pill');
  if (pill) pill.textContent = 'Reconnecting…';
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
  stopAdaptiveQuality();
  if (_pc) { try { _pc.close(); } catch(_) {} _pc = null; }
  document.getElementById('stream-video').style.display = 'none';
  document.getElementById('stream-img').style.display = 'block';
  initStream();
}

// Resolve once ICE gathering completes (or after a short cap, so a stuck
// gatherer can't hang the whole negotiation).
function waitForIceGatheringComplete(pc, capMs = 2000) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise(resolve => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      pc.removeEventListener('icegatheringstatechange', check);
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === 'complete') finish(); };
    pc.addEventListener('icegatheringstatechange', check);
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
  _activeWindowId = windowId;
  _whepUrl = whepUrl || _whepUrl;
  _stunUrl = stunUrl || _stunUrl;
  _currentSerial = serial || _currentSerial;

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
    _pc = new RTCPeerConnection({
      iceServers: _stunUrl ? [{ urls: _stunUrl }] : [],
    });

    const video = document.getElementById('stream-video');
    const img   = document.getElementById('stream-img');

    _pc.ontrack = e => {
      console.log('[webrtc] ontrack fired');
      video.srcObject = e.streams[0];
      video.onloadedmetadata = () => console.log('[webrtc] video loadedmetadata');
      video.oncanplay = () => console.log('[webrtc] video canplay');
      video.onplaying = () => console.log('[webrtc] video playing');
      video.style.display = 'block';
      img.style.display = 'none';
      _webrtcActive = true;
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
      if (s === 'failed' || s === 'closed') {
        const retryId = _activeWindowId;
        const retryPc = _pc;
        setTimeout(() => {
          if (_activeWindowId === retryId && _pc === retryPc && !_webrtcInProgress) {
            initWebRTC(retryId, undefined, undefined, _currentSerial);
          }
        }, 2000);
      }
    };

    const thisPc = _pc;
    _pc.addTransceiver('video', { direction: 'recvonly' });

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

document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initTouch();
  initMouse();
  initKeyboard();
  initFPS();
  initDrawer();
  startWindowsPolling();

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
        if (_webrtcActive && _activeWindowId) {
          initWebRTC(_activeWindowId, _whepUrl, undefined, _currentSerial);
        } else {
          initStream();
        }
      }
    }
  });

  // Fullscreen toggle button
  document.getElementById('fullscreen-btn').addEventListener('click', () => {
    const el = document.getElementById('screen-stream');
    const isFs = document.fullscreenElement || document.webkitFullscreenElement;
    if (isFs) {
      if (document.exitFullscreen) document.exitFullscreen();
      else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
    } else {
      if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
      else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
    }
  });
});
