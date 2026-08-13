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
  showScreen('screen-stream');
  try {
    const r = await fetch(`/instances/${_serial}/select`, { method: 'POST' });
    const data = await r.json();
    // Each instance has its own always-live mediamtx path; the select response
    // carries that instance's WHEP URL. Switching is a fresh WHEP negotiation to
    // the new path — initWebRTC closes the old PeerConnection and opens one to
    // the new instance. No shared mux, no server-side repoint, no reader
    // teardown to wait out.
    setAdaptiveSerial(_serial);
    initWebRTC(id, data.whep_url, data.stun_url, _serial);
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

// ── Quick-switch drawer ──────────────────────────────────────────
function openSwitchDrawer() {
  renderSwitchList();
  const d = document.getElementById('switch-drawer');
  const s = document.getElementById('switch-drawer-scrim');
  if (s) s.style.display = 'block';
  if (d) d.classList.remove('closed');
}

function closeSwitchDrawer() {
  const d = document.getElementById('switch-drawer');
  const s = document.getElementById('switch-drawer-scrim');
  if (d) d.classList.add('closed');
  if (s) s.style.display = 'none';
}

function renderSwitchList() {
  const list = document.getElementById('switch-drawer-list');
  if (!list) return;
  list.innerHTML = '';
  _windows.forEach(w => {
    const row = document.createElement('button');
    row.className = 'switch-row' + (w.id === _activeId ? ' active' : '');
    // Small Android glyph + the instance title, mirroring the reference UI.
    row.innerHTML =
      '<svg class="switch-row-ico" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
      '<path d="M6 18a1 1 0 0 0 1 1h1v3a1 1 0 0 0 2 0v-3h4v3a1 1 0 0 0 2 0v-3h1a1 1 0 0 0 1-1V8H6v10zM3.5 8A1.5 1.5 0 0 0 2 9.5v5a1.5 1.5 0 0 0 3 0v-5A1.5 1.5 0 0 0 3.5 8zm17 0A1.5 1.5 0 0 0 19 9.5v5a1.5 1.5 0 0 0 3 0v-5A1.5 1.5 0 0 0 20.5 8zM15.53 2.16l1.3-1.3a.5.5 0 0 0-.7-.7l-1.48 1.48A5.98 5.98 0 0 0 12 1c-.96 0-1.86.22-2.66.62L7.87.14a.5.5 0 1 0-.7.7l1.3 1.3A5.99 5.99 0 0 0 6 7h12a5.99 5.99 0 0 0-2.47-4.84zM10 5H9V4h1v1zm5 0h-1V4h1v1z"/>' +
      '</svg><span class="switch-row-label"></span>';
    row.querySelector('.switch-row-label').textContent = w.title;
    row.addEventListener('click', () => {
      closeSwitchDrawer();
      if (w.id !== _activeId) selectWindow(w.id, w.serial);
    });
    list.appendChild(row);
  });
}

function initDrawer() {
  document.getElementById('back-btn').addEventListener('click', () => {
    showScreen('screen-list');
    fetchWindows();
  });

  document.getElementById('list-refresh-btn').addEventListener('click', fetchWindows);

  // Swipe up/down on the right toolbar switches instances (prev/next).
  const rt = document.getElementById('right-toolbar');
  if (rt) {
    let _swipeY = null;
    let _swipeFired = false;
    rt.addEventListener('touchstart', e => {
      if (e.target.closest('.rt-btn')) return;   // let buttons handle their taps
      if (e.touches.length !== 1) return;
      _swipeY = e.touches[0].clientY;
      _swipeFired = false;
    }, { passive: true });
    rt.addEventListener('touchmove', e => {
      if (_swipeY === null || _swipeFired || e.touches.length !== 1) return;
      const dy = e.touches[0].clientY - _swipeY;
      if (Math.abs(dy) < 40) return;             // threshold
      _swipeFired = true;
      if (dy < 0) selectNext();                  // swipe up → next
      else selectPrev();                         // swipe down → prev
    }, { passive: true });
    rt.addEventListener('touchend', () => { _swipeY = null; }, { passive: true });
  }

  const switchBtn = document.getElementById('switch-btn');
  if (switchBtn) switchBtn.addEventListener('click', openSwitchDrawer);
  const scrim = document.getElementById('switch-drawer-scrim');
  if (scrim) scrim.addEventListener('click', closeSwitchDrawer);
}
