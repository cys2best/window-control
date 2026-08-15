import { WebSocketServer } from 'ws';
import { createServer } from 'node:http';
import jwt from 'jsonwebtoken';

export async function createSignalingServer({ port = 8443, jwtSecret = null } = {}) {
  const httpServer = createServer();
  const wss = new WebSocketServer({ server: httpServer });

  // session id -> { engine: ws|null, viewer: ws|null, queue: { role, msg }[] }
  const sessions = new Map();

  function getSession(id) {
    if (!sessions.has(id)) {
      sessions.set(id, { engine: null, viewer: null, queue: [] });
    }
    return sessions.get(id);
  }

  function otherRole(role) {
    return role === 'engine' ? 'viewer' : 'engine';
  }

  wss.on('connection', (ws, req) => {
    const url = new URL(req.url, 'http://localhost');
    const sessionId = url.searchParams.get('session');
    const role = url.searchParams.get('role');
    const token = url.searchParams.get('token');

    if (!sessionId || (role !== 'engine' && role !== 'viewer')) {
      ws.close(1008, 'session and role (engine|viewer) query params required');
      return;
    }

    if (jwtSecret) {
      try {
        const payload = jwt.verify(token, jwtSecret);
        if (payload.session !== sessionId) {
          ws.close(1008, 'token session claim does not match session param');
          return;
        }
      } catch {
        ws.close(1008, 'invalid or missing token');
        return;
      }
    }

    const session = getSession(sessionId);
    if (session[role]) {
      ws.close(1008, 'role already taken');
      return;
    }
    session[role] = ws;

    // Flush any queued messages meant for this role.
    session.queue = session.queue.filter(({ role: destRole, msg }) => {
      if (destRole === role) {
        ws.send(msg);
        return false;
      }
      return true;
    });

    ws.on('message', (data) => {
      const target = session[otherRole(role)];
      const msg = data.toString();
      if (target && target.readyState === target.OPEN) {
        target.send(msg);
      } else {
        session.queue.push({ role: otherRole(role), msg });
        if (session.queue.length > 10) session.queue.shift();
      }
    });

    ws.on('close', () => {
      if (session[role] === ws) {
        session[role] = null;
      }
      if (!session.engine && !session.viewer) {
        sessions.delete(sessionId);
      }
    });
  });

  await new Promise((resolve) => httpServer.listen(port, resolve));
  const actualPort = httpServer.address().port;

  return {
    server: {
      close: () => new Promise((resolve) => {
        wss.close(() => httpServer.close(resolve));
      }),
    },
    port: actualPort,
  };
}

// Run standalone when executed directly (not imported by tests).
if (import.meta.url === `file://${process.argv[1]}`) {
  const port = process.env.PORT ? Number(process.env.PORT) : 8443;
  const jwtSecret = process.env.JWT_SECRET || null;
  createSignalingServer({ port, jwtSecret }).then(({ port: actualPort }) => {
    console.log(`Signaling server listening on port ${actualPort}`);
    if (!jwtSecret) console.warn('WARNING: JWT_SECRET not set, auth disabled');
  });
}
