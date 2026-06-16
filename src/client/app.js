// app.js — stream display, touch→input, reconnect, stats
let ws = null;
let wsRetryDelay = 1000;
let frameCount = 0;
let lastFrameTime = Date.now();
let totalFrameCount = 0;

let lastDist = null;
let currentScale = 1;

// Drag state
let _dragActive = false;
let _dragStartX = 0;
let _dragStartY = 0;
let _dragMoved = false;

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

  let lastSeenTotal = totalFrameCount;
  let lastChange = Date.now();
  window._streamPoll = setInterval(() => {
    if (gen !== _streamGeneration) { clearInterval(window._streamPoll); return; }
    const w = newImg.naturalWidth;
    if (w > 0) {
      frameCount++;
      totalFrameCount++;
      lastSeenTotal = totalFrameCount;
      lastChange = Date.now();
      clearUnavailable();
    } else if (Date.now() - lastChange > 5000) {
      clearInterval(window._streamPoll);
      setTimeout(() => { if (gen === _streamGeneration) initStream(); }, 500);
    }
  }, 200);

  newImg.onerror = () => {
    if (gen !== _streamGeneration) return;
    clearInterval(window._streamPoll);
    setTimeout(() => { if (gen === _streamGeneration) initStream(); }, 2000);
  };
}

function showUnavailable() {
  document.getElementById('unavailable-overlay').classList.add('show');
}

function clearUnavailable() {
  document.getElementById('unavailable-overlay').classList.remove('show');
}

function getStreamRect() {
  return document.getElementById('stream-img').getBoundingClientRect();
}

function normalizeCoords(clientX, clientY) {
  const r = getStreamRect();
  return {
    x: Math.max(0, Math.min(1, (clientX - r.left) / r.width)),
    y: Math.max(0, Math.min(1, (clientY - r.top) / r.height))
  };
}

function initTouch() {
  const container = document.getElementById('stream-container');

  container.addEventListener('touchstart', e => {
    // Don't capture touches on toolbar buttons
    if (e.target.closest('#right-toolbar')) return;
    if (e.touches.length === 1) {
      const t = e.touches[0];
      _dragStartX = t.clientX;
      _dragStartY = t.clientY;
      _dragMoved = false;
      _dragActive = true;
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      sendInput({ type: 'drag_start', x, y });
    } else if (e.touches.length === 2) {
      if (_dragActive) _dragActive = false;
      lastDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  }, { passive: true });

  container.addEventListener('touchmove', e => {
    if (e.touches.length === 1 && _dragActive) {
      const t = e.touches[0];
      const dx = t.clientX - _dragStartX;
      const dy = t.clientY - _dragStartY;
      if (Math.hypot(dx, dy) > 4) _dragMoved = true;
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      sendInput({ type: 'drag_move', x, y });
    } else if (e.touches.length === 2 && lastDist !== null) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      currentScale = Math.max(0.5, Math.min(4, currentScale * (dist / lastDist)));
      document.getElementById('stream-img').style.transform = `scale(${currentScale})`;
      lastDist = dist;
    }
  }, { passive: true });

  container.addEventListener('touchend', e => {
    if (_dragActive && e.touches.length === 0) {
      const t = e.changedTouches[0];
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      if (!_dragMoved) {
        // Tap — release drag, then send a clean click
        sendInput({ type: 'drag_end', x, y });
        sendInput({ type: 'click', x, y });
      } else {
        sendInput({ type: 'drag_end', x, y });
      }
      _dragActive = false;
    }
    if (e.touches.length < 2) lastDist = null;
  }, { passive: true });
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

function initFPS() {
  setInterval(() => {
    const now = Date.now();
    const elapsed = (now - lastFrameTime) / 1000;
    const fps = Math.round(frameCount / elapsed);
    const pill = document.getElementById('fps-pill');
    if (pill) pill.textContent = `${fps} fps`;
    frameCount = 0;
    lastFrameTime = now;
  }, 1000);
}

function startLockPolling() {
  let wasLocked = false;
  let lastSeenFrameCount = 0;
  let lastFrameCountChange = Date.now();
  setInterval(async () => {
    try {
      const r = await fetch('/status');
      const { locked } = await r.json();
      if (totalFrameCount !== lastSeenFrameCount) {
        lastSeenFrameCount = totalFrameCount;
        lastFrameCountChange = Date.now();
      }
      const stale = Date.now() - lastFrameCountChange > 4000;
      // Reinit if: lock→unlock transition, OR unlocked but no new frames for 4s
      if ((wasLocked && !locked) || (!locked && stale)) {
        lastFrameCountChange = Date.now(); // prevent rapid re-trigger
        initStream();
      }
      wasLocked = locked;
    } catch (_) {}
  }, 2000);
}

document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initStream();
  initTouch();
  initKeyboard();
  initFPS();
  initDrawer();
  startWindowsPolling();
  startLockPolling();

  document.getElementById('reconnect-btn').addEventListener('click', () => {
    clearUnavailable();
    initStream();
  });
});
