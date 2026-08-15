// infra/vps/signaling/server.test.js
import { test } from 'node:test';
import assert from 'node:assert';
import { WebSocketServer, WebSocket } from 'ws';
import { createSignalingServer } from './server.js';

function openClient(port, session, role) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://localhost:${port}/?session=${session}&role=${role}`);
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
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
