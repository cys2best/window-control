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
  newImg.onload = () => { if (gen === _streamGeneration) { clearUnavailable(); updateRotation(); } };

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

// ── Landscape rotate ──────────────────────────────────────────────────────────
// When the device is portrait but the stream is landscape, rotate the container
// 90° CW (body.rotated) so it fills the tall screen. We track _rotated so
// normalizeCoords can remap touch coordinates into the stream's frame.
let _rotated = false;

function _activeStreamEl() {
  const video = document.getElementById('stream-video');
  if (_webrtcActive && video.style.display !== 'none') return video;
  return document.getElementById('stream-img');
}

function _streamNaturalSize() {
  const el = _activeStreamEl();
  if (!el) return null;
  const w = el.videoWidth || el.naturalWidth || 0;
  const h = el.videoHeight || el.naturalHeight || 0;
  return (w && h) ? { w, h } : null;
}

// Decide whether to rotate: only when the viewport is portrait AND the stream
// is landscape. Re-evaluated on select, on metadata load, and on orientation
// change. No-op if the phone is already landscape.
function updateRotation() {
  const onStream = document.getElementById('screen-stream').classList.contains('active');
  const nat = _streamNaturalSize();
  const portrait = window.innerHeight >= window.innerWidth;
  const landscapeStream = nat ? nat.w > nat.h : true; // assume landscape until known
  const shouldRotate = onStream && portrait && landscapeStream;
  if (shouldRotate !== _rotated) {
    _rotated = shouldRotate;
    document.body.classList.toggle('rotated', _rotated);
  }
}

function clearRotation() {
  _rotated = false;
  document.body.classList.remove('rotated');
}

// ── WebRTC via WHEP (mediamtx) ────────────────────────────────────────────────

function _fallbackToMJPEG() {
  _webrtcActive = false;
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

async function initWebRTC(windowId, whepUrl, stunUrl) {
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
      video.onloadedmetadata = () => { console.log('[webrtc] video loadedmetadata'); updateRotation(); };
      video.oncanplay = () => console.log('[webrtc] video canplay');
      video.onplaying = () => console.log('[webrtc] video playing');
      video.style.display = 'block';
      img.style.display = 'none';
      _webrtcActive = true;
      clearUnavailable();
      updateRotation();
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
            initWebRTC(retryId);
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
  if (_rotated) return _normalizeCoordsRotated(clientX, clientY);

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

// Rotated (body.rotated) touch mapping. The container is rotated 90° CW, so we
// can't trust its getBoundingClientRect. Instead map screen coords into the
// container's local (pre-rotation) frame, then apply object-fit:contain letter-
// boxing against the swapped viewport dimensions.
//
// Screen (sx, sy) in viewport W×H maps to local (u, v) = (sy, W - sx), where the
// local box is H wide (stream horizontal) × W tall (stream vertical).
function _normalizeCoordsRotated(sx, sy) {
  const W = window.innerWidth;
  const H = window.innerHeight;
  const nat = _streamNaturalSize();

  // Local frame: horizontal axis length H, vertical axis length W.
  const u = sy;          // along stream width  (0..H)
  const v = W - sx;      // along stream height (0..W)

  let contentW = H, contentH = W, offsetX = 0, offsetY = 0;
  if (nat) {
    const scale = Math.min(H / nat.w, W / nat.h);
    contentW = nat.w * scale;
    contentH = nat.h * scale;
    offsetX = (H - contentW) / 2;
    offsetY = (W - contentH) / 2;
  }

  return {
    x: Math.max(0, Math.min(1, (u - offsetX) / contentW)),
    y: Math.max(0, Math.min(1, (v - offsetY) / contentH)),
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

  // Re-evaluate landscape rotate when the device turns or the viewport resizes.
  window.addEventListener('orientationchange', () => setTimeout(updateRotation, 200));
  window.addEventListener('resize', updateRotation);

  // Reconnect stream + WS when app returns from background (iOS Safari suspends both)
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        wsRetryDelay = 1000;
        connectWS();
      }
      if (document.getElementById('screen-stream').classList.contains('active')) {
        if (_webrtcActive && _activeWindowId) {
          initWebRTC(_activeWindowId, _whepUrl);
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
