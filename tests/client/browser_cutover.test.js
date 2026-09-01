const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const client = path.join(__dirname, '../../src/client');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function selection(serial, suffix) {
  return {
    id: `adb:${serial}`, serial, name: serial, w: 720, h: 1280,
    whep_url: `http://engine/${serial}/whep`, whep_token: `whep-${suffix || serial}`,
    signaling_url: null, signaling_token: null, ice_servers: [], generation: 1,
  };
}

function eventTarget(target) {
  const listeners = new Map();
  target.addEventListener = (type, listener) => {
    if (!listeners.has(type)) listeners.set(type, []);
    listeners.get(type).push(listener);
  };
  target.dispatch = (type, event = {}) => {
    for (const listener of listeners.get(type) || []) listener({ target, ...event });
  };
  return target;
}

function element(id) {
  const el = eventTarget({
    id, style: { display: '' }, className: '', dataset: {}, children: [], textContent: '',
    classList: { add() {}, remove() {}, contains() { return false; } },
    appendChild(child) { this.children.push(child); return child; },
    replaceWith() {}, querySelector() { return null; }, closest() { return null; },
    focus() {}, requestFullscreen() { return Promise.resolve(); },
    getBoundingClientRect() { return { left: 0, top: 0, width: 400, height: 200 }; },
  });
  if (id === 'stream-video') { el.videoWidth = 400; el.videoHeight = 200; }
  return el;
}

function createHarness() {
  const elements = new Map();
  for (const id of [
    'stream-container', 'stream-video', 'stream-img', 'unavailable-overlay', 'net-status',
    'keyboard-btn', 'keyboard-input', 'stats-overlay', 'fps-pill', 'screen-stream',
    'screen-list', 'windows-grid', 'switch-drawer-list', 'stream-title', 'right-toolbar',
    'settings-btn', 'settings-overlay', 'settings-close', 'stats-toggle', 'reconnect-btn',
    'back-btn', 'list-refresh-btn', 'switch-btn', 'switch-drawer', 'switch-drawer-scrim',
    'quality-opts', 'login-overlay', 'login-form', 'login-token', 'login-error',
  ]) elements.set(id, element(id));
  const selectPosts = [];
  const fetchCalls = [];
  const sessions = new Map();
  const connectedSelections = [];
  const pending = new Map();
  const responses = new Map();
  const closeGates = new Map();
  let authGateShown = false;
  const document = eventTarget({
    getElementById(id) { return elements.get(id) || null; },
    querySelectorAll() { return []; },
    createElement() { return element('created'); },
    fullscreenElement: null,
  });
  const context = eventTarget({
    console, Object, Promise, URL, Math, Date, JSON,
    setTimeout(callback) { callback(); return 1; }, clearTimeout() {},
    setInterval() { return 1; }, clearInterval() {},
    performance: { now: () => Date.now() },
    document,
    location: { protocol: 'http:', host: 'test' },
    localStorage: { getItem() { return null; }, setItem() {} },
    screen: { orientation: { lock() { return Promise.resolve(); }, unlock() {} } },
    Event: class Event { constructor(type) { this.type = type; } },
    fetch: async (url, options = {}) => {
      fetchCalls.push({ url, options });
      if (url === '/instances') return { ok: true, status: 200, json: async () => [] };
      if (String(url).endsWith('/select')) {
        const serial = String(url).split('/')[2];
        selectPosts.push({ serial, url, options });
        const queued = responses.get(serial);
        if (queued && queued.length) return queued.shift();
        const next = pending.get(serial);
        if (next) return next.promise;
        return { ok: true, status: 200, json: async () => selection(serial, `${serial}-${selectPosts.length}`) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  context.globalThis = context;
  context.window = context;
  context.matchMedia = () => ({ matches: false });
  context.wcAuthReady = Promise.resolve(true);
  context.wcShowAuthGate = () => { authGateShown = true; };
  function installManagerFake() {
    context.WindowControlEngineSessions = {
    createManager() {
      return {
        async connect(selected, callbacks) {
          connectedSelections.push(selected);
          const session = {
            selected, closed: false,
            pc: { connectionState: 'connected', getStats: async () => new Map() },
            input: {
              channel: { readyState: 'open' }, sent: [],
              send(message) { this.sent.push(message); return true; },
              dragStart(x, y) { this.sent.push({ type: 'drag_start', x, y }); },
              dragMove(x, y) { this.sent.push({ type: 'drag_move', x, y }); },
              dragEnd(x, y) { this.sent.push({ type: 'drag_end', x, y }); },
              scroll(x, y, dy) { this.sent.push({ type: 'scroll', x, y, dy }); },
            },
            async close() {
              this.closed = true;
              const gate = closeGates.get(selected.serial);
              if (gate) await gate.promise;
            },
          };
          session.failInput = () => callbacks.onState('failed');
          sessions.set(selected.serial, session);
          callbacks.onTrack({ id: selected.serial });
          callbacks.onState('connected');
          return session;
        },
        async close() { for (const session of sessions.values()) await session.close(); },
      };
    },
    };
  }
  for (const file of ['input_channel.js', 'engine_session.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(client, file), 'utf8'), context, { filename: file });
  }
  installManagerFake();
  for (const file of ['app.js', 'windows_panel.js']) {
    vm.runInNewContext(fs.readFileSync(path.join(client, file), 'utf8'), context, { filename: file });
  }
  return {
    context, selectPosts, fetchCalls, connectedSelections, sessions, pending,
    get activeSerial() { return elements.get('stream-video').srcObject && elements.get('stream-video').srcObject.id; },
    get authGateShown() { return authGateShown; },
    select(serial) { return context.selectWindow(`adb:${serial}`, serial); },
    reconnect() { return context.reconnectEngineInstance(); },
    selectDeferred(serial) {
      const gate = deferred();
      pending.set(serial, { promise: gate.promise.then(data => ({ ok: true, status: 200, json: async () => data })) });
      return gate;
    },
    respond(serial, status, body) {
      if (!responses.has(serial)) responses.set(serial, []);
      responses.get(serial).push({
        ok: status >= 200 && status < 300, status,
        json: async () => body || selection(serial, `${serial}-${status}`),
      });
    },
    sessionFor(serial) { return sessions.get(serial); },
    deferClose(serial) {
      const gate = deferred();
      closeGates.set(serial, gate);
      return gate;
    },
    flush: async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); },
  };
}

test('reconnect fetches fresh selection instead of reusing endpoint or token', async () => {
  const harness = createHarness();
  await harness.select('emulator-5554');
  await harness.reconnect();
  assert.equal(harness.selectPosts.length, 2);
  assert.notEqual(harness.connectedSelections[0].whep_token, harness.connectedSelections[1].whep_token);
});

test('rapid switch closes old session and stale completion cannot win', async () => {
  const harness = createHarness();
  const first = harness.selectDeferred('emulator-5554');
  const second = harness.selectDeferred('emulator-5556');
  const firstConnect = harness.select('emulator-5554');
  const secondConnect = harness.select('emulator-5556');
  second.resolve(selection('emulator-5556'));
  await secondConnect;
  first.resolve(selection('emulator-5554'));
  await Promise.all([firstConnect, harness.flush()]);
  assert.equal(harness.activeSerial, 'emulator-5556');
  assert.equal(harness.sessionFor('emulator-5554'), undefined);
});

test('attaches a ready replacement before waiting for predecessor cleanup', async () => {
  const harness = createHarness();
  await harness.select('emulator-5554');
  const cleanup = harness.deferClose('emulator-5554');
  const switching = harness.select('emulator-5556');
  await harness.flush();

  assert.equal(harness.activeSerial, 'emulator-5556');
  assert.equal(harness.sessionFor('emulator-5554').closed, true);
  cleanup.resolve();
  await switching;
});

test('requested selection never overwrites adopted identity on close or failure', async () => {
  const sameCard = createHarness();
  await sameCard.select('emulator-5554');
  await sameCard.context.closeEngineInstance();
  await sameCard.select('emulator-5554');
  assert.equal(sameCard.selectPosts.length, 2);

  const failedSwitch = createHarness();
  await failedSwitch.select('emulator-5554');
  failedSwitch.respond('emulator-5556', 500);
  await failedSwitch.select('emulator-5556');
  await failedSwitch.reconnect();
  assert.equal(failedSwitch.selectPosts.at(-1).serial, 'emulator-5554');
});

test('an initial unavailable selection remains reconnectable and settings controls open', async () => {
  const harness = createHarness();
  for (let attempt = 0; attempt < 5; attempt += 1) harness.respond('emulator-5554', 503);
  await harness.select('emulator-5554');
  await harness.reconnect();
  assert.equal(harness.selectPosts.length, 6);

  await harness.context._startApp();
  const settings = harness.context.document.getElementById('settings-btn');
  const overlay = harness.context.document.getElementById('settings-overlay');
  const toggle = harness.context.document.getElementById('stats-toggle');
  settings.dispatch('click');
  toggle.checked = true;
  toggle.dispatch('change');
  assert.equal(overlay.style.display, 'flex');
  assert.equal(harness.context.document.getElementById('stats-overlay').style.display, 'block');
});

test('selection opens no input WebSocket or WHEP prewarm and sends keys, IDR, and echo on the DataChannel', async () => {
  const harness = createHarness();
  await harness.select('emulator-5554');
  const session = harness.sessionFor('emulator-5554');
  harness.context.initKeyboard();
  harness.context.document.getElementById('keyboard-input').dispatch('keydown', { key: 'Enter', preventDefault() {} });
  session.pc.getStats = async () => new Map([['video', {
    type: 'inbound-rtp', kind: 'video', pliCount: 0, freezeCount: 0, framesDropped: 6,
  }]]);
  await harness.context._pollDecodeHealth(session);
  harness.context._sendInput({ type: 'echo', t: 12 });

  assert.deepEqual(session.input.sent.map(message => message.type), ['key', 'idr', 'echo']);
  assert.equal(harness.fetchCalls.some(call => String(call.url).includes('/whep-url')), false);
  assert.equal(harness.fetchCalls.some(call => String(call.url).includes('/input')), false);
});

test('pointer gestures use sender drag methods and normalize two-finger scroll pixels', async () => {
  const harness = createHarness();
  await harness.select('emulator-5554');
  harness.context.initTouch();
  const stream = harness.context.document.getElementById('stream-container');
  const event = (touches, changedTouches) => ({ touches, changedTouches, preventDefault() {} });
  const touch = (x, y) => ({ clientX: x, clientY: y });
  stream.dispatch('touchstart', event([touch(20, 20)], []));
  stream.dispatch('touchmove', event([touch(60, 20)], []));
  stream.dispatch('touchend', event([], [touch(80, 20)]));
  stream.dispatch('touchstart', event([touch(30, 20), touch(50, 20)], []));
  stream.dispatch('touchmove', event([touch(30, 60), touch(50, 60)], []));

  assert.deepEqual(harness.sessionFor('emulator-5554').input.sent, [
    { type: 'drag_start', x: 0.05, y: 0.1 },
    { type: 'drag_move', x: 0.15, y: 0.1 },
    { type: 'drag_end', x: 0.2, y: 0.1 },
    { type: 'scroll', x: 0.1, y: 0.3, dy: -0.2 },
  ]);
});

test('touch, mouse, and pointer taps send drag start/end without click messages', async () => {
  const tap = async (initializer, down, up, pointer) => {
    const harness = createHarness();
    await harness.select('emulator-5554');
    if (pointer) harness.context.PointerEvent = function PointerEvent() {};
    harness.context[initializer]();
    const stream = harness.context.document.getElementById('stream-container');
    stream.dispatch(down, { button: 0, pointerId: 7, clientX: 40, clientY: 60, touches: [{ clientX: 40, clientY: 60 }], preventDefault() {} });
    stream.dispatch(up, { button: 0, pointerId: 7, clientX: 40, clientY: 60, touches: [], changedTouches: [{ clientX: 40, clientY: 60 }], preventDefault() {} });
    return harness.sessionFor('emulator-5554').input.sent.map(message => message.type);
  };

  assert.deepEqual(await tap('initTouch', 'touchstart', 'touchend'), ['drag_start', 'drag_end']);
  assert.deepEqual(await tap('initMouse', 'mousedown', 'mouseup'), ['drag_start', 'drag_end']);
  assert.deepEqual(await tap('initPointer', 'pointerdown', 'pointerup', true), ['drag_start', 'drag_end']);
});

test('an input close reconnects with a fresh selection and a 401 returns to the auth gate', async () => {
  const reconnectHarness = createHarness();
  await reconnectHarness.select('emulator-5554');
  reconnectHarness.sessionFor('emulator-5554').failInput();
  await reconnectHarness.flush();
  assert.equal(reconnectHarness.selectPosts.length, 2);

  const authHarness = createHarness();
  authHarness.respond('emulator-5554', 401);
  await authHarness.select('emulator-5554');
  assert.equal(authHarness.authGateShown, true);
  assert.equal(authHarness.connectedSelections.length, 0);
});

test('a starting engine retries fresh select metadata with the bounded retry schedule', async () => {
  const harness = createHarness();
  for (let attempt = 0; attempt < 4; attempt += 1) harness.respond('emulator-5554', 503);
  await harness.select('emulator-5554');
  assert.equal(harness.selectPosts.length, 5);
  assert.equal(harness.connectedSelections.length, 1);
});
