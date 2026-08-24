import { WebSocketServer } from 'ws';
import { createServer } from 'node:http';
import { randomUUID, timingSafeEqual } from 'node:crypto';

const HOP_BY_HOP = new Set(['host', 'content-length', 'connection', 'transfer-encoding']);

// The PC's httpx client waits up to 60s for the local app (a cold scrcpy/adb
// start behind POST /instances/{id}/select is genuinely slow -- see
// TUNNEL_HTTP_TIMEOUT in src/server/http_tunnel.py). Time out a little later
// than that, so a legitimately slow request is answered by the PC (with its
// real status) rather than being cut off first by this side.
const DEFAULT_REQUEST_TIMEOUT_MS = 70_000;

function filterHeaders(headers) {
  return Object.fromEntries(
    Object.entries(headers).filter(([k]) => !HOP_BY_HOP.has(k.toLowerCase())));
}

// Constant-time token comparison, mirroring the Python side's use of
// hmac.compare_digest. timingSafeEqual throws on unequal-length buffers, so
// the length check (itself not timing-safe, but length is not a secret) comes
// first.
function tokensMatch(provided, expected) {
  if (typeof provided !== 'string') return false;
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

export async function createTunnelServer({
  port = 0, tunnelSecret = null, requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
} = {}) {
  const httpServer = createServer();
  const registerWss = new WebSocketServer({ noServer: true });
  const publicWss = new WebSocketServer({ noServer: true });

  let pcConn = null;
  const pendingHttp = new Map(); // stream id -> { resolve, reject }
  const wsStreams = new Map(); // stream id -> browser-side ws

  httpServer.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/__tunnel/register') {
      registerWss.handleUpgrade(req, socket, head, (ws) => registerWss.emit('connection', ws, req));
    } else {
      publicWss.handleUpgrade(req, socket, head, (ws) => publicWss.emit('connection', ws, req));
    }
  });

  registerWss.on('connection', (ws, req) => {
    const url = new URL(req.url, 'http://localhost');
    // `tunnelSecret = null` means "accept any registration" -- kept for tests
    // and local runs only. The CLI entrypoint at the bottom of this file
    // refuses to start without a secret.
    if (tunnelSecret && !tokensMatch(url.searchParams.get('token'), tunnelSecret)) {
      ws.close(1008, 'invalid tunnel token');
      return;
    }
    if (pcConn) {
      ws.close(1008, 'a PC is already registered');
      return;
    }
    pcConn = ws;

    ws.on('message', (raw) => {
      let frame;
      try {
        frame = JSON.parse(raw.toString());
      } catch (err) {
        console.error('tunnel: received malformed frame from registered PC:', err.message);
        return; // ignore malformed frames rather than crashing the connection handler
      }
      if (frame.type === 'http_response') {
        const pending = pendingHttp.get(frame.id);
        if (pending) {
          pendingHttp.delete(frame.id);
          pending.resolve(frame);
        }
      } else if (frame.type === 'ws_message') {
        const browserWs = wsStreams.get(frame.id);
        // ws's send() is a silent no-op once readyState is CLOSING/CLOSED (it
        // only reports through an optional callback), but it *throws
        // synchronously* while CONNECTING -- and a throw from inside this
        // 'message' listener would take down the whole process. Socket-level
        // failures (ECONNRESET etc.) are emitted as 'error' instead and are
        // handled by the browserWs 'error' listener below.
        if (browserWs) {
          try {
            browserWs.send(frame.data);
          } catch (err) {
            console.error('tunnel: failed to relay ws_message to browser:', err.message);
          }
        }
      } else if (frame.type === 'ws_close') {
        const browserWs = wsStreams.get(frame.id);
        if (browserWs) { browserWs.close(); wsStreams.delete(frame.id); }
      }
      // ws_open_ack is informational only -- the browser WS is already open
      // by the time this arrives (opened eagerly on the public-side
      // connection event below), so no action needed here.
    });

    ws.on('close', () => {
      if (pcConn === ws) pcConn = null;
      for (const pending of pendingHttp.values()) pending.reject(new Error('PC disconnected'));
      pendingHttp.clear();
      for (const browserWs of wsStreams.values()) browserWs.close();
      wsStreams.clear();
    });

    ws.on('error', (err) => {
      // 'close' always follows 'error' for ws connections; cleanup happens there.
      console.error('tunnel: registered PC connection error:', err.message);
    });
  });

  publicWss.on('connection', (browserWs, req) => {
    if (!pcConn) { browserWs.close(1013, 'no PC connected'); return; }
    const id = randomUUID();
    wsStreams.set(id, browserWs);
    pcConn.send(JSON.stringify({
      type: 'ws_open', id, path: req.url, headers: filterHeaders(req.headers),
    }));

    browserWs.on('message', (data) => {
      if (pcConn) pcConn.send(JSON.stringify({ type: 'ws_message', id, data: data.toString() }));
    });
    browserWs.on('close', () => {
      wsStreams.delete(id);
      if (pcConn) pcConn.send(JSON.stringify({ type: 'ws_close', id }));
    });
    browserWs.on('error', (err) => {
      // An unhandled 'error' event throws out of ws and kills the process --
      // a single mobile-network TCP reset would otherwise take the tunnel
      // down for every user. 'close' always follows, so cleanup happens there.
      // Same convention as the registered-PC handler above.
      console.error('tunnel: browser connection error:', err.message);
    });
  });

  httpServer.on('request', async (req, res) => {
    try {
      if (!pcConn) {
        res.writeHead(502, { 'Content-Type': 'text/plain' });
        res.end('No PC connected');
        return;
      }

      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const body = Buffer.concat(chunks).toString('base64');

      const id = randomUUID();
      let timer = null;
      const responded = new Promise((resolve, reject) => {
        pendingHttp.set(id, { resolve, reject });
        // Without this, a PC that never answers (crashed forward, dropped
        // frame) leaves this entry -- and the browser's request behind it --
        // pending forever.
        timer = setTimeout(() => {
          if (pendingHttp.delete(id)) {
            reject(new Error(`no response from PC within ${requestTimeoutMs}ms`));
          }
        }, requestTimeoutMs);
        if (typeof timer.unref === 'function') timer.unref();
      }).finally(() => clearTimeout(timer));
      try {
        pcConn.send(JSON.stringify({
          type: 'http_request', id, method: req.method, path: req.url,
          headers: filterHeaders(req.headers), body,
        }));
      } catch (err) {
        pendingHttp.delete(id);
        res.writeHead(502, { 'Content-Type': 'text/plain' });
        res.end('Failed to reach PC');
        return;
      }

      try {
        const frame = await responded;
        res.writeHead(frame.status, filterHeaders(frame.headers));
        res.end(Buffer.from(frame.body, 'base64'));
      } catch {
        res.writeHead(502, { 'Content-Type': 'text/plain' });
        res.end('PC disconnected before responding');
      }
    } catch (err) {
      // Defends against e.g. the client aborting mid-upload, which makes the
      // `for await` body-drain loop above throw. Without this, that throw
      // would escape as an unhandled rejection and crash the whole process.
      console.error('tunnel: request handler error:', err.message);
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'text/plain' });
        res.end('Internal tunnel error');
      } else {
        res.destroy();
      }
    }
  });

  await new Promise((resolve) => httpServer.listen(port, resolve));
  const actualPort = httpServer.address().port;

  return {
    server: { close: () => new Promise((resolve) => httpServer.close(resolve)) },
    port: actualPort,
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const port = process.env.PORT ? Number(process.env.PORT) : 8444;
  const tunnelSecret = process.env.TUNNEL_SECRET || null;
  // This process is internet-facing: without a secret, ANY client could
  // register as "the PC" -- during the real PC's reconnect backoff, say -- and
  // become the origin the operator's browser talks to, harvesting AUTH_TOKEN
  // in cleartext at the next login. Refuse to start, mirroring the PC side,
  // where PUBLIC_UI_URL without TUNNEL_SECRET is a hard RuntimeError in
  // create_app().
  if (!tunnelSecret) {
    console.error('FATAL: TUNNEL_SECRET is not set. Refusing to start an ' +
      'unauthenticated tunnel server: any client could register as the PC. ' +
      'Set TUNNEL_SECRET (see infra/vps/tunnel/README.md) and restart.');
    process.exit(1);
  }
  createTunnelServer({ port, tunnelSecret }).then(({ port: actualPort }) => {
    console.log(`Tunnel server listening on port ${actualPort}`);
  });
}
