import { test } from 'node:test';
import assert from 'node:assert';
import http from 'node:http';
import net from 'node:net';
import { WebSocket } from 'ws';
import { createTunnelServer } from './server.js';

function registerPc(port, token) {
  return new Promise((resolve, reject) => {
    const url = token
      ? `ws://localhost:${port}/__tunnel/register?token=${token}`
      : `ws://localhost:${port}/__tunnel/register`;
    const ws = new WebSocket(url);
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
  });
}

function get(port, path) {
  return new Promise((resolve, reject) => {
    http.get({ port, path }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString() }));
    }).on('error', reject);
  });
}

test('returns 502 when no PC is registered', async () => {
  const { server, port } = await createTunnelServer({ port: 0 });
  const res = await get(port, '/instances');
  assert.strictEqual(res.status, 502);
  await server.close();
});

test('relays a public HTTP request to the registered PC and back', async () => {
  const { server, port } = await createTunnelServer({ port: 0 });
  const pc = await registerPc(port);

  pc.on('message', (raw) => {
    const frame = JSON.parse(raw.toString());
    assert.strictEqual(frame.type, 'http_request');
    assert.strictEqual(frame.path, '/instances');
    pc.send(JSON.stringify({
      type: 'http_response', id: frame.id, status: 200,
      headers: { 'content-type': 'application/json' },
      body: Buffer.from('[]').toString('base64'),
    }));
  });

  const res = await get(port, '/instances');
  assert.strictEqual(res.status, 200);
  assert.strictEqual(res.body, '[]');

  pc.close();
  await server.close();
});

test('rejects registration with the wrong token', async () => {
  const { server, port } = await createTunnelServer({ port: 0, tunnelSecret: 'correct' });
  const ws = new WebSocket(`ws://localhost:${port}/__tunnel/register?token=wrong`);
  const closeCode = await new Promise((resolve) => ws.once('close', (code) => resolve(code)));
  assert.strictEqual(closeCode, 1008);
  await server.close();
});

test('accepts registration with the correct token', async () => {
  const { server, port } = await createTunnelServer({ port: 0, tunnelSecret: 'correct' });
  const pc = await registerPc(port, 'correct');
  pc.close();
  await server.close();
  assert.ok(true); // registerPc resolves only on 'open'
});

test('rejects a second registration while one PC is already connected', async () => {
  const { server, port } = await createTunnelServer({ port: 0 });
  const pc1 = await registerPc(port);
  const pc2 = new WebSocket(`ws://localhost:${port}/__tunnel/register`);
  const closeCode = await new Promise((resolve) => pc2.once('close', (code) => resolve(code)));
  assert.strictEqual(closeCode, 1008);
  pc1.close();
  await server.close();
});

test('server survives a client aborting the request mid-upload', async () => {
  const { server, port } = await createTunnelServer({ port: 0 });
  const pc = await registerPc(port);
  pc.on('message', () => {}); // this PC should never see a fully-drained request in this test

  // Send a chunked-encoding request but never send the terminating 0-length
  // chunk, then destroy the socket mid-stream. This makes `req` (the
  // IncomingMessage the server is reading via `for await`) emit an 'error',
  // which without a surrounding try/catch would crash the whole process.
  await new Promise((resolve) => {
    const socket = net.connect(port, 'localhost', () => {
      socket.write(
        'POST /upload HTTP/1.1\r\n' +
        'Host: localhost\r\n' +
        'Transfer-Encoding: chunked\r\n' +
        '\r\n' +
        '5\r\nhello\r\n'
      );
      setTimeout(() => {
        socket.destroy();
        resolve();
      }, 50);
    });
    socket.on('error', () => {}); // a local ECONNRESET/EPIPE here is expected too
  });

  // give the server's request handler a chance to catch the resulting error
  await new Promise((resolve) => setTimeout(resolve, 100));

  // the process must still be alive and the server still functional
  pc.close();
  await new Promise((resolve) => setTimeout(resolve, 50));
  const res = await get(port, '/still-alive');
  assert.strictEqual(res.status, 502); // no PC connected -> confirms the server survived
  assert.strictEqual(res.body, 'No PC connected');

  await server.close();
});

test('a new PC can register after the previous one disconnects', async () => {
  const { server, port } = await createTunnelServer({ port: 0 });
  const pc1 = await registerPc(port);
  pc1.close();
  await new Promise((resolve) => setTimeout(resolve, 50)); // let the close event settle
  const pc2 = await registerPc(port);
  pc2.close();
  await server.close();
  assert.ok(true);
});
