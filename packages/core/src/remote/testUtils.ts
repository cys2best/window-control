// Transport double: the real client owns all auth, bounds and lifecycle logic.
export class FakeSocket {
  static sockets: FakeSocket[] = [];
  readyState = 0;
  sent: any[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.sockets.push(this); }
  send(raw: string) { if (this.readyState !== 1) throw new Error("closed"); this.sent.push(JSON.parse(raw)); }
  open() { this.readyState = 1; this.onopen?.(); }
  reply(result: any, frame = this.sent[this.sent.length - 1]) {
    this.raw(JSON.stringify({ v: 1, id: frame.id, ok: true, result }));
  }
  error(code: string, frame = this.sent[this.sent.length - 1]) {
    this.raw(JSON.stringify({ v: 1, id: frame.id, ok: false, error: { code, message: "error" } }));
  }
  raw(data: string) { this.onmessage?.({ data }); }
  close() { this.readyState = 3; this.onclose?.({ code: 1000 }); }
}
export const installationId = "a".repeat(32);
export const target = { kind: "remote" as const, serviceUrl: "https://relay.example", installationId };
export const selection = {
  ok: true as const, id: "adb:A", serial: "A", name: "Alpha", w: 1920, h: 1080, tier: "1080",
  session_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", generation: 1,
  ice_servers: [{ urls: ["stun:stun.example:3478"] }], expires_at: 3600, renew_after: 3300 as const,
  relay_available: false,
};
export function options(extra = {}) {
  let id = 0;
  return { requestId: () => `00000000-0000-4000-8000-${(++id).toString().padStart(12, "0")}`,
    trustedOrigin: target.serviceUrl, WebSocketImpl: FakeSocket, ...extra };
}
export async function tick() { for (let i = 0; i < 8; i++) await Promise.resolve(); }
export async function authenticate(socket = FakeSocket.sockets[0]) {
  socket.open(); socket.reply({ authenticated: true, viewer_id: "viewer" }); await tick(); return socket;
}
