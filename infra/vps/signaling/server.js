import { WebSocketServer } from 'ws';
import { createServer } from 'node:http';
import { createServer as createSecureServer } from 'node:https';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import crypto from 'node:crypto';
import { createRemoteJWKSet, jwtVerify } from 'jose';

export async function createSignalingServer({
  port = 8443, supabaseUrl = null, serviceRoleKey = null,
  verifyViewerToken = null, installLookup = null, tls = null,
} = {}) {
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

  const resolveViewerToken = verifyViewerToken || (supabaseUrl ? (() => {
    const jwks = createRemoteJWKSet(
      new URL(`${supabaseUrl.replace(/\/$/, '')}/auth/v1/.well-known/jwks.json`),
    );
    return async (token) => {
      const { payload } = await jwtVerify(token, jwks, {
        algorithms: ['ES256'], audience: 'authenticated',
      });
      return payload.sub;
    };
  })() : null);
  const lookupInstall = installLookup || ((supabaseUrl && serviceRoleKey) ? (async (userId) => {
    const res = await fetch(
      `${supabaseUrl.replace(/\/$/, '')}/rest/v1/installs?user_id=eq.${encodeURIComponent(userId)}&select=public_key`,
      { headers: { apikey: serviceRoleKey, Authorization: `Bearer ${serviceRoleKey}` } },
    );
    if (!res.ok) throw new Error(`installs lookup failed: ${res.status}`);
    return res.json();
  }) : null);

  wss.on('connection', async (ws, req) => {
    const url = new URL(req.url, 'http://localhost');
    const sessionId = url.searchParams.get('session');
    const role = url.searchParams.get('role');
    const token = url.searchParams.get('token');

    if (!sessionId || (role !== 'engine' && role !== 'viewer')) {
      ws.close(1008, 'session and role (engine|viewer) query params required');
      return;
    }

    if (role === 'viewer') {
      if (resolveViewerToken) {
        let userId;
        try {
          userId = await resolveViewerToken(token);
        } catch {
          ws.close(1008, 'invalid or expired viewer token');
          return;
        }
        if (userId !== sessionId.split('.', 1)[0]) {
          ws.close(1008, "token does not match this session's account");
          return;
        }
      }
    } else if (lookupInstall) {
      // role === 'engine'
      const expectedUserId = sessionId.split('.', 1)[0];
      let rows;
      try {
        rows = await lookupInstall(expectedUserId);
      } catch {
        ws.close(1008, 'install lookup failed');
        return;
      }
      if (!rows.length) {
        ws.close(1008, 'no registered install for this account');
        return;
      }
      try {
        const [header, payload, signature] = token.split('.');
        const claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
        if (claims.session !== sessionId || claims.role !== 'engine'
            || typeof claims.exp !== 'number' || !Number.isFinite(claims.exp)
            || claims.exp < Date.now() / 1000) {
          throw new Error('claims mismatch');
        }
        const signedData = Buffer.from(`${header}.${payload}`);
        const signatureBuffer = Buffer.from(signature, 'base64url');
        const verified = rows.some((row) => {
          const publicKeyObject = crypto.createPublicKey({
            key: { kty: 'OKP', crv: 'Ed25519', x: row.public_key },
            format: 'jwk',
          });
          return crypto.verify(null, signedData, publicKeyObject, signatureBuffer);
        });
        if (!verified) throw new Error('bad signature');
      } catch {
        ws.close(1008, 'invalid engine registration token');
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
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = process.env.PORT ? Number(process.env.PORT) : 8443;
  const supabaseUrl = process.env.SUPABASE_URL || null;
  const serviceRoleKey = process.env.SUPABASE_SERVICE_ROLE_KEY || null;
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

  const servers = [createSignalingServer({ port, supabaseUrl, serviceRoleKey })];
  if (tlsPort) {
    servers.push(createSignalingServer({
      port: tlsPort,
      supabaseUrl,
      serviceRoleKey,
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
    if (!supabaseUrl || !serviceRoleKey) {
      console.warn('WARNING: SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY not set, engine auth disabled');
    }
    if (!supabaseUrl) console.warn('WARNING: SUPABASE_URL not set, viewer auth disabled');
  });
}
