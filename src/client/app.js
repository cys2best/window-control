// app.js — engine-session display, DataChannel input, quality, and stats

const _TIER_ORDER = ['480', '720', '1080', '1440'];
let _preferredTier = localStorage.getItem('wc_tier') || 'auto';
let _currentTier = '720';
let _tierManualUntil = 0;
let _tierSwitchUntil = 0;
let _lastTierChange = 0;
let _badStreak = 0;
let _adaptiveSerial = null;
let _adaptiveTimer = null;
let _statsTimer = null;
let _statsPrev = null;
let _echoTimer = null;
let _decodeHealthTimer = null;
let _inputRttMs = 0;
let _idrPrev = { pli: 0, freeze: 0, dropped: 0 };
let _idrLastSent = 0;
let _dragActive = false;
let _dragStartX = 0;
let _dragStartY = 0;
let _dragMoved = false;
let _twoFingerLastY = null;
let _activeWindowId = null;
let _currentSerial = null;
let _activeEngineSession = null;
let _activeSelectionGeneration = 0;
let _requestedEngineSelection = null;

// The manager owns each local/public race. The UI owns only the selected,
// ready session, so the old video remains visible while a replacement starts.
const _engineSessionManager = WindowControlEngineSessions.createManager();

function setNetStatus(state, label) {
  const el = document.getElementById('net-status');
  if (!el) return;
  el.classList.remove('net-good', 'net-warn', 'net-bad');
  el.classList.add(`net-${state}`);
  if (label) el.title = label;
}

function clearUnavailable() {
  const el = document.getElementById('unavailable-overlay');
  if (el) el.classList.remove('show');
}

function showUnavailable(message) {
  const el = document.getElementById('unavailable-overlay');
  if (!el) return;
  const label = el.querySelector('p');
  if (label && message) label.textContent = message;
  el.classList.add('show');
}

function _activeStreamEl() { return document.getElementById('stream-video'); }
function getStreamRect() { return _activeStreamEl().getBoundingClientRect(); }

function _activeInput() {
  const input = _activeEngineSession && _activeEngineSession.input;
  return input || null;
}

function _sendInput(message) {
  const input = _activeInput();
  return input ? input.send(message) : false;
}

function _setVideoStream(stream) {
  const video = _activeStreamEl();
  const image = document.getElementById('stream-img');
  video.srcObject = stream;
  video.style.display = 'block';
  if (image) image.style.display = 'none';
}

function setAdaptiveSerial(serial) {
  _currentSerial = serial;
  _adaptiveSerial = serial;
  _currentTier = '720';
  _badStreak = 0;
}

function stopAdaptiveQuality() {
  if (_adaptiveTimer) clearInterval(_adaptiveTimer);
  _adaptiveTimer = null;
}

function _stepTier(direction) {
  const index = _TIER_ORDER.indexOf(_currentTier);
  return _TIER_ORDER[Math.max(0, Math.min(_TIER_ORDER.length - 1, index + direction))];
}

async function _applyTier(tier) {
  if (tier === _currentTier || !_adaptiveSerial) return;
  _currentTier = tier;
  _lastTierChange = Date.now();
  _tierSwitchUntil = Date.now() + 8000;
  try {
    await window.wcFetch(`/instances/${_adaptiveSerial}/quality`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tier }),
    });
  } catch (_) {}
}

async function setPreferredTier(tier) {
  if (tier !== 'auto' && !_TIER_ORDER.includes(tier)) return;
  _preferredTier = tier;
  localStorage.setItem('wc_tier', tier);
  if (tier === 'auto') { _tierManualUntil = 0; return; }
  _tierManualUntil = Date.now() + 60000;
  await _applyTier(tier);
}

async function _sampleAndAdapt() {
  const session = _activeEngineSession;
  if (!session || !session.pc || Date.now() < _tierManualUntil || Date.now() - _lastTierChange < 10000) return;
  let loss = 0;
  let rtt = 0;
  let seen = false;
  const stats = await session.pc.getStats();
  stats.forEach(record => {
    if (record.type === 'inbound-rtp' && record.kind === 'video') {
      const received = record.packetsReceived || 0;
      const lost = record.packetsLost || 0;
      if (received + lost) loss = lost / (received + lost);
      seen = true;
    }
    if (record.type === 'candidate-pair' && record.state === 'succeeded') rtt = (record.currentRoundTripTime || 0) * 1000;
  });
  if (!seen) return;
  if (loss > 0.08 || rtt > 400) {
    _badStreak += 1;
    if (_badStreak >= 3) { _badStreak = 0; await _applyTier(_stepTier(-1)); }
  } else _badStreak = 0;
}

function startAdaptiveQuality(serial) {
  setAdaptiveSerial(serial);
  stopAdaptiveQuality();
  _adaptiveTimer = setInterval(_sampleAndAdapt, 5000);
}

async function _applyPersistedTier(serial, generation) {
  const tier = _preferredTier;
  if (tier === 'auto' || generation !== _activeSelectionGeneration || _adaptiveSerial !== serial) return;
  _tierManualUntil = Date.now() + 60000;
  try {
    await window.wcFetch(`/instances/${serial}/quality`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tier }),
    });
  } catch (_) { return; }
  if (generation !== _activeSelectionGeneration || _adaptiveSerial !== serial || _preferredTier !== tier) return;
  _currentTier = tier;
  _lastTierChange = Date.now();
  _tierSwitchUntil = Date.now() + 8000;
}

function _onInputMessage(data) {
  try {
    const message = JSON.parse(data);
    if (message.type === 'echo' && message.t) _inputRttMs = Date.now() - message.t;
  } catch (_) {}
}

async function _pollDecodeHealth(session) {
  if (session !== _activeEngineSession || !session.pc || session.pc.connectionState !== 'connected') return;
  const stats = await session.pc.getStats();
  stats.forEach(record => {
    if (record.type !== 'inbound-rtp' || record.kind !== 'video') return;
    const delta = {
      pli: (record.pliCount || 0) - _idrPrev.pli,
      freeze: (record.freezeCount || 0) - _idrPrev.freeze,
      dropped: (record.framesDropped || 0) - _idrPrev.dropped,
    };
    _idrPrev = { pli: record.pliCount || 0, freeze: record.freezeCount || 0, dropped: record.framesDropped || 0 };
    if ((delta.pli > 0 || delta.freeze >= 2 || delta.dropped > 5) && performance.now() - _idrLastSent > 1000) {
      if (_sendInput({ type: 'idr' })) _idrLastSent = performance.now();
    }
  });
}

function _startInputHealth(session) {
  if (_echoTimer) clearInterval(_echoTimer);
  if (_decodeHealthTimer) clearInterval(_decodeHealthTimer);
  _echoTimer = setInterval(() => _sendInput({ type: 'echo', t: Date.now() }), 2000);
  _decodeHealthTimer = setInterval(() => _pollDecodeHealth(session), 1000);
}

function _stopInputHealth() {
  if (_echoTimer) clearInterval(_echoTimer);
  if (_decodeHealthTimer) clearInterval(_decodeHealthTimer);
  _echoTimer = null;
  _decodeHealthTimer = null;
}

async function _connectEngineSelection(windowId, serial, selection, generation) {
  let stream = null;
  setNetStatus('warn', 'Connecting…');
  try {
    const session = await _engineSessionManager.connect(selection, {
      onTrack(nextStream) { if (generation === _activeSelectionGeneration) stream = nextStream; },
      onInputMessage: _onInputMessage,
      onState(state) {
        if (generation !== _activeSelectionGeneration) return;
        if (state === 'failed') {
          setNetStatus('bad', 'Disconnected');
          if (Date.now() >= _tierSwitchUntil) reconnectEngineInstance().catch(() => showUnavailable());
        }
      },
    });
    if (generation !== _activeSelectionGeneration) { await session.close(); return; }
    const previous = _activeEngineSession;
    _activeEngineSession = session;
    _activeWindowId = windowId;
    startAdaptiveQuality(serial);
    const persistedTier = _applyPersistedTier(serial, generation);
    _setVideoStream(session.stream || stream);
    _idrPrev = { pli: 0, freeze: 0, dropped: 0 };
    _startInputHealth(session);
    clearUnavailable();
    setNetStatus('good', 'Connected');
    if (previous && previous !== session) await previous.close();
    await persistedTier;
    return true;
  } catch (error) {
    if (generation !== _activeSelectionGeneration) return;
    if (error && error.code === 'credential-expired') {
      if (window.wcShowAuthGate) window.wcShowAuthGate();
      return false;
    }
    showUnavailable(error && error.code === 'capacity' ? 'Engine starting…' : 'Window unavailable');
    setNetStatus('bad', 'Disconnected');
    throw error;
  }
}

async function connectEngineInstance(windowId, serial, selection) {
  return _connectEngineSelection(windowId, serial, selection, ++_activeSelectionGeneration);
}

async function closeEngineInstance() {
  _activeSelectionGeneration += 1;
  _requestedEngineSelection = null;
  if (window.wcClearActiveWindow) window.wcClearActiveWindow();
  _stopInputHealth();
  stopAdaptiveQuality();
  const session = _activeEngineSession;
  _activeEngineSession = null;
  if (session) await session.close();
}

function _wait(milliseconds) { return new Promise(resolve => setTimeout(resolve, milliseconds)); }

async function fetchEngineSelection(serial) {
  const delays = [0, 250, 500, 1000, 2000];
  let response = null;
  for (const delay of delays) {
    if (delay) await _wait(delay);
    response = await window.wcFetch(`/instances/${serial}/select`, { method: 'POST' });
    if (response.status === 401) {
      if (window.wcShowAuthGate) window.wcShowAuthGate();
      const error = new Error('Selection requires authentication');
      error.code = 'unauthorized';
      throw error;
    }
    if (response.status === 503) showUnavailable('Engine starting…');
    if (response.status !== 503) break;
  }
  if (!response || response.status === 503) {
    const error = new Error('Engine unavailable');
    error.code = 'capacity';
    throw error;
  }
  if (!response.ok) throw new Error(`Selection failed (${response.status})`);
  return response.json();
}

async function reconnectEngineInstance() {
  const target = _requestedEngineSelection || (_currentSerial && _activeWindowId && {
    windowId: _activeWindowId, serial: _currentSerial,
  });
  if (!target) return;
  await selectEngineInstance(target.windowId, target.serial);
}

async function selectEngineInstance(windowId, serial) {
  const generation = ++_activeSelectionGeneration;
  const requested = { windowId, serial, generation };
  _requestedEngineSelection = requested;
  try {
    const selected = await fetchEngineSelection(serial);
    if (generation !== _activeSelectionGeneration) return false;
    const adopted = await _connectEngineSelection(windowId, serial, selected, generation);
    if (generation === _activeSelectionGeneration && _requestedEngineSelection === requested && adopted) {
      _requestedEngineSelection = null;
    }
    return adopted;
  } catch (error) {
    // Keep an initial unavailable target so Reconnect can retry it, but a
    // failed switch must leave the already-adopted instance authoritative.
    if (generation === _activeSelectionGeneration && _activeEngineSession) _requestedEngineSelection = null;
    throw error;
  }
}

function _forcedLandscapeActive() {
  return window.matchMedia && window.matchMedia('(max-width: 900px) and (orientation: portrait)').matches;
}

function normalizeCoords(clientX, clientY) {
  const el = _activeStreamEl();
  let boxW;
  let boxH;
  let x;
  let y;
  if (_forcedLandscapeActive()) {
    boxW = window.innerHeight - 52;
    boxH = window.innerWidth;
    x = clientY;
    y = boxH - clientX;
  } else {
    const rect = getStreamRect();
    boxW = rect.width; boxH = rect.height; x = clientX - rect.left; y = clientY - rect.top;
  }
  const mediaW = el.videoWidth || el.naturalWidth || boxW;
  const mediaH = el.videoHeight || el.naturalHeight || boxH;
  const scale = Math.min(boxW / mediaW, boxH / mediaH);
  const contentW = mediaW * scale;
  const contentH = mediaH * scale;
  return {
    x: Math.max(0, Math.min(1, (x - (boxW - contentW) / 2) / contentW)),
    y: Math.max(0, Math.min(1, (y - (boxH - contentH) / 2) / contentH)),
  };
}

function _endDrag(clientX, clientY) {
  if (!_dragActive) return;
  const point = normalizeCoords(clientX, clientY);
  const input = _activeInput();
  if (input) input.dragEnd(point.x, point.y);
  _dragActive = false;
  _dragMoved = false;
}

function initTouch() {
  const container = document.getElementById('stream-container');
  if (!container) return;
  container.addEventListener('touchstart', event => {
    if (event.target.closest && event.target.closest('#right-toolbar')) return;
    event.preventDefault();
    if (event.touches.length === 1) {
      const touch = event.touches[0];
      _dragStartX = touch.clientX; _dragStartY = touch.clientY; _dragMoved = false; _dragActive = true;
      const point = normalizeCoords(touch.clientX, touch.clientY);
      const input = _activeInput();
      if (input) input.dragStart(point.x, point.y);
    } else if (event.touches.length === 2) {
      _endDrag(_dragStartX, _dragStartY);
      _twoFingerLastY = (event.touches[0].clientY + event.touches[1].clientY) / 2;
    }
  }, { passive: false });
  container.addEventListener('touchmove', event => {
    event.preventDefault();
    const input = _activeInput();
    if (event.touches.length === 1 && _dragActive) {
      const touch = event.touches[0];
      if (Math.hypot(touch.clientX - _dragStartX, touch.clientY - _dragStartY) > 8) _dragMoved = true;
      const point = normalizeCoords(touch.clientX, touch.clientY);
      if (input && _dragMoved) input.dragMove(point.x, point.y);
    } else if (event.touches.length === 2 && _twoFingerLastY !== null) {
      const midY = (event.touches[0].clientY + event.touches[1].clientY) / 2;
      const midX = (event.touches[0].clientX + event.touches[1].clientX) / 2;
      const deltaPixels = midY - _twoFingerLastY;
      if (input && Math.abs(deltaPixels) > 2) {
        const point = normalizeCoords(midX, midY);
        const contentHeight = Math.max(1, _activeStreamEl().getBoundingClientRect().height);
        input.scroll(point.x, point.y, -deltaPixels / contentHeight);
      }
      _twoFingerLastY = midY;
    }
  }, { passive: false });
  const end = event => {
    event.preventDefault();
    if (_dragActive && event.touches.length === 0) {
      const touch = event.changedTouches[0];
      _endDrag(touch.clientX, touch.clientY);
    }
    if (event.touches.length < 2) _twoFingerLastY = null;
  };
  container.addEventListener('touchend', end, { passive: false });
  container.addEventListener('touchcancel', end, { passive: false });
}

function initMouse() {
  const container = document.getElementById('stream-container');
  if (!container) return;
  let down = false;
  let startX = 0;
  let startY = 0;
  container.addEventListener('mousedown', event => {
    if (event.button !== 0 || (event.target.closest && event.target.closest('#right-toolbar'))) return;
    event.preventDefault();
    down = true; startX = event.clientX; startY = event.clientY; _dragMoved = false;
    const point = normalizeCoords(event.clientX, event.clientY);
    const input = _activeInput();
    if (input) input.dragStart(point.x, point.y);
  });
  container.addEventListener('mousemove', event => {
    if (!down) return;
    if (Math.hypot(event.clientX - startX, event.clientY - startY) > 8) _dragMoved = true;
    if (_dragMoved) {
      const point = normalizeCoords(event.clientX, event.clientY);
      const input = _activeInput();
      if (input) input.dragMove(point.x, point.y);
    }
  });
  const end = event => {
    if (event.button !== 0 || !down) return;
    down = false;
    const point = normalizeCoords(event.clientX, event.clientY);
    const input = _activeInput();
    if (input) input.dragEnd(point.x, point.y);
  };
  container.addEventListener('mouseup', end);
  container.addEventListener('mouseleave', end);
}

function initPointer() {
  const container = document.getElementById('stream-container');
  if (!container || !window.PointerEvent) return false;
  let pointerId = null;
  let startX = 0;
  let startY = 0;
  let moved = false;
  container.addEventListener('pointerdown', event => {
    if (event.button !== 0 || (event.target.closest && event.target.closest('#right-toolbar'))) return;
    event.preventDefault();
    pointerId = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
    moved = false;
    if (container.setPointerCapture) container.setPointerCapture(pointerId);
    const point = normalizeCoords(event.clientX, event.clientY);
    const input = _activeInput();
    if (input) input.dragStart(point.x, point.y);
  });
  container.addEventListener('pointermove', event => {
    if (event.pointerId !== pointerId) return;
    if (Math.hypot(event.clientX - startX, event.clientY - startY) > 8) moved = true;
    if (!moved) return;
    const point = normalizeCoords(event.clientX, event.clientY);
    const input = _activeInput();
    if (input) input.dragMove(point.x, point.y);
  });
  const end = event => {
    if (event.pointerId !== pointerId) return;
    pointerId = null;
    const point = normalizeCoords(event.clientX, event.clientY);
    const input = _activeInput();
    if (input) input.dragEnd(point.x, point.y);
  };
  container.addEventListener('pointerup', end);
  container.addEventListener('pointercancel', end);
  return true;
}

function initKeyboard() {
  const button = document.getElementById('keyboard-btn');
  const input = document.getElementById('keyboard-input');
  if (!button || !input) return;
  button.addEventListener('click', () => { input.style.pointerEvents = 'auto'; input.focus(); });
  input.addEventListener('blur', () => { input.style.pointerEvents = 'none'; });
  input.addEventListener('keydown', event => { event.preventDefault(); _sendInput({ type: 'key', key: event.key }); });
}

function _startStatsOverlay() {
  const el = document.getElementById('stats-overlay');
  if (el) el.style.display = 'block';
  _statsPrev = null;
  if (_statsTimer) clearInterval(_statsTimer);
  _statsTimer = setInterval(_sampleStats, 1000);
}

function _stopStatsOverlay() {
  const el = document.getElementById('stats-overlay');
  if (el) el.style.display = 'none';
  if (_statsTimer) clearInterval(_statsTimer);
  _statsTimer = null;
}

async function _sampleStats() {
  if (!_activeEngineSession || !_activeEngineSession.pc) return;
  const el = document.getElementById('stats-overlay');
  if (!el) return;
  let fps = 0;
  let kbps = 0;
  const stats = await _activeEngineSession.pc.getStats();
  stats.forEach(record => {
    if (record.type !== 'inbound-rtp' || record.kind !== 'video') return;
    fps = record.framesPerSecond || 0;
    if (_statsPrev && record.timestamp > _statsPrev.timestamp) kbps = (record.bytesReceived - _statsPrev.bytes) * 8 / (record.timestamp - _statsPrev.timestamp);
    _statsPrev = { bytes: record.bytesReceived || 0, timestamp: record.timestamp || 0 };
  });
  el.textContent = `${Math.round(fps)}fps · ${(kbps / 1000).toFixed(1)} Mbps · input ${Math.round(_inputRttMs)}ms · tier ${_currentTier}`;
}

async function _startApp() {
  const touchCapable = (window.navigator && window.navigator.maxTouchPoints > 0) || 'ontouchstart' in window;
  if (touchCapable) { initTouch(); initMouse(); }
  else if (!initPointer()) initMouse();
  initKeyboard(); initDrawer(); startWindowsPolling();
  const settingsButton = document.getElementById('settings-btn');
  const settingsOverlay = document.getElementById('settings-overlay');
  const settingsClose = document.getElementById('settings-close');
  const qualityOptions = document.getElementById('quality-opts');
  const statsToggle = document.getElementById('stats-toggle');
  const markSelectedTier = () => {
    if (!qualityOptions || typeof qualityOptions.querySelectorAll !== 'function') return;
    qualityOptions.querySelectorAll('.q-opt').forEach(button =>
      button.classList.toggle('sel', button.dataset.tier === _preferredTier));
  };
  if (settingsButton && settingsOverlay) settingsButton.addEventListener('click', () => {
    markSelectedTier();
    if (statsToggle) statsToggle.checked = document.getElementById('stats-overlay').style.display !== 'none';
    settingsOverlay.style.display = 'flex';
  });
  if (settingsClose && settingsOverlay) settingsClose.addEventListener('click', () => { settingsOverlay.style.display = 'none'; });
  if (settingsOverlay) settingsOverlay.addEventListener('click', event => {
    if (event.target === settingsOverlay) settingsOverlay.style.display = 'none';
  });
  if (qualityOptions && typeof qualityOptions.querySelectorAll === 'function') {
    qualityOptions.querySelectorAll('.q-opt').forEach(button => button.addEventListener('click', () => {
      setPreferredTier(button.dataset.tier);
      markSelectedTier();
    }));
  }
  const reconnect = document.getElementById('reconnect-btn');
  if (reconnect) reconnect.addEventListener('click', () => reconnectEngineInstance().catch(() => showUnavailable()));
  if (statsToggle) statsToggle.addEventListener('change', () => statsToggle.checked ? _startStatsOverlay() : _stopStatsOverlay());
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && _activeWindowId) reconnectEngineInstance().catch(() => showUnavailable());
  });
  window.addEventListener('beforeunload', () => { closeEngineInstance(); _engineSessionManager.close(); });
}

document.addEventListener('DOMContentLoaded', async () => {
  const ok = await (window.wcAuthReady || Promise.resolve(true));
  if (!ok) { window.addEventListener('wc-authenticated', _startApp, { once: true }); return; }
  _startApp();
});
