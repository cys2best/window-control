const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const modulePath = path.join(__dirname, '../../src/client/engine_session.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function eventTarget(target) {
  const listeners = new Map();
  target.addEventListener = (type, listener) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(listener);
  };
  target.dispatch = (type, event) => {
    const payload = event || { type, target };
    for (const listener of listeners.get(type) || []) listener(payload);
    if (typeof target[`on${type}`] === 'function') target[`on${type}`](payload);
  };
  return target;
}

function response(status, headers, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => (headers || {})[name] || null },
    text: async () => body || 'answer-sdp',
  };
}

function fakeDeps() {
  const deps = {
    local: { calls: [], post: deferred() },
    public: { calls: [] },
    fetch: { calls: [] },
    deleteDeferred: null,
    flush: async () => {
      for (let index = 0; index < 10; index += 1) await Promise.resolve();
    },
  };
  deps.scenarios = [deps.local, deps.public];

  class FakePeerConnection {
    constructor(config) {
      this.scenario = deps.scenarios.shift();
      this.scenario.pc = this;
      this.config = config;
      this.calls = this.scenario.calls;
      this.iceGatheringState = 'complete';
      this.iceConnectionState = 'new';
      this.localDescription = null;
      this.remoteDescription = null;
      this.closed = false;
      eventTarget(this);
    }
    addTransceiver(kind, options) { this.calls.push(`addTransceiver:${kind}`); this.transceiverOptions = options; }
    createDataChannel(name, options) {
      this.calls.push(`createDataChannel:${name}`);
      if (this.scenario.createDataChannelError) throw this.scenario.createDataChannelError;
      this.channel = eventTarget({
        name, options, readyState: 'connecting', closed: false, send() {},
        close() {
          if (this.closed) return;
          this.closed = true;
          this.readyState = 'closed';
          this.dispatch('close');
        },
      });
      this.channel.scenario = this.scenario;
      this.scenario.channel = this.channel;
      return this.channel;
    }
    async createOffer() { this.calls.push('createOffer'); return { type: 'offer', sdp: `${this.scenario === deps.local ? 'local' : 'public'}-offer` }; }
    async setLocalDescription(description) { this.calls.push('setLocalDescription'); this.localDescription = description; }
    async setRemoteDescription(description) { this.calls.push('setRemoteDescription'); this.remoteDescription = description; }
    close() {
      if (this.closed) return;
      this.closed = true;
      this.iceConnectionState = 'closed';
      this.dispatch('iceconnectionstatechange');
    }
  }

  class FakeWebSocket {
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.sent = [];
      this.closed = false;
      deps.public.ws = this;
      eventTarget(this);
    }
    send(value) { this.sent.push(value); }
    open() { this.readyState = 1; this.dispatch('open'); }
    answer(sdp) { this.dispatch('message', { data: sdp, target: this }); }
    fail() { this.dispatch('error'); }
    close() {
      if (this.closed) return;
      this.closed = true;
      this.readyState = 3;
      this.dispatch('close');
    }
  }

  deps.fetchImpl = async (url, options) => {
    deps.fetch.calls.push({ url, options });
    if (options.method === 'DELETE') return deps.deleteDeferred ? deps.deleteDeferred.promise : response(204);
    return deps.local.post.promise;
  };
  deps.PeerConnection = FakePeerConnection;
  deps.WebSocketImpl = FakeWebSocket;
  deps.inputApi = {
    createSender(channel) {
      if (channel.scenario.senderError) throw channel.scenario.senderError;
      return { channel, closed: false, close() { this.closed = true; } };
    },
  };
  deps.local.resolvePost = (location, status) => deps.local.post.resolve(response(status || 201, { Location: location }, 'local-answer'));
  deps.local.rejectPost = (error) => deps.local.post.reject(error || new Error('local failed'));
  deps.local.becomeReady = () => becomeReady(deps.local);
  deps.public.becomeReady = () => becomeReady(deps.public);
  deps.publicOnly = () => { deps.scenarios = [deps.public]; };
  deps.holdDeletes = () => { deps.deleteDeferred = deferred(); };
  deps.resolveDelete = () => deps.deleteDeferred.resolve(response(204));
  return deps;
}

function becomeReady(scenario) {
  scenario.channel.readyState = 'open';
  scenario.channel.dispatch('open');
  scenario.pc.iceConnectionState = 'connected';
  scenario.pc.dispatch('iceconnectionstatechange');
  scenario.pc.dispatch('track', { track: { kind: 'video' }, streams: [{ id: 'stream' }] });
}

function localSelection() {
  return {
    name: 'pixel 8', whep_url: 'http://host/whep', whep_token: 'fresh-whep-token',
    signaling_url: null, signaling_token: null, ice_servers: [{ urls: 'stun:local' }],
  };
}

function publicSelection() {
  return {
    name: 'pixel 8 & friends', whep_url: null, whep_token: null,
    signaling_url: 'wss://signal.example/relay', signaling_token: 'jwt+/=?&', ice_servers: [{ urls: 'turn:public' }],
  };
}

function fullSelection() {
  return { ...localSelection(), signaling_url: 'wss://signal.example/relay', signaling_token: 'jwt+/=?&' };
}

function callbacks() {
  const states = [];
  const tracks = [];
  return { states, tracks, onState: (state) => states.push(state), onTrack: (stream) => tracks.push(stream), onInputMessage() {} };
}

function loadApi() {
  const inputSource = fs.readFileSync(path.join(__dirname, '../../src/client/input_channel.js'), 'utf8');
  const sessionSource = fs.readFileSync(modulePath, 'utf8');
  const context = { globalThis: { setTimeout, clearTimeout }, Object, Promise, URL, setTimeout, clearTimeout };
  vm.runInNewContext(inputSource, context, { filename: 'input_channel.js' });
  vm.runInNewContext(sessionSource, context, { filename: modulePath });
  return context.globalThis.WindowControlEngineSessions;
}

async function startLocal(deps, manager, selection) {
  const connecting = manager.connect(selection || localSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.becomeReady();
  return connecting;
}

async function startPublic(deps, manager, selection) {
  const connecting = manager.connect(selection || publicSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();
  return connecting;
}

test('creates input before offer and authenticates WHEP POST', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const connecting = manager.connect(localSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.becomeReady();
  await connecting;

  assert.deepEqual(deps.local.calls.slice(0, 3), ['addTransceiver:video', 'createDataChannel:input', 'createOffer']);
  assert.equal(deps.local.pc.transceiverOptions.direction, 'recvonly');
  assert.equal(deps.local.channel.options.ordered, true);
  assert.equal(deps.fetch.calls[0].options.headers.Authorization, 'Bearer fresh-whep-token');
});

test('uses the final video track, ICE, and input channel before adopting local session', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const callbacksUsed = callbacks();
  const connecting = manager.connect(localSelection(), callbacksUsed);
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.channel.readyState = 'open';
  deps.local.channel.dispatch('open');
  deps.local.pc.iceConnectionState = 'connected';
  deps.local.pc.dispatch('iceconnectionstatechange');
  await deps.flush();
  let settled = false;
  connecting.then(() => { settled = true; });
  await deps.flush();
  assert.equal(settled, false);
  deps.local.pc.dispatch('track', { track: { kind: 'video' }, streams: [{ id: 'video-stream' }] });
  const session = await connecting;

  assert.equal(session.kind, 'local');
  assert.equal(session.stream.id, 'video-stream');
  assert.equal(callbacksUsed.tracks[0].id, 'video-stream');
  assert.equal(session.input.channel, deps.local.channel);
});

test('does not adopt an audio track in place of the required video track', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager({ ...deps, timeoutMs: 0 });
  const connecting = manager.connect(localSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.channel.readyState = 'open';
  deps.local.channel.dispatch('open');
  deps.local.pc.iceConnectionState = 'connected';
  deps.local.pc.dispatch('iceconnectionstatechange');
  deps.local.pc.dispatch('track', { track: { kind: 'audio' }, streams: [{ id: 'audio-stream' }] });
  await deps.flush();
  let settled = false;
  connecting.then(() => { settled = true; });
  await deps.flush();
  assert.equal(settled, false);
  deps.local.pc.dispatch('track', { track: { kind: 'video' }, streams: [{ id: 'video-stream' }] });
  assert.equal((await connecting).kind, 'local');
});

test('does not adopt a track event that lacks a video track', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager({ ...deps, timeoutMs: 0 });
  const connecting = manager.connect(localSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.channel.readyState = 'open';
  deps.local.channel.dispatch('open');
  deps.local.pc.iceConnectionState = 'connected';
  deps.local.pc.dispatch('iceconnectionstatechange');
  deps.local.pc.dispatch('track', { streams: [{ id: 'missing-track' }] });
  await deps.flush();
  let settled = false;
  connecting.then(() => { settled = true; });
  await deps.flush();
  assert.equal(settled, false);
  deps.local.pc.dispatch('track', { track: { kind: 'video' }, streams: [{ id: 'video-stream' }] });
  assert.equal((await connecting).kind, 'local');
});

test('resolves a relative WHEP Location and deletes it on close', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const session = await startLocal(deps, manager);
  await session.close();

  assert.equal(deps.fetch.calls.at(-1).url, 'http://host/resource');
  assert.equal(deps.fetch.calls.at(-1).options.method, 'DELETE');
});

test('rejects a successful WHEP response without Location and cleans up its peer', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const connecting = manager.connect(localSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost(null);

  await assert.rejects(connecting, /Location/);
  assert.equal(deps.local.pc.closed, true);
});

test('classifies WHEP 401 as credential-expired', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const connecting = manager.connect(localSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/ignored', 401);

  await assert.rejects(connecting, (error) => error.code === 'credential-expired');
});

test('classifies WHEP 503 as capacity', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const connecting = manager.connect(localSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/ignored', 503);

  await assert.rejects(connecting, (error) => error.code === 'capacity');
});

test('encodes session, viewer role, and JWT in the public signaling URL', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  deps.publicOnly();
  const manager = api.createManager(deps);
  const connecting = manager.connect(publicSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  const url = new URL(deps.public.ws.url);
  assert.equal(url.searchParams.get('session'), 'pixel 8 & friends');
  assert.equal(url.searchParams.get('role'), 'viewer');
  assert.equal(url.searchParams.get('token'), 'jwt+/=?&');
  assert.deepEqual(deps.public.ws.sent, ['public-offer']);
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();
  const session = await connecting;
  assert.equal(session.kind, 'public');
  assert.equal(deps.public.ws.closed, true);
});

test('does not start public signaling for a half-configured public pair', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const selection = localSelection();
  selection.signaling_url = 'wss://signal.example/relay';
  const session = await startLocal(deps, manager, selection);

  assert.equal(session.kind, 'local');
  assert.equal(deps.public.ws, undefined);
});

test('race adopts first ready session and deletes the WHEP loser', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const connected = manager.connect(fullSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();
  const winner = await connected;
  deps.local.resolvePost('/whep/local-loser');
  await deps.flush();

  assert.equal(winner.kind, 'public');
  assert.equal(deps.fetch.calls.at(-1).url, 'http://host/whep/local-loser');
  assert.equal(deps.fetch.calls.at(-1).options.method, 'DELETE');
  assert.equal(deps.local.pc.closed, true);
});

test('manager close cancels a winner while loser cleanup is still pending', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  deps.holdDeletes();
  const manager = api.createManager(deps);
  const connecting = manager.connect(fullSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/local-resource');
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();
  await deps.flush();
  assert.equal(deps.fetch.calls.at(-1).options.method, 'DELETE');

  await manager.close();
  deps.resolveDelete();

  await assert.rejects(connecting, /closed/);
  assert.equal(deps.public.pc.closed, true);
});

test('a later connect supersedes a winner while loser cleanup is still pending', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  deps.holdDeletes();
  const manager = api.createManager(deps);
  const first = manager.connect(fullSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/local-resource');
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();
  await deps.flush();

  const replacementAttempt = { calls: [] };
  deps.scenarios.push(replacementAttempt);
  const replacement = manager.connect(publicSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('replacement-answer');
  await deps.flush();
  becomeReady(replacementAttempt);
  deps.resolveDelete();

  await assert.rejects(first, /superseded/);
  assert.equal((await replacement).kind, 'public');
});

test('allows a failed local transport to leave public able to win', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const connected = manager.connect(fullSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/ignored', 503);
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();

  assert.equal((await connected).kind, 'public');
});

test('a public signaling close after answer rejects before readiness', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  deps.publicOnly();
  const manager = api.createManager({ ...deps, timeoutMs: 0 });
  const connecting = manager.connect(publicSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.ws.close();
  deps.public.becomeReady();

  await assert.rejects(connecting, /Public signaling closed/);
  assert.equal(deps.public.pc.closed, true);
});

test('a synchronous local DataChannel failure cleans its peer and lets public win', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  deps.local.createDataChannelError = new Error('DataChannel setup failed');
  const manager = api.createManager(deps);
  const connecting = manager.connect(fullSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();

  assert.equal((await connecting).kind, 'public');
  assert.equal(deps.local.pc.closed, true);
});

test('a synchronous local input sender failure cleans its peer and lets public win', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  deps.local.senderError = new Error('Input sender setup failed');
  const manager = api.createManager(deps);
  const connecting = manager.connect(fullSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();

  assert.equal((await connecting).kind, 'public');
  assert.equal(deps.local.pc.closed, true);
});

test('rejects only after every configured transport fails', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const connected = manager.connect(fullSelection(), callbacks());
  await deps.flush();
  deps.local.resolvePost('/ignored', 503);
  await deps.flush();
  deps.public.ws.fail();

  await assert.rejects(connected, /All engine session attempts failed/);
  assert.equal(deps.local.pc.closed, true);
  assert.equal(deps.public.pc.closed, true);
});

test('closes an earlier in-flight connection when a later connect supersedes it', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const first = manager.connect(localSelection(), callbacks());
  await deps.flush();
  const second = manager.connect(publicSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  deps.public.ws.answer('public-answer');
  await deps.flush();
  deps.public.becomeReady();

  await assert.rejects(first, /superseded/);
  assert.equal((await second).kind, 'public');
  assert.equal(deps.local.pc.closed, true);
});

test('closes a late public answer after the manager is closed', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  deps.publicOnly();
  const manager = api.createManager(deps);
  const connecting = manager.connect(publicSelection(), callbacks());
  await deps.flush();
  deps.public.ws.open();
  await deps.flush();
  await manager.close();
  deps.public.ws.answer('late-answer');

  await assert.rejects(connecting, /closed/);
  assert.equal(deps.public.pc.remoteDescription, null);
  assert.equal(deps.public.pc.closed, true);
});

test('session close is idempotent and closes every owned resource once', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const session = await startLocal(deps, manager);
  await session.close();
  await session.close();

  assert.equal(deps.local.pc.closed, true);
  assert.equal(deps.local.channel.closed, true);
  assert.equal(deps.fetch.calls.filter((call) => call.options.method === 'DELETE').length, 1);
});

test('ice disconnected that does not recover within the grace period is treated as failed', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(Object.assign(deps, { disconnectedGraceMs: 10 }));
  const callbacksUsed = callbacks();
  const connecting = manager.connect(localSelection(), callbacksUsed);
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.becomeReady();
  await connecting;
  deps.local.pc.iceConnectionState = 'disconnected';
  deps.local.pc.dispatch('iceconnectionstatechange');
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.ok(callbacksUsed.states.includes('failed'));
});

test('ice disconnected that recovers within the grace period is not treated as failed', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(Object.assign(deps, { disconnectedGraceMs: 200 }));
  const callbacksUsed = callbacks();
  const connecting = manager.connect(localSelection(), callbacksUsed);
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.becomeReady();
  await connecting;
  deps.local.pc.iceConnectionState = 'disconnected';
  deps.local.pc.dispatch('iceconnectionstatechange');
  await new Promise((resolve) => setTimeout(resolve, 20));
  deps.local.pc.iceConnectionState = 'connected';
  deps.local.pc.dispatch('iceconnectionstatechange');
  await new Promise((resolve) => setTimeout(resolve, 250));

  assert.ok(!callbacksUsed.states.includes('failed'));
  assert.equal(deps.local.pc.closed, false);
});

test('input-channel failure after adoption notifies failure and closes the session', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const callbacksUsed = callbacks();
  const connecting = manager.connect(localSelection(), callbacksUsed);
  await deps.flush();
  deps.local.resolvePost('/resource');
  await deps.flush();
  deps.local.becomeReady();
  const session = await connecting;
  deps.local.channel.readyState = 'closed';
  deps.local.channel.dispatch('close');
  await deps.flush();

  assert.equal(session.pc.closed, true);
  assert.ok(callbacksUsed.states.includes('failed'));
});

test('reserves a ready replacement before its predecessor cleanup completes', async () => {
  const api = loadApi();
  const deps = fakeDeps();
  const manager = api.createManager(deps);
  const first = await startLocal(deps, manager);
  deps.holdDeletes();
  deps.scenarios = [deps.public];
  const replacing = manager.connect(localSelection(), callbacks());
  await deps.flush();
  deps.public.becomeReady();
  await deps.flush();

  let resolved = false;
  replacing.then(() => { resolved = true; });
  await deps.flush();
  assert.equal(resolved, true);
  assert.equal(deps.fetch.calls.filter(call => call.options.method === 'DELETE').length, 0);

  const closingFirst = first.close();
  assert.equal(deps.fetch.calls.filter(call => call.options.method === 'DELETE').length, 1);
  deps.resolveDelete();
  await closingFirst;
  await replacing;
});
