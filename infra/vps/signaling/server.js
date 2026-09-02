import { WebSocketServer } from 'ws';
import { createServer } from 'node:http';
import { createServer as createSecureServer } from 'node:https';
import { readFileSync } from 'node:fs';
import jwt from 'jsonwebtoken';

export async function createSignalingServer({ port = 8443, jwtSecret = null, tls = null } = {}) {
  const httpServer = tls ? createSecureServer(tls) : createServer();
  const wss = new WebSocketServer({ server: httpServer });

  // session id -> { engine: ws|null, viewer: ws|null }
  const sessions = new Map();

  function getSession(id) {
    if (!sessions.has(id)) {
      sessions.set(id, { engine: null, viewer: null });
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
        const payload = jwt.verify(token, jwtSecret, { algorithms: ['HS256'] });
        if (payload.session !== sessionId || payload.role !== role
            || typeof payload.exp !== 'number' || !Number.isFinite(payload.exp)) {
          ws.close(1008, 'token claims do not match session, role, and expiry requirements');
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

    ws.on('message', (data) => {
      const target = session[otherRole(role)];
      const msg = data.toString();
      if (target && target.readyState === target.OPEN) {
        target.send(msg);
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
  const tlsCertFile = process.env.SIGNALING_TLS_CERT_FILE;
  const tlsKeyFile = process.env.SIGNALING_TLS_KEY_FILE;
  const tlsPort = process.env.SIGNALING_TLS_PORT
    ? Number(process.env.SIGNALING_TLS_PORT) : null;

  if ([tlsCertFile, tlsKeyFile, tlsPort].some(Boolean)
      && ![tlsCertFile, tlsKeyFile, tlsPort].every(Boolean)) {
    throw new Error(
      'SIGNALING_TLS_CERT_FILE, SIGNALING_TLS_KEY_FILE, and SIGNALING_TLS_PORT must be set together',
    );
  }

  const servers = [createSignalingServer({ port, jwtSecret })];
  if (tlsPort) {
    servers.push(createSignalingServer({
      port: tlsPort,
      jwtSecret,
      tls: {
        cert: readFileSync(tlsCertFile),
        key: readFileSync(tlsKeyFile),
      },
    }));
  }

  Promise.all(servers).then(([plain, secure]) => {
    const actualPort = plain.port;
    console.log(`Signaling server listening on port ${actualPort}`);
    if (secure) console.log(`Secure signaling server listening on port ${secure.port}`);
    if (!jwtSecret) console.warn('WARNING: JWT_SECRET not set, auth disabled');
  });
}
