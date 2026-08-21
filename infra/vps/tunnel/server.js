import { WebSocketServer } from 'ws';
import { createServer } from 'node:http';
import { randomUUID } from 'node:crypto';

const HOP_BY_HOP = new Set(['host', 'content-length', 'connection', 'transfer-encoding']);

function filterHeaders(headers) {
  return Object.fromEntries(
    Object.entries(headers).filter(([k]) => !HOP_BY_HOP.has(k.toLowerCase())));
}

export async function createTunnelServer({ port = 0, tunnelSecret = null } = {}) {
  const httpServer = createServer();
  const registerWss = new WebSocketServer({ noServer: true });

  let pcConn = null;
  const pendingHttp = new Map(); // stream id -> { resolve, reject }

  httpServer.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/__tunnel/register') {
      registerWss.handleUpgrade(req, socket, head, (ws) => registerWss.emit('connection', ws, req));
    } else {
      socket.destroy(); // WS-stream upgrades handled once Task 8 lands
    }
  });

  registerWss.on('connection', (ws, req) => {
    const url = new URL(req.url, 'http://localhost');
    if (tunnelSecret && url.searchParams.get('token') !== tunnelSecret) {
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
      } catch {
        return; // ignore malformed frames rather than crashing the connection handler
      }
      if (frame.type === 'http_response') {
        const pending = pendingHttp.get(frame.id);
        if (pending) {
          pendingHttp.delete(frame.id);
          pending.resolve(frame);
        }
      }
    });

    ws.on('close', () => {
      if (pcConn === ws) pcConn = null;
      for (const pending of pendingHttp.values()) pending.reject(new Error('PC disconnected'));
      pendingHttp.clear();
    });

    ws.on('error', () => {
      // 'close' always follows 'error' for ws connections; cleanup happens there.
    });
  });

  httpServer.on('request', async (req, res) => {
    if (!pcConn) {
      res.writeHead(502, { 'Content-Type': 'text/plain' });
      res.end('No PC connected');
      return;
    }

    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString('base64');

    const id = randomUUID();
    const responded = new Promise((resolve, reject) => {
      pendingHttp.set(id, { resolve, reject });
    });
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
  createTunnelServer({ port, tunnelSecret }).then(({ port: actualPort }) => {
    console.log(`Tunnel server listening on port ${actualPort}`);
    if (!tunnelSecret) console.warn('WARNING: TUNNEL_SECRET not set, tunnel registration unauthenticated');
  });
}
