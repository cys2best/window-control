// Blocks the app behind a login/register form when the server has
// Supabase auth configured. Runs before windows_panel.js/app.js so
// nothing else fires until unlocked.
(function () {
  const JWT_KEY = 'wc_jwt';
  const overlay = document.getElementById('login-overlay');
  const form = document.getElementById('login-form');
  const emailInput = document.getElementById('login-email');
  const passwordInput = document.getElementById('login-password');
  const submitBtn = document.getElementById('login-submit');
  const toggleBtn = document.getElementById('login-toggle-mode');
  const error = document.getElementById('login-error');

  let mode = 'sign-in'; // or 'sign-up'
  let supabaseUrl = '';
  let supabaseAnonKey = '';

  function showGate() {
    document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
    overlay.style.display = 'flex';
    emailInput.focus();
  }
  window.wcShowAuthGate = showGate;

  function hideGate() {
    overlay.style.display = 'none';
  }

  function setMode(next) {
    mode = next;
    submitBtn.textContent = mode === 'sign-in' ? 'Sign in' : 'Create account';
    toggleBtn.textContent = mode === 'sign-in'
      ? 'Need an account? Register' : 'Have an account? Sign in';
  }
  toggleBtn.addEventListener('click', () => setMode(mode === 'sign-in' ? 'sign-up' : 'sign-in'));

  window.wcFetch = function (path, init = {}) {
    const headers = new Headers(init.headers);
    const jwt = localStorage.getItem(JWT_KEY);
    if (jwt) headers.set('Authorization', `Bearer ${jwt}`);
    return fetch(path, { ...init, headers });
  };

  window.wcGetAccessToken = function () {
    return localStorage.getItem(JWT_KEY);
  };

  form.addEventListener('submit', async e => {
    e.preventDefault();
    error.style.display = 'none';
    const endpoint = mode === 'sign-in' ? 'token?grant_type=password' : 'signup';
    try {
      const r = await fetch(`${supabaseUrl}/auth/v1/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', apikey: supabaseAnonKey },
        body: JSON.stringify({ email: emailInput.value, password: passwordInput.value }),
      });
      const body = await r.json();
      if (r.ok && body.access_token) {
        localStorage.setItem(JWT_KEY, body.access_token);
        hideGate();
        window.dispatchEvent(new Event('wc-authenticated'));
        document.getElementById('screen-list').classList.add('active');
      } else {
        error.textContent = body.error_description || body.msg || 'Sign-in failed';
        error.style.display = 'block';
      }
    } catch (_) {
      error.textContent = 'Network error';
      error.style.display = 'block';
    }
  });

  async function init() {
    let cfg = { auth_enabled: false, supabase_url: '', supabase_anon_key: '' };
    try {
      cfg = await fetch('/auth/config').then(r => r.json());
    } catch (_) { /* fall through with auth disabled */ }
    supabaseUrl = cfg.supabase_url;
    supabaseAnonKey = cfg.supabase_anon_key;

    if (!cfg.auth_enabled) return true;

    return window.wcFetch('/instances', { method: 'GET' })
      .then(r => {
        if (r.status === 401) { showGate(); return false; }
        return true;
      })
      .catch(() => true);
  }

  window.wcAuthReady = init();
})();
