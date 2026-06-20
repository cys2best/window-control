// windows_panel.js — window list screen + window switching
let _windows = [];
let _activeId = null;

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

// ── Window grid rendering ────────────────────────────────────────
let _thumbObserver = null;

function _initThumbObserver() {
  if (_thumbObserver) return;
  _thumbObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const img = entry.target;
      if (img.dataset.lazySrc) {
        img.src = img.dataset.lazySrc;
        delete img.dataset.lazySrc;
        _thumbObserver.unobserve(img);
      }
    });
  }, { rootMargin: '50px' });
}

function renderWindowsGrid() {
  _initThumbObserver();
  const grid = document.getElementById('windows-grid');
  grid.innerHTML = '';
  _windows.forEach(w => {
    const card = document.createElement('div');
    card.className = 'window-card' + (w.id === _activeId ? ' active' : '');
    card.dataset.id = w.id;

    const thumb = document.createElement('img');
    thumb.className = 'window-card-thumb';
    const serial = w.serial || (w.id.startsWith('adb:') ? w.id.slice(4) : w.id);
    // Lazy-load: only fetch preview when card scrolls into view
    thumb.dataset.lazySrc = `/instances/${serial}/preview?t=${Date.now()}`;
    thumb.alt = '';
    _thumbObserver.observe(thumb);

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
  const _serial = serial || (id.startsWith('adb:') ? id.slice(4) : id);
  // Navigate immediately — don't block on server round-trip
  _activeId = id;
  const w = _windows.find(w => w.id === id);
  const titleEl = document.getElementById('stream-title');
  if (titleEl && w) titleEl.textContent = w.title;
  renderWindowsGrid();
  showScreen('screen-stream');
  try {
    const r = await fetch(`/instances/${_serial}/select`, { method: 'POST' });
    const data = await r.json();
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
    const url = `/instances/${serial}/preview?t=${Date.now()}`;
    if (img.dataset.lazySrc) {
      img.dataset.lazySrc = url;
    } else {
      img.src = url;
    }
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
