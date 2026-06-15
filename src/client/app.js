// app.js — stream display, touch→input, reconnect, stats
let ws = null;
let wsRetryDelay = 1000;
let frameCount = 0;
let lastFrameTime = Date.now();

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

function initStream() {
  const img = document.getElementById('stream-img');
  img.src = '/stream?' + Date.now();

  let lastW = 0, lastCheck = Date.now();
  clearInterval(window._streamPoll);
  window._streamPoll = setInterval(() => {
    const w = img.naturalWidth;
    if (w > 0) {
      frameCount++;
      clearUnavailable();
      lastW = w;
      lastCheck = Date.now();
    } else if (Date.now() - lastCheck > 5000) {
      // No frame for 5s — silently reconnect stream
      clearInterval(window._streamPoll);
      img.src = '';
      clearUnavailable();
      setTimeout(() => initStream(), 1000);
    }
  }, 200);

  img.onerror = () => {
    clearInterval(window._streamPoll);
    setTimeout(() => initStream(), 2000);
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
    if (e.touches.length === 1) {
      const t = e.touches[0];
      _dragStartX = t.clientX;
      _dragStartY = t.clientY;
      _dragMoved = false;
      _dragActive = true;
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      sendInput({ type: 'drag_start', x, y });
    } else if (e.touches.length === 2) {
      // Cancel drag if second finger added
      if (_dragActive) {
        _dragActive = false;
      }
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

document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initStream();
  initTouch();
  initKeyboard();
  initFPS();
  initDrawer();
  startWindowsPolling();

  document.getElementById('reconnect-btn').addEventListener('click', () => {
    clearUnavailable();
    initStream();
  });
});
