// windows_panel.js — swipe drawer + window switching
let _windows = [];
let _activeId = null;
let _thumbTimers = {};

function initDrawer() {
  const drawer = document.getElementById('drawer');
  const handle = document.getElementById('drawer-handle');

  handle.addEventListener('click', () => drawer.classList.toggle('open'));

  // swipe up to open, swipe down to close
  let startY = 0;
  document.addEventListener('touchstart', e => { startY = e.touches[0].clientY; }, { passive: true });
  document.addEventListener('touchend', e => {
    const dy = startY - e.changedTouches[0].clientY;
    if (dy > 60) drawer.classList.add('open');
    if (dy < -60) drawer.classList.remove('open');
  }, { passive: true });
}

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
    row.addEventListener('click', () => selectWindow(w.id));
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
    document.getElementById('drawer').classList.remove('open');
  } catch (_) {}
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
