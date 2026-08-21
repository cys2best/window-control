// windows_panel.js — window list screen + window switching
let _windows = [];
let _activeId = null;

// Switch prefetch: request a keyframe for an instance the user is about to
// switch to (touchstart / hover), so the IDR is already in flight before the
// select's WHEP negotiates. Throttled per serial so touchstart + mouseenter (or
// a jittery hover) don't spam the encoder with reset requests.
const _kfPrefetchAt = {};
function prefetchKeyframe(serial) {
  if (!serial) return;
  const now = Date.now();
  if (now - (_kfPrefetchAt[serial] || 0) < 1500) return;
  _kfPrefetchAt[serial] = now;
  fetch(`/instances/${serial}/keyframe`, { method: 'POST' }).catch(() => {});
}

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
    // Switch prefetch: kick a keyframe the instant the user shows intent
    // (finger down / pointer over the tile), before the click's select() even
    // fires. Copy-mux has no ffmpeg GOP, so this source-side IDR is what a fresh
    // WHEP needs to paint — doing it now hides the IDR+encode time behind the
    // tap gesture, so the switch feels instant.
    card.addEventListener('touchstart', () => prefetchKeyframe(serial), { passive: true });
    card.addEventListener('mouseenter', () => prefetchKeyframe(serial));
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
  if (id === _activeId) return;                 // no-op switch
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
    if (data.signaling_url) {
      const ok = await initWebRTCPublic(id, data.signaling_url, data.name, _serial);
      if (!ok) {
        initWebRTC(id, data.whep_url, data.stun_url, _serial);
      }
    } else {
      initWebRTC(id, data.whep_url, data.stun_url, _serial);
    }
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
    // Prefetch a keyframe on intent so the drawer switch paints instantly.
    row.addEventListener('touchstart', () => prefetchKeyframe(w.serial), { passive: true });
    row.addEventListener('mouseenter', () => prefetchKeyframe(w.serial));
    list.appendChild(row);
  });
}

function initDrawer() {
  document.getElementById('back-btn').addEventListener('click', () => {
    showScreen('screen-list');
    fetchWindows();
  });

  document.getElementById('list-refresh-btn').addEventListener('click', fetchWindows);

  // Swipe up/down anywhere on the right toolbar switches instances.
  // Tracks over buttons too — once the finger moves past the threshold we
  // treat it as a swipe (not a tap): mark .rt-swiping so button :active
  // styling is suppressed, fire prev/next, and swallow the ensuing click.
  const rt = document.getElementById('right-toolbar');
  if (rt) {
    let _sy = null, _sx = null, _fired = false, _isSwipe = false;
    const THRESH = 30;

    rt.addEventListener('touchstart', e => {
      if (e.touches.length !== 1) { _sy = null; return; }
      _sy = e.touches[0].clientY;
      _sx = e.touches[0].clientX;
      _fired = false;
      _isSwipe = false;
    }, { passive: true });

    rt.addEventListener('touchmove', e => {
      if (_sy === null || e.touches.length !== 1) return;
      const dy = e.touches[0].clientY - _sy;
      const dx = e.touches[0].clientX - _sx;
      // Vertical intent: mark swiping early so the button doesn't light up.
      if (!_isSwipe && Math.abs(dy) > 8 && Math.abs(dy) > Math.abs(dx)) {
        _isSwipe = true;
        rt.classList.add('rt-swiping');
        e.preventDefault();                      // kill scroll + tap highlight
      }
      if (_isSwipe && !_fired && Math.abs(dy) >= THRESH) {
        _fired = true;
        if (dy < 0) selectNext();                // swipe up → next
        else selectPrev();                       // swipe down → prev
      }
    }, { passive: false });

    const endSwipe = e => {
      if (_isSwipe && e) {
        // Prevent the tap that would otherwise land on a button.
        e.preventDefault();
      }
      _sy = null;
      rt.classList.remove('rt-swiping');
      setTimeout(() => { _isSwipe = false; }, 0);
    };
    rt.addEventListener('touchend', endSwipe, { passive: false });
    rt.addEventListener('touchcancel', endSwipe, { passive: false });
    // Belt-and-suspenders: swallow a click that follows a swipe.
    rt.addEventListener('click', e => {
      if (_isSwipe) { e.preventDefault(); e.stopPropagation(); }
    }, true);
  }

  const switchBtn = document.getElementById('switch-btn');
  if (switchBtn) switchBtn.addEventListener('click', openSwitchDrawer);
  const scrim = document.getElementById('switch-drawer-scrim');
  if (scrim) scrim.addEventListener('click', closeSwitchDrawer);
}
