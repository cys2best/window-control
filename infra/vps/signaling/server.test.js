// infra/vps/signaling/server.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import { WebSocketServer, WebSocket } from 'ws';
import jwt from 'jsonwebtoken';
import { createSignalingServer } from './server.js';

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

test('rejects connection with missing token', async () => {
  const { server, port } = await createSignalingServer({ port: 0, jwtSecret: 'test-secret' });
  const ws = new WebSocket(`ws://localhost:${port}/?session=sess-1&role=engine`);
  const closeCode = await new Promise((resolve) => {
    ws.once('close', (code) => resolve(code));
  });
  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('rejects connection with token for a different session', async () => {
  const { server, port } = await createSignalingServer({ port: 0, jwtSecret: 'test-secret' });
  const token = jwt.sign({ session: 'sess-OTHER' }, 'test-secret');
  const ws = new WebSocket(`ws://localhost:${port}/?session=sess-1&role=engine&token=${token}`);
  const closeCode = await new Promise((resolve) => {
    ws.once('close', (code) => resolve(code));
  });
  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('accepts connection with valid token matching session', async () => {
  const { server, port } = await createSignalingServer({ port: 0, jwtSecret: 'test-secret' });
  const token = jwt.sign({
    session: 'sess-1',
    role: 'engine',
    exp: Math.floor(Date.now() / 1000) + 60,
  }, 'test-secret');
  const ws = await openClientWithToken(port, 'sess-1', 'engine', token);
  ws.close();
  server.close();
  // openClientWithToken resolves only on 'open' — if we got here, connection was accepted.
  assert.ok(true);
});

test('rejects connection where token role does not match requested role param', async () => {
  const { server, port } = await createSignalingServer({ port: 0, jwtSecret: 'test-secret' });
  const token = jwt.sign({ session: 'sess-1', role: 'viewer' }, 'test-secret');
  const ws = new WebSocket(`ws://localhost:${port}/?session=sess-1&role=engine&token=${token}`);
  const closeCode = await new Promise((resolve) => {
    ws.once('close', (code) => resolve(code));
  });
  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('accepts connection where token role matches requested role param', async () => {
  const { server, port } = await createSignalingServer({ port: 0, jwtSecret: 'test-secret' });
  const token = jwt.sign({
    session: 'sess-1',
    role: 'viewer',
    exp: Math.floor(Date.now() / 1000) + 60,
  }, 'test-secret');
  const ws = await openClientWithToken(port, 'sess-1', 'viewer', token);
  ws.close();
  server.close();
  assert.ok(true);
});

test('rejects an otherwise matching token without an expiry claim', async () => {
  const { server, port } = await createSignalingServer({ port: 0, jwtSecret: 'test-secret' });
  const token = jwt.sign({ session: 'sess-1', role: 'engine' }, 'test-secret');
  const ws = new WebSocket(`ws://localhost:${port}/?session=sess-1&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);
  ws.close();
  await server.close();
  assert.strictEqual(closeCode, 1008);
});

test('rejects an expired token with otherwise matching claims', async () => {
  const { server, port } = await createSignalingServer({ port: 0, jwtSecret: 'test-secret' });
  const token = jwt.sign({
    session: 'sess-1',
    role: 'engine',
    exp: Math.floor(Date.now() / 1000) - 1,
  }, 'test-secret');
  const ws = new WebSocket(`ws://localhost:${port}/?session=sess-1&role=engine&token=${token}`);
  const closeCode = await new Promise((resolve) => {
    ws.once('close', (code) => resolve(code));
  });
  assert.strictEqual(closeCode, 1008);
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
