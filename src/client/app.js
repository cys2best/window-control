// app.js — stream display, touch→input, reconnect, stats
let ws = null;
let wsRetryDelay = 1000;
let frameCount = 0;
let lastFrameTime = Date.now();
let fpsInterval = null;
let streamStallTimer = null;

// Pinch zoom state
let lastDist = null;
let currentScale = 1;

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
  document.getElementById('status-pill').textContent = 'Reconnecting…';
}

function sendInput(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function initStream() {
  const img = document.getElementById('stream-img');
  img.src = '/stream';

  img.onload = () => {
    frameCount++;
    resetStallTimer();
    clearUnavailable();
  };

  img.onerror = () => {
    scheduleStreamReconnect();
  };
}

function scheduleStreamReconnect() {
  setTimeout(() => {
    const img = document.getElementById('stream-img');
    img.src = '/stream?' + Date.now();
  }, 2000);
}

function resetStallTimer() {
  clearTimeout(streamStallTimer);
  streamStallTimer = setTimeout(() => showUnavailable(), 2000);
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
    if (e.touches.length === 2) {
      lastDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  }, { passive: true });

  container.addEventListener('touchmove', e => {
    if (e.touches.length === 2 && lastDist !== null) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      currentScale = Math.max(0.5, Math.min(4, currentScale * (dist / lastDist)));
      document.getElementById('stream-img').style.transform = `scale(${currentScale})`;
      lastDist = dist;
    } else if (e.touches.length === 2) {
      const dx = e.touches[0].clientX - e.touches[1].clientX;
      const dy = e.touches[0].clientY - e.touches[1].clientY;
      sendInput({ type: 'scroll', dx: Math.round(dx / 20), dy: Math.round(dy / 20) });
    }
  }, { passive: true });

  container.addEventListener('touchend', e => {
    if (e.changedTouches.length === 1 && e.touches.length === 0) {
      const t = e.changedTouches[0];
      const { x, y } = normalizeCoords(t.clientX, t.clientY);
      sendInput({ type: 'click', x, y });
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
  fpsInterval = setInterval(() => {
    const now = Date.now();
    const elapsed = (now - lastFrameTime) / 1000;
    const fps = Math.round(frameCount / elapsed);
    document.getElementById('fps-pill').textContent = `${fps} fps`;
    frameCount = 0;
    lastFrameTime = now;
  }, 1000);
}

function initReconnectBtn() {
  document.getElementById('reconnect-btn').addEventListener('click', () => {
    clearUnavailable();
    initStream();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  connectWS();
  initStream();
  initTouch();
  initKeyboard();
  initFPS();
  initReconnectBtn();
  initDrawer();
  startWindowsPolling();
});
