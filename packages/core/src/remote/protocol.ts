import type { IceServer } from "../api/client";

export type RemoteSelection = {
  kind: "remote"; ok: true; id: string; serial: string; name: string;
  w: number; h: number; tier: string; session_id: string; generation: number;
  ice_servers: IceServer[]; expires_at: number; renew_after: 3300;
  relay_available: boolean;
};
export type RemoteAnswer = { answer: string; session_id: string; generation: number };
export type RequestOptions = { signal?: AbortSignal; deadline?: number };

export const MAX_FRAME_BYTES = 1024 * 1024;
export const MAX_SDP_BYTES = 128 * 1024;
export const MAX_PREVIEW_BYTES = 384 * 1024;
export const MAX_PENDING_REQUESTS = 32;
const ERROR_CODES = new Set(["not_paired", "offline", "expired_pairing", "invalid_request", "busy", "stale_generation", "unavailable", "relay_unavailable", "quota_exceeded", "timeout", "canceled"]);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ICE_URL = /^(?:stun|stuns|turn|turns):(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?(?:\?transport=(?:udp|tcp))?$/;
const byteLength = (s: string) => {
  let bytes = 0;
  for (const char of s) { const n = char.codePointAt(0)!; bytes += n < 0x80 ? 1 : n < 0x800 ? 2 : n < 0x10000 ? 3 : 4; }
  return bytes;
};
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const fields = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => k in v);
const bounded = (v: unknown, limit: number) => typeof v === "string" && v.length > 0 && byteLength(v) <= limit;
const positiveInt = (v: unknown) => Number.isSafeInteger(v) && (v as number) > 0;

// JSON.parse silently accepts duplicate keys; reject them before parsing.
export function parseReply(raw: string): { v: 1; id: string; ok: boolean; result?: Record<string, unknown>; error?: { code: string; message: string } } {
  if (byteLength(raw) > MAX_FRAME_BYTES) throw new Error("oversized frame");
  let duplicate = false;
  const stack: Set<string>[] = [];
  let inString = false, escape = false, key = "", readingKey = false;
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i];
    if (inString) { if (escape) { if (readingKey) key += c; escape = false; } else if (c === "\\") { if (readingKey) key += c; escape = true; } else if (c === '"') { inString = false; if (readingKey) { const decoded = JSON.parse('"' + key + '"') as string; const set = stack[stack.length - 1]; if (set.has(decoded)) duplicate = true; set.add(decoded); readingKey = false; } } else if (readingKey) key += c; continue; }
    if (c === '{') stack.push(new Set());
    else if (c === '}') stack.pop();
    else if (c === '"') { inString = true; let j = i - 1; while (j >= 0 && /\s/.test(raw[j])) j--; readingKey = (raw[j] === '{' || raw[j] === ',') && stack.length > 0 && raw.slice(i).match(/^"(?:\\.|[^"\\])*"\s*:/) !== null; key = ""; }
  }
  if (duplicate) throw new Error("duplicate JSON key");
  const frame: unknown = JSON.parse(raw);
  if (!object(frame) || frame.v !== 1 || !bounded(frame.id, 128) || !UUID.test(frame.id as string) || typeof frame.ok !== "boolean") throw new Error("invalid reply");
  if (frame.ok) {
    if (!fields(frame, ["v", "id", "ok", "result"]) || !object(frame.result)) throw new Error("invalid result");
    return frame as ReturnType<typeof parseReply>;
  }
  if (!fields(frame, ["v", "id", "ok", "error"]) || !object(frame.error) || !fields(frame.error, ["code", "message"]) || !ERROR_CODES.has(frame.error.code as string) || typeof frame.error.message !== "string") throw new Error("invalid error");
  return frame as ReturnType<typeof parseReply>;
}

export function validateSelection(v: unknown): RemoteSelection {
  const keys = ["ok", "id", "serial", "name", "w", "h", "tier", "session_id", "generation", "ice_servers", "expires_at", "renew_after", "relay_available"];
  if (!object(v) || !fields(v, keys) || v.ok !== true || !["id", "serial", "name", "tier"].every(k => bounded(v[k], 128)) || !bounded(v.session_id, 128) || !UUID.test(v.session_id as string) || !positiveInt(v.w) || !positiveInt(v.h) || !positiveInt(v.generation) || !positiveInt(v.expires_at) || v.renew_after !== 3300 || typeof v.relay_available !== "boolean" || !Array.isArray(v.ice_servers) || v.ice_servers.length > 16) throw new Error("invalid selection");
  for (const server of v.ice_servers) {
    if (!object(server) || !Array.isArray(server.urls) || !server.urls.length || server.urls.length > 16 || !server.urls.every((u: unknown) => typeof u === "string" && u.length <= 2048 && ICE_URL.test(u))) throw new Error("invalid ICE server");
    const turn = server.urls.some((u: string) => u.startsWith("turn:" ) || u.startsWith("turns:"));
    if (turn ? (!fields(server, ["urls", "username", "credential"]) || !bounded(server.username, 4096) || !bounded(server.credential, 4096)) : !fields(server, ["urls"])) throw new Error("invalid ICE authentication");
  }
  return { ...v, kind: "remote" } as RemoteSelection;
}

export function validateAnswer(v: unknown): RemoteAnswer {
  if (!object(v) || !fields(v, ["answer", "session_id", "generation"]) || !bounded(v.answer, MAX_SDP_BYTES) || !bounded(v.session_id, 128) || !UUID.test(v.session_id as string) || !positiveInt(v.generation)) throw new Error("invalid answer");
  return v as RemoteAnswer;
}

export function validatePreview(v: unknown): string {
  if (!object(v) || !fields(v, ["mime", "data_base64"]) || v.mime !== "image/jpeg" || typeof v.data_base64 !== "string" || v.data_base64.length > Math.ceil(MAX_PREVIEW_BYTES / 3) * 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(v.data_base64) || v.data_base64.length * 3 / 4 - (v.data_base64.endsWith("==") ? 2 : v.data_base64.endsWith("=") ? 1 : 0) > MAX_PREVIEW_BYTES) throw new Error("invalid preview");
  return `data:image/jpeg;base64,${v.data_base64}`;
}

export function validateInput(value: string, key: "serial" | "tier" | "offer" | "handle" | "code" | "device_name"): void {
  if (key === "offer") { if (!bounded(value, MAX_SDP_BYTES)) throw new Error("invalid offer"); return; }
  if (key === "code") { if (!/^\d{6}$/.test(value)) throw new Error("invalid code"); return; }
  if (key === "device_name") { if (typeof value !== "string" || value.length > 128) throw new Error("invalid name"); return; }
  if (typeof value !== "string" || value.length < (key === "handle" ? 22 : 1) || value.length > 128 || !/^[A-Za-z0-9_.:-]+$/.test(value)) throw new Error(`invalid ${key}`);
}

export function trustedServiceUrl(serviceUrl: string, trustedOrigin: string, allowInsecureLocalhost = false): string {
  const service = new URL(serviceUrl), trusted = new URL(trustedOrigin);
  const local = allowInsecureLocalhost && service.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(service.hostname);
  if ((service.protocol !== "https:" && !local) || service.origin !== trusted.origin || service.username || service.password || service.search || service.hash || service.pathname !== "/") throw new Error("untrusted service URL");
  return service.origin;
}
