// windows_panel.js — window list screen + window switching
let _windows = [];
let _activeId = null;

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ── Window grid rendering ────────────────────────────────────────
function renderWindowsGrid() {
  const grid = document.getElementById('windows-grid');
  grid.innerHTML = '';
  _windows.forEach(w => {
    const card = document.createElement('div');
    card.className = 'window-card' + (w.id === _activeId ? ' active' : '');
    card.dataset.id = w.id;

    const thumb = document.createElement('img');
    thumb.className = 'window-card-thumb';
    // serial is either in w.serial or parsed from w.id ("adb:SERIAL")
    const serial = w.serial || (w.id.startsWith('adb:') ? w.id.slice(4) : w.id);
    thumb.src = `/instances/${serial}/preview?t=${Date.now()}`;
    thumb.alt = '';

    const title = document.createElement('div');
    title.className = 'window-card-title';
    title.textContent = w.title;

    card.appendChild(thumb);
    card.appendChild(title);
    card.addEventListener('click', () => selectWindow(w.id, w.serial));
    grid.appendChild(card);
  });
}

async function fetchWindows() {
  try {
    const r = await fetch('/instances');
    _windows = await r.json();
    renderWindowsGrid();
  } catch (_) {}
}

async function selectWindow(id, serial) {
  // serial may be undefined if called from legacy path
  const _serial = serial || (id.startsWith('adb:') ? id.slice(4) : id);
  try {
    const r = await fetch(`/instances/${_serial}/select`, { method: 'POST' });
    const data = await r.json();
    _activeId = id;
    const w = _windows.find(w => w.id === id);
    const titleEl = document.getElementById('stream-title');
    if (titleEl && w) titleEl.textContent = w.title;
    renderWindowsGrid();
    showScreen('screen-stream');
    // Pass WHEP URL from server response so client connects to mediamtx directly
    initWebRTC(id, data.whep_url);
  } catch (_) {}
}

// ── Prev / Next window ───────────────────────────────────────────
function selectPrev() {
  if (!_windows.length) return;
  const idx = _windows.findIndex(w => w.id === _activeId);
  const w = _windows[(idx - 1 + _windows.length) % _windows.length];
  selectWindow(w.id, w.serial);
}

function selectNext() {
  if (!_windows.length) return;
  const idx = _windows.findIndex(w => w.id === _activeId);
  const w = _windows[(idx + 1) % _windows.length];
  selectWindow(w.id, w.serial);
}

function refreshThumbnails() {
  document.querySelectorAll('.window-card-thumb').forEach(img => {
    const id = img.closest('.window-card').dataset.id;
    const serial = id.startsWith('adb:') ? id.slice(4) : id;
    img.src = `/instances/${serial}/preview?t=${Date.now()}`;
  });
}

function startWindowsPolling() {
  fetchWindows();
  setInterval(() => {
    if (document.getElementById('screen-list').classList.contains('active')) {
      fetchWindows();
    }
  }, 60000);
}

function initDrawer() {
  document.getElementById('back-btn').addEventListener('click', () => {
    showScreen('screen-list');
    fetchWindows();
  });

  document.getElementById('list-refresh-btn').addEventListener('click', fetchWindows);

  document.getElementById('prev-btn').addEventListener('click', selectPrev);
  document.getElementById('next-btn').addEventListener('click', selectNext);
}
