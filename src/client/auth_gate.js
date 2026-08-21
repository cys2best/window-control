// Blocks the app behind a token prompt when the server has AUTH_TOKEN set.
// Runs before windows_panel.js/app.js so nothing else fires until unlocked.
(function () {
  const overlay = document.getElementById('login-overlay');
  const form = document.getElementById('login-form');
  const input = document.getElementById('login-token');
  const error = document.getElementById('login-error');

  function showGate() {
    document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
    overlay.style.display = 'flex';
    input.focus();
  }

  function hideGate() {
    overlay.style.display = 'none';
  }

  form.addEventListener('submit', async e => {
    e.preventDefault();
    error.style.display = 'none';
    try {
      const r = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: input.value }),
      });
      if (r.ok) {
        hideGate();
        window.dispatchEvent(new Event('wc-authenticated'));
        document.getElementById('screen-list').classList.add('active');
      } else {
        error.style.display = 'block';
        input.value = '';
        input.focus();
      }
    } catch (_) {
      error.style.display = 'block';
    }
  });

  // Probe with a cheap, always-present endpoint before the rest of the app
  // starts making real requests.
  window.wcAuthReady = fetch('/instances', { method: 'GET' })
    .then(r => {
      if (r.status === 401) { showGate(); return false; }
      return true;
    })
    .catch(() => true); // network error — let the app's own error handling deal with it
})();
