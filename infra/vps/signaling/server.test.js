// infra/vps/signaling/server.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, WebSocket } from 'ws';
import {
  generateKeyPair, SignJWT, jwtVerify,
  generateKeyPair as generateEdKeyPair, SignJWT as SignEdJWT, exportJWK as exportEdJWK,
} from 'jose';
import { createSignalingServer } from './server.js';

const tlsCa = readFileSync(fileURLToPath(new URL('../../../engine/test/tls/ca-cert.pem', import.meta.url)));
const tlsCert = readFileSync(fileURLToPath(new URL('../../../engine/test/tls/localhost-cert.pem', import.meta.url)));
const tlsKey = readFileSync(fileURLToPath(new URL('../../../engine/test/tls/localhost-key.pem', import.meta.url)));

function openClient(port, session, role) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://localhost:${port}/?session=${session}&role=${role}`);
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
  });
}

function openClientWithToken(port, session, role, token) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://localhost:${port}/?session=${session}&role=${role}&token=${token}`);
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
  });
}

function openSecureClient(port, session, role) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`wss://localhost:${port}/?session=${session}&role=${role}`, {
      ca: tlsCa,
    });
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
  });
}

function waitForCloseCode(ws, timeoutMs = 250) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), timeoutMs);
    ws.once('close', (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
}

test('relays a message from engine to viewer in the same session', async () => {
  const { server, port } = await createSignalingServer({ port: 0 });
  const engine = await openClient(port, 'sess-1', 'engine');
  const viewer = await openClient(port, 'sess-1', 'viewer');

  const received = new Promise((resolve) => {
    viewer.once('message', (data) => resolve(JSON.parse(data.toString())));
  });

  engine.send(JSON.stringify({ type: 'offer', sdp: 'fake-sdp' }));

  const msg = await received;
  assert.strictEqual(msg.type, 'offer');
  assert.strictEqual(msg.sdp, 'fake-sdp');

  engine.close();
  viewer.close();
  server.close();
});

test('relays over TLS when supplied a server certificate and key', async () => {
  const { server, port } = await createSignalingServer({
    port: 0,
    tls: { cert: tlsCert, key: tlsKey },
  });
  const engine = await openSecureClient(port, 'secure-session', 'engine');
  const viewer = await openSecureClient(port, 'secure-session', 'viewer');
  const received = new Promise((resolve) => {
    viewer.once('message', (data) => resolve(data.toString()));
  });

  engine.send('secure-message');

  assert.strictEqual(await received, 'secure-message');
  engine.close();
  viewer.close();
  await server.close();
});

test('does not leak messages across different sessions', async () => {
  const { server, port } = await createSignalingServer({ port: 0 });
  const engineA = await openClient(port, 'sess-A', 'engine');
  const viewerA = await openClient(port, 'sess-A', 'viewer');
  const engineB = await openClient(port, 'sess-B', 'engine');
  const viewerB = await openClient(port, 'sess-B', 'viewer');

  let viewerBGotMessage = false;
  viewerB.once('message', () => { viewerBGotMessage = true; });

  const viewerAReceived = new Promise((resolve) => {
    viewerA.once('message', (data) => resolve(JSON.parse(data.toString())));
  });

  engineA.send(JSON.stringify({ type: 'offer', sdp: 'sess-A-sdp' }));

  const msg = await viewerAReceived;
  assert.strictEqual(msg.sdp, 'sess-A-sdp');
  assert.strictEqual(viewerBGotMessage, false);

  engineA.close(); viewerA.close(); engineB.close(); viewerB.close();
  server.close();
});

test('does not deliver a message queued before the destination ever connected', async () => {
  const { server, port } = await createSignalingServer({ port: 0 });
  const engine = await openClient(port, 'sess-1', 'engine');

  engine.send(JSON.stringify({ type: 'offer', sdp: 'stale-offer' }));
  // Give the relay a tick to process the message with no viewer connected.
  await new Promise((resolve) => setTimeout(resolve, 50));

  const viewer = await openClient(port, 'sess-1', 'viewer');
  let viewerGotMessage = false;
  viewer.once('message', () => { viewerGotMessage = true; });

  // Give any (incorrect) queued-flush delivery a chance to arrive.
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.strictEqual(viewerGotMessage, false);

  engine.close();
  viewer.close();
  server.close();
});

test('rejects a second connection claiming an already-taken role and keeps the first alive', async () => {
  const { server, port } = await createSignalingServer({ port: 0 });
  const engine1 = await openClient(port, 'sess-1', 'engine');
  const engine2 = new WebSocket(`ws://localhost:${port}/?session=sess-1&role=engine`);

  const closeCode = await new Promise((resolve) => {
    engine2.once('close', (code) => resolve(code));
  });
  assert.strictEqual(closeCode, 1008);

  // The first engine connection must still be alive and able to relay to viewer.
  const viewer = await openClient(port, 'sess-1', 'viewer');
  const received = new Promise((resolve) => {
    viewer.once('message', (data) => resolve(JSON.parse(data.toString())));
  });
  engine1.send(JSON.stringify({ type: 'offer', sdp: 'still-alive' }));
  const msg = await received;
  assert.strictEqual(msg.sdp, 'still-alive');

  engine1.close();
  viewer.close();
  server.close();
});

async function makeSupabaseToken({ sub, privateKey, audience = 'authenticated', expiresInSeconds = 3600 }) {
  return new SignJWT({ sub })
    .setProtectedHeader({ alg: 'ES256' })
    .setIssuedAt()
    .setAudience(audience)
    .setExpirationTime(Math.floor(Date.now() / 1000) + expiresInSeconds)
    .sign(privateKey);
}

function makeVerifyViewerToken(publicKey) {
  return async (token) => {
    const { payload } = await jwtVerify(token, publicKey, {
      algorithms: ['ES256'], audience: 'authenticated',
    });
    return payload.sub;
  };
}

test('viewer with a valid Supabase JWT whose sub matches the session is accepted', async () => {
  const { publicKey, privateKey } = await generateKeyPair('ES256');
  const { server, port } = await createSignalingServer({
    port: 0, verifyViewerToken: makeVerifyViewerToken(publicKey),
  });
  const token = await makeSupabaseToken({ sub: 'user-1', privateKey });

  const ws = await openClientWithToken(port, 'user-1.instance0', 'viewer', token);
  // openClientWithToken resolves on 'open', which fires before the server's
  // async connection handler has a chance to reject and close the socket, so
  // assert the connection is actually kept alive (not closed with 1008).
  const closeCode = await waitForCloseCode(ws, 200);
  assert.strictEqual(closeCode, null);

  ws.close();
  server.close();
});

test('viewer whose sub does not match the session user id is rejected', async () => {
  const { publicKey, privateKey } = await generateKeyPair('ES256');
  const { server, port } = await createSignalingServer({
    port: 0, verifyViewerToken: makeVerifyViewerToken(publicKey),
  });
  const token = await makeSupabaseToken({ sub: 'user-2', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=viewer&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('viewer with an expired Supabase JWT is rejected', async () => {
  const { publicKey, privateKey } = await generateKeyPair('ES256');
  const { server, port } = await createSignalingServer({
    port: 0, verifyViewerToken: makeVerifyViewerToken(publicKey),
  });
  const token = await makeSupabaseToken({ sub: 'user-1', privateKey, expiresInSeconds: -10 });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=viewer&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('viewer connection is still trusted with no verification configured (dev/local relay)', async () => {
  // Matches the existing "no installLookup = trusted" behavior for engine
  // role: an operator who hasn't configured SUPABASE_URL gets the old
  // trusted-relay behavior, not a hard failure.
  const { server, port } = await createSignalingServer({ port: 0 });

  const ws = await openClient(port, 'sess-1', 'viewer');

  ws.close();
  server.close();
  assert.ok(true);
});

async function makeEngineToken({ session, privateKey, expiresInSeconds = 3600 }) {
  return new SignEdJWT({ session, role: 'engine' })
    .setProtectedHeader({ alg: 'EdDSA' })
    .setJti('1')
    .setExpirationTime(Math.floor(Date.now() / 1000) + expiresInSeconds)
    .sign(privateKey);
}

test('engine whose signature matches the registered install key is accepted', async () => {
  const { publicKey, privateKey } = await generateEdKeyPair('EdDSA');
  const jwk = await exportEdJWK(publicKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async (userId) =>
      userId === 'user-1' ? [{ public_key: jwk.x, user_id: 'user-1' }] : [],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey });

  const ws = await openClientWithToken(port, 'user-1.instance0', 'engine', token);
  // openClientWithToken resolves on 'open', which fires before the server's
  // async connection handler has a chance to reject and close the socket, so
  // assert the connection is actually kept alive (not closed with 1008).
  const closeCode = await waitForCloseCode(ws, 200);
  assert.strictEqual(closeCode, null);

  ws.close();
  server.close();
});

test('engine whose signature matches the second registered install key is accepted', async () => {
  const { publicKey: firstKey } = await generateEdKeyPair('EdDSA');
  const firstJwk = await exportEdJWK(firstKey);
  const { publicKey: secondKey, privateKey: secondPrivateKey } = await generateEdKeyPair('EdDSA');
  const secondJwk = await exportEdJWK(secondKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async (userId) =>
      (userId === 'user-1'
        ? [
          { public_key: firstJwk.x, user_id: 'user-1' },
          { public_key: secondJwk.x, user_id: 'user-1' },
        ]
        : []),
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey: secondPrivateKey });

  const ws = await openClientWithToken(port, 'user-1.instance0', 'engine', token);
  // openClientWithToken resolves on 'open', which fires before the server's
  // async connection handler has a chance to reject and close the socket, so
  // assert the connection is actually kept alive (not closed with 1008).
  const closeCode = await waitForCloseCode(ws, 200);
  assert.strictEqual(closeCode, null);

  ws.close();
  server.close();
});

test('engine whose signature does not match the registered install key is rejected', async () => {
  const { privateKey } = await generateEdKeyPair('EdDSA');
  const { publicKey: someoneElsesKey } = await generateEdKeyPair('EdDSA');
  const someoneElsesJwk = await exportEdJWK(someoneElsesKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [{ public_key: someoneElsesJwk.x, user_id: 'user-1' }],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('engine for a user_id with no installs row is rejected', async () => {
  const { privateKey } = await generateEdKeyPair('EdDSA');
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('engine token with a mismatched session claim is rejected even with a valid signature', async () => {
  const { publicKey, privateKey } = await generateEdKeyPair('EdDSA');
  const jwk = await exportEdJWK(publicKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [{ public_key: jwk.x, user_id: 'user-1' }],
  });
  const token = await makeEngineToken({ session: 'user-1.some-other-instance', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('expired engine token is rejected even with a valid signature', async () => {
  const { publicKey, privateKey } = await generateEdKeyPair('EdDSA');
  const jwk = await exportEdJWK(publicKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [{ public_key: jwk.x, user_id: 'user-1' }],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey, expiresInSeconds: -10 });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

// The standalone-run guards live behind `import.meta.url === argv[1]`, so they
// are unreachable from an in-process import. Spawn the real entrypoint to
// exercise them. Both throw before any listener binds, so the child exits
// immediately and never holds a port.
function runStandalone(env) {
  return new Promise((resolve) => {
    const entrypoint = fileURLToPath(new URL('./server.js', import.meta.url));
    const child = spawn(process.execPath, [entrypoint], {
      env: { ...process.env, PORT: '0', ...env },
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    let stderr = '';
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    const timer = setTimeout(() => child.kill(process.platform === 'win32' ? undefined : 'SIGKILL'), 5000);
    child.once('close', (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal, stderr });
    });
  });
}

test('standalone relay refuses to start with SUPABASE_URL but no service role key', async () => {
  const { code, stderr } = await runStandalone({
    SUPABASE_URL: 'https://project.supabase.co',
    SUPABASE_SERVICE_ROLE_KEY: '',
  });

  // Half-configured is the dangerous case: viewers verified, engine
  // registration wide open. It must fail closed at startup.
  assert.notStrictEqual(code, 0);
  assert.match(stderr, /SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set together/);
});

test('standalone relay refuses to start with a service role key but no SUPABASE_URL', async () => {
  const { code, stderr } = await runStandalone({
    SUPABASE_URL: '',
    SUPABASE_SERVICE_ROLE_KEY: 'service-role-key',
  });

  assert.notStrictEqual(code, 0);
  assert.match(stderr, /SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set together/);
});

test('standalone relay still starts unverified when neither Supabase variable is set', async () => {
  const { code, signal, stderr } = await runStandalone({
    SUPABASE_URL: '', SUPABASE_SERVICE_ROLE_KEY: '',
  });

  // Not a startup error: it binds and runs as the trusted dev/local relay,
  // so the 5s guard has to kill it. That it was killed (rather than exiting
  // on its own) is the proof it started.
  if (process.platform === 'win32') {
    // Windows TerminateProcess produces null signal
    assert.ok(signal === 'SIGKILL' || signal === null || code !== 0);
  } else {
    assert.strictEqual(signal, 'SIGKILL');
  }
  assert.doesNotMatch(stderr, /must be set together/);
});
