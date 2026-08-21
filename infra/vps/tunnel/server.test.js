import { test } from 'node:test';
import assert from 'node:assert';
import http from 'node:http';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { WebSocket } from 'ws';
import { createTunnelServer } from './server.js';

const SERVER_PATH = fileURLToPath(new URL('./server.js', import.meta.url));

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

function openBrowserWs(port, path) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://localhost:${port}${path}`);
    ws.once('open', () => resolve(ws));
    ws.once('error', reject);
  });
}

test('proxies a browser /input WebSocket to the registered PC', async () => {
  const { server, port } = await createTunnelServer({ port: 0 });
  const pc = await registerPc(port);

  let openFrame = null;
  const pcGotOpen = new Promise((resolve) => {
    pc.on('message', (raw) => {
      const frame = JSON.parse(raw.toString());
      if (frame.type === 'ws_open') {
        openFrame = frame;
        resolve();
        pc.send(JSON.stringify({ type: 'ws_open_ack', id: frame.id }));
      } else if (frame.type === 'ws_message') {
        // Echo back with a prefix, proving round-trip through the PC side.
        pc.send(JSON.stringify({ type: 'ws_message', id: frame.id, data: `echo:${frame.data}` }));
      }
    });
  });

  const browserWs = await openBrowserWs(port, '/input');
  await pcGotOpen;
  assert.strictEqual(openFrame.path, '/input');

  const browserGotEcho = new Promise((resolve) => {
    browserWs.once('message', (data) => resolve(data.toString()));
  });
  browserWs.send('tap');
  assert.strictEqual(await browserGotEcho, 'echo:tap');

  browserWs.close();
  pc.close();
  await server.close();
});

test('closes the browser WebSocket when no PC is connected', async () => {
  const { server, port } = await createTunnelServer({ port: 0 });
  const ws = new WebSocket(`ws://localhost:${port}/input`);
  const closeCode = await new Promise((resolve) => ws.once('close', (code) => resolve(code)));
  assert.strictEqual(closeCode, 1013);
  await server.close();
});

test('rejects a same-length wrong token (timing-safe compare does not throw)', async () => {
  // timingSafeEqual throws on unequal-length buffers; 'wrongxx' is the same
  // length as 'correct', so this exercises the compare itself rather than the
  // length guard. Either way the registration must be refused, not crash.
  const { server, port } = await createTunnelServer({ port: 0, tunnelSecret: 'correct' });
  const ws = new WebSocket(`ws://localhost:${port}/__tunnel/register?token=wrongxx`);
  const closeCode = await new Promise((resolve) => ws.once('close', (code) => resolve(code)));
  assert.strictEqual(closeCode, 1008);
  await server.close();
});

test('responds 502 instead of hanging when the PC never answers', async () => {
  // Regression test: pendingHttp entries had no timeout, so a request the PC
  // never responded to left the browser hanging forever.
  const { server, port } = await createTunnelServer({ port: 0, requestTimeoutMs: 250 });
  const pc = await registerPc(port);
  pc.on('message', () => {}); // deliberately never sends an http_response

  const started = Date.now();
  const res = await get(port, '/instances');
  const elapsed = Date.now() - started;

  assert.strictEqual(res.status, 502);
  assert.ok(elapsed >= 200, `responded too early (${elapsed}ms)`);
  assert.ok(elapsed < 5000, `responded too late (${elapsed}ms)`);

  pc.close();
  await server.close();
});

test('survives an erroring browser WebSocket connection', async () => {
  // Regression test: publicWss connections had no 'error' handler. In ws, an
  // 'error' event with no listener throws out of the EventEmitter and takes
  // the whole tunnel process down -- for every user, not just the one whose
  // connection misbehaved. Verified vector (ws 8.21): a malformed frame makes
  // the Receiver emit 'error' on the server-side WebSocket
  // (websocket.js receiverOnError). Note socket-level failures (ECONNRESET)
  // do NOT go this route in ws 8.21 -- socketOnError just destroys the socket
  // -- so this, not a TCP reset, is the crash the handler prevents.
  const { server, port } = await createTunnelServer({ port: 0 });
  const pc = await registerPc(port);
  pc.on('message', () => {});

  const browserWs = await openBrowserWs(port, '/input');
  browserWs.on('error', () => {}); // the client end sees the teardown too
  // FIN + RSV1 set, opcode text, masked, zero-length: "RSV1 must be clear".
  browserWs._socket.write(Buffer.from([0xC1, 0x80, 1, 2, 3, 4]));

  await new Promise((resolve) => setTimeout(resolve, 150));

  // The process is still alive and the server still serving: a second browser
  // connection is proxied to the PC as normal.
  const pcGotOpen = new Promise((resolve) => {
    pc.on('message', (raw) => {
      const frame = JSON.parse(raw.toString());
      if (frame.type === 'ws_open') resolve(frame);
    });
  });
  const second = await openBrowserWs(port, '/input');
  const openFrame = await pcGotOpen;
  assert.strictEqual(openFrame.path, '/input');

  second.close();
  pc.close();
  await server.close();
});

function runCli(env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [SERVER_PATH], {
      env: { ...process.env, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (c) => { stdout += c.toString(); });
    child.stderr.on('data', (c) => { stderr += c.toString(); });
    const killTimer = setTimeout(() => child.kill('SIGKILL'), 3000);
    child.on('exit', (code, signal) => {
      clearTimeout(killTimer);
      resolve({ code, signal, stdout, stderr });
    });
  });
}

test('CLI refuses to start without TUNNEL_SECRET', async () => {
  // Regression test: an unset TUNNEL_SECRET used to warn and start anyway,
  // leaving an internet-facing server that accepts a registration from anyone.
  const { code, stderr } = await runCli({ TUNNEL_SECRET: '', PORT: '0' });
  assert.strictEqual(code, 1);
  assert.match(stderr, /TUNNEL_SECRET/);
});

test('CLI starts when TUNNEL_SECRET is set', async () => {
  const { signal, stdout } = await runCli({ TUNNEL_SECRET: 'a-real-secret', PORT: '0' });
  // It stays up until the harness kills it, and reported a listening port.
  assert.strictEqual(signal, 'SIGKILL');
  assert.match(stdout, /Tunnel server listening on port/);
});
