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
    const previewId = w.id.startsWith('adb:') ? w.id.slice(4) : w.id;
    thumb.src = `/window/${previewId}/preview?t=${Date.now()}`;
    thumb.alt = '';

    const title = document.createElement('div');
    title.className = 'window-card-title';
    title.textContent = w.title;

    card.appendChild(thumb);
    card.appendChild(title);
    card.addEventListener('click', () => selectWindow(w.id));
    grid.appendChild(card);
  });
}

async function fetchWindows() {
  try {
    const r = await fetch('/windows');
    _windows = await r.json();
    renderWindowsGrid();
  } catch (_) {}
}

async function selectWindow(id) {
  try {
    await fetch('/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    _activeId = id;
    const w = _windows.find(w => w.id === id);
    const titleEl = document.getElementById('stream-title');
    if (titleEl && w) titleEl.textContent = w.title;
    renderWindowsGrid();
    showScreen('screen-stream');
    // Request fullscreen on the stream screen
    const el = document.getElementById('screen-stream');
    if (el.requestFullscreen) el.requestFullscreen().catch(() => {});
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  } catch (_) {}
}

// ── Prev / Next window ───────────────────────────────────────────
function selectPrev() {
  if (!_windows.length) return;
  const idx = _windows.findIndex(w => w.id === _activeId);
  selectWindow(_windows[(idx - 1 + _windows.length) % _windows.length].id);
}

function selectNext() {
  if (!_windows.length) return;
  const idx = _windows.findIndex(w => w.id === _activeId);
  selectWindow(_windows[(idx + 1) % _windows.length].id);
}

function refreshThumbnails() {
  document.querySelectorAll('.window-card-thumb').forEach(img => {
    const id = img.closest('.window-card').dataset.id;
    img.src = `/window/${id}/preview?t=${Date.now()}`;
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
