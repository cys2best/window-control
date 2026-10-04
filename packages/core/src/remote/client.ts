import type { Instance, PreviewSource } from "../api/client";
import type { ServerTarget } from "../api/target";
import { MAX_PENDING_REQUESTS, parseReply, trustedServiceUrl, validateAnswer, validateInput, validatePreview, validateSelection, type RemoteAnswer, type RemoteSelection, type RequestOptions } from "./protocol";

export type RemoteClientOptions = {
  requestId: () => string;
  trustedOrigin: string;
  WebSocketImpl?: any;
  now?: () => number;
  allowInsecureLocalhost?: boolean;
};
export class RemoteError extends Error {
  constructor(public code: string, message = code) { super(message); }
}
type Pending = {
  op: string; payload: Record<string, unknown>; resolve: (value: any) => void; reject: (reason: RemoteError) => void;
  validate: (result: Record<string, unknown>) => any; options?: RequestOptions;
  sent?: boolean; id?: string; cleanup?: () => void;
  timer?: ReturnType<typeof setTimeout>;
  session?: RemoteSelection;
};
const err = (code: string) => new RemoteError(code);
const MAX_WAIT = 30_000;

export function connectRemoteClient(target: Extract<ServerTarget, { kind: "remote" }>, token: string, onUnauthorized: (() => void) | undefined, options: RemoteClientOptions) {
  const origin = trustedServiceUrl(target.serviceUrl, options.trustedOrigin, options.allowInsecureLocalhost);
  if (!/^[0-9a-f]{32}$/.test(target.installationId) || !token || token.length > 256) throw new Error("invalid remote identity");
  const Socket = options.WebSocketImpl ?? WebSocket;
  const now = options.now ?? (() => performance.now());
  const url = `${origin.replace(/^http/, "ws")}/connect`;
  let socket: any = null;
  let epoch = 0;
  let authId: string | null = null;
  let authenticated = false;
  let disposed = false;
  const pending = new Set<Pending>();
  const ids = new Map<string, Pending>();
  const used = new Set<string>();
  // Share cancellation and explicit close, including a late adapter teardown.
  // Retain live promises and at most 256 recent settled exact identities.
  const closing = new Map<string, { promise: Promise<void>; settled: boolean }>();

  function freshId(): string {
    const id = options.requestId();
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) || used.has(id)) throw new Error("invalid or reused request ID");
    used.add(id); return id;
  }
  function send(ws: any, op: string, payload: Record<string, unknown>, id = freshId()) {
    ws.send(JSON.stringify({ v: 1, id, op, payload }));
    return id;
  }
  function finish(p: Pending, error?: RemoteError, value?: any) {
    if (!pending.delete(p)) return;
    if (p.id) ids.delete(p.id);
    if (p.timer) clearTimeout(p.timer);
    p.cleanup?.();
    error ? p.reject(error) : p.resolve(value);
  }
  function closeSession(session: RemoteSelection): Promise<void> {
    const key = `${session.session_id}:${session.generation}`;
    const existing = closing.get(key);
    if (existing) return existing.promise;
    if (closing.size >= 256) {
      for (const [old, entry] of closing) {
        if (entry.settled) { closing.delete(old); break; }
      }
      if (closing.size >= 256) return Promise.reject(err("busy"));
    }
    const entry = { promise: Promise.resolve(), settled: false };
    entry.promise = command("close", { session_id: session.session_id, generation: session.generation }, r => {
      if (Object.keys(r).length !== 1 || r.closed !== true) throw new Error("invalid close");
    });
    closing.set(key, entry);
    void entry.promise.then(() => { entry.settled = true; }, () => { entry.settled = true; });
    return entry.promise;
  }
  function closeKnown(session: RemoteSelection) { void closeSession(session).catch(() => {}); }
  function retire(code: string, ws = socket) {
    if (ws !== socket) return;
    epoch++; socket = null; authenticated = false; authId = null;
    try { ws?.close(); } catch {}
    for (const p of [...pending]) finish(p, err(code));
  }
  function cancel(p: Pending, code: string) {
    if (!pending.has(p)) return;
    if ((p.op === "select" || p.op === "renew") && p.sent) retire(code);
    else { finish(p, err(code)); if (p.session && p.sent) closeKnown(p.session); }
  }
  function dispatch(p: Pending) {
    if (!authenticated || !socket || socket.readyState !== 1 || !pending.has(p)) return;
    if (p.options?.deadline !== undefined) {
      const remaining = Math.min(MAX_WAIT, Math.floor(p.options.deadline - now()));
      if (remaining < 1) { cancel(p, "timeout"); return; }
      if (p.op === "negotiate") p.payload = { ...p.payload, timeout_ms: remaining };
    }
    try { p.id = send(socket, p.op, p.payload); p.sent = true; ids.set(p.id, p); }
    catch { retire("offline"); }
  }
  function open() {
    if (socket || disposed) return;
    const ws = new Socket(url); socket = ws; const mine = ++epoch;
    ws.onopen = () => {
      if (mine !== epoch) return;
      try { authId = send(ws, "viewer_auth", { installation_id: target.installationId, token }); }
      catch { retire("offline", ws); }
    };
    ws.onmessage = ({ data }: { data: string }) => {
      if (mine !== epoch) return;
      let reply: ReturnType<typeof parseReply>;
      try { reply = parseReply(data); }
      catch { retire("invalid_request", ws); return; }
      if (reply.id === authId) {
        if (!reply.ok) { if (reply.error?.code === "not_paired") onUnauthorized?.(); retire(reply.error?.code ?? "invalid_request", ws); return; }
        const result = reply.result!;
        if (Object.keys(result).length !== 2 || result.authenticated !== true || typeof result.viewer_id !== "string" || !result.viewer_id) { retire("invalid_request", ws); return; }
        authId = null; authenticated = true;
        for (const p of [...pending]) dispatch(p);
        return;
      }
      const p = ids.get(reply.id);
      if (!p) return;
      if (!reply.ok) {
        if (reply.error!.code === "not_paired") onUnauthorized?.();
        finish(p, new RemoteError(reply.error!.code, reply.error!.message)); return;
      }
      try {
        const value = p.validate(reply.result!);
        if (p.options?.deadline !== undefined && now() >= p.options.deadline) {
          if (p.op === "select") retire("timeout", ws);
          else { finish(p, err("timeout")); if (p.op === "renew") closeKnown(value as RemoteSelection); else if (p.session) closeKnown(p.session); }
        }
        else finish(p, undefined, value);
      } catch { retire("invalid_request", ws); }
    };
    ws.onclose = () => { if (mine === epoch) retire("offline", ws); };
    ws.onerror = () => { if (mine === epoch) retire("offline", ws); };
  }
  function command<T>(op: string, payload: Record<string, unknown>, validate: (r: Record<string, unknown>) => T, requestOptions?: RequestOptions, session?: RemoteSelection): Promise<T> {
    if (disposed) return Promise.reject(err("canceled"));
    if (requestOptions?.signal?.aborted) return Promise.reject(err("canceled"));
    if (requestOptions?.deadline !== undefined && (!Number.isFinite(requestOptions.deadline) || requestOptions.deadline <= now())) return Promise.reject(err("timeout"));
    if (pending.size >= MAX_PENDING_REQUESTS) return Promise.reject(err("busy"));
    return new Promise<T>((resolve, reject) => {
      const p: Pending = { op, payload, validate, options: requestOptions, session, resolve, reject };
      if (requestOptions?.signal) {
        const abort = () => cancel(p, "canceled");
        requestOptions.signal.addEventListener("abort", abort, { once: true });
        p.cleanup = () => requestOptions.signal?.removeEventListener("abort", abort);
      }
      pending.add(p); open(); dispatch(p);
      if (requestOptions?.deadline !== undefined && pending.has(p)) {
        p.timer = setTimeout(() => cancel(p, "timeout"), Math.max(0, requestOptions.deadline! - now()));
      }
    });
  }
  const selected = (r: Record<string, unknown>) => validateSelection(r);
  const simple = (r: Record<string, unknown>) => { if (Object.keys(r).length !== 1 || r.ok !== true) throw new Error("invalid result"); };
  const sessionPayload = (s: RemoteSelection) => ({ session_id: s.session_id, generation: s.generation });
  const client = {
    kind: "remote" as const,
    instances(): Promise<Instance[]> { return command("instances", {}, r => {
      if (Object.keys(r).length !== 1 || !Array.isArray(r.instances) || r.instances.length > 256) throw new Error("invalid instances");
      return r.instances.map((item: unknown) => {
        if (!item || typeof item !== "object") throw new Error("invalid instance");
        const d = item as Record<string, unknown>;
        if (typeof d.id !== "string" || typeof d.serial !== "string" || typeof d.name !== "string" || (d.title !== undefined && typeof d.title !== "string") || typeof d.active !== "boolean" || !Number.isSafeInteger(d.w) || !Number.isSafeInteger(d.h)) throw new Error("invalid instance");
        return { id: d.id, serial: d.serial, title: d.title ?? d.name, active: d.active, w: d.w as number, h: d.h as number, fps: typeof d.fps === "number" ? d.fps : undefined } as Instance;
      });
    }); },
    async ping(): Promise<number> { const start = now(); await this.instances(); return Math.max(0, now() - start); },
    async select(serial: string, opts?: RequestOptions): Promise<RemoteSelection> { validateInput(serial, "serial"); return command("select", { serial }, selected, opts); },
    preview(serial: string, opts?: RequestOptions): Promise<PreviewSource> { validateInput(serial, "serial"); return command("preview", { serial }, r => ({ uri: validatePreview(r) }), opts); },
    keyframe(serial: string): Promise<void> { validateInput(serial, "serial"); return command("keyframe", { serial }, simple); },
    setQuality(serial: string, tier: string): Promise<void> { validateInput(serial, "serial"); validateInput(tier, "tier"); return command("quality", { serial, tier }, r => { if (r.ok !== true || (Object.keys(r).length !== 1 && !(Object.keys(r).length === 2 && r.tier === tier))) throw new Error("invalid quality"); }); },
    async negotiate(selection: RemoteSelection, offer: string, opts: RequestOptions): Promise<RemoteAnswer> { validateInput(offer, "offer"); return command("negotiate", { ...sessionPayload(selection), offer, timeout_ms: MAX_WAIT }, r => { const answer = validateAnswer(r); if (answer.session_id !== selection.session_id || answer.generation !== selection.generation) throw new Error("mismatched answer"); return answer; }, opts, selection); },
    closeSession,
    renew(selection: RemoteSelection, opts?: RequestOptions): Promise<RemoteSelection> { return command("renew", sessionPayload(selection), selected, opts, selection); },
    dispose(): void { disposed = true; retire("canceled"); },
  };
  open();
  return client;
}
export type RemoteApiClient = ReturnType<typeof connectRemoteClient>;
