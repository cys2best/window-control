// windows_panel.js — left panel + window switching
let _windows = [];
let _activeId = null;

// ── Left panel ──────────────────────────────────────────────────
function initLeftPanel() {
  const panel  = document.getElementById('left-panel');
  const toggle = document.getElementById('left-toggle');
  const close  = document.getElementById('left-close');

  const iconSpan = document.getElementById('left-toggle-icon');
  toggle.addEventListener('click', () => {
    panel.classList.toggle('open');
    iconSpan.innerHTML = panel.classList.contains('open') ? '&#9664;' : '&#9654;';
  });
  close.addEventListener('click', () => {
    panel.classList.remove('open');
    iconSpan.innerHTML = '&#9654;';
  });
}

function closeLeftPanel() {
  const panel    = document.getElementById('left-panel');
  const iconSpan = document.getElementById('left-toggle-icon');
  panel.classList.remove('open');
  if (iconSpan) iconSpan.innerHTML = '&#9654;';
}

// ── Window list rendering ────────────────────────────────────────
function renderWindowsList() {
  const list = document.getElementById('windows-list');
  list.innerHTML = '';
  _windows.forEach(w => {
    const row = document.createElement('div');
    row.className = 'window-row' + (w.id === _activeId ? ' active' : '');
    row.dataset.id = w.id;

    const thumb = document.createElement('img');
    thumb.className = 'window-thumb';
    thumb.src = `/window/${w.id}/preview?t=${Date.now()}`;
    thumb.alt = '';

    const title = document.createElement('span');
    title.className = 'window-title';
    title.textContent = w.title;

    row.appendChild(thumb);
    row.appendChild(title);
    row.addEventListener('click', () => {
      selectWindow(w.id);
      closeLeftPanel();
    });
    list.appendChild(row);
  });
}

async function fetchWindows() {
  try {
    const r = await fetch('/windows');
    _windows = await r.json();
    renderWindowsList();
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
    renderWindowsList();
  } catch (_) {}
}

// ── Prev / Next window ───────────────────────────────────────────
function selectPrev() {
  if (!_windows.length) return;
  const idx = _windows.findIndex(w => w.id === _activeId);
  const next = _windows[(idx - 1 + _windows.length) % _windows.length];
  selectWindow(next.id);
}

function selectNext() {
  if (!_windows.length) return;
  const idx = _windows.findIndex(w => w.id === _activeId);
  const next = _windows[(idx + 1) % _windows.length];
  selectWindow(next.id);
}

function initPrevNext() {
  document.getElementById('prev-btn').addEventListener('click', selectPrev);
  document.getElementById('next-btn').addEventListener('click', selectNext);
}

function refreshThumbnails() {
  document.querySelectorAll('.window-thumb').forEach(img => {
    const id = img.closest('.window-row').dataset.id;
    img.src = `/window/${id}/preview?t=${Date.now()}`;
  });
}

function startWindowsPolling() {
  fetchWindows();
  setInterval(() => {
    fetchWindows();
    refreshThumbnails();
  }, 2500);
}

function setActiveWindow(id) {
  _activeId = id;
  renderWindowsList();
}

// called from app.js DOMContentLoaded
function initDrawer() {
  initLeftPanel();
  initPrevNext();
}
