import { connectRemoteClient } from "./client";
import { FakeSocket, target, options, authenticate, tick, selection } from "./testUtils";
// ES2020 is the core package's target.
function last<T>(items: T[]): T { return items[items.length - 1]; }
let clients: ReturnType<typeof connectRemoteClient>[];
beforeEach(() => { FakeSocket.sockets = []; clients = []; });
afterEach(() => { clients.forEach(c => c.dispose()); jest.useRealTimers(); });
function make(extra = {}, unauthorized?: () => void) { const c = connectRemoteClient(target, "secret", unauthorized, options(extra)); clients.push(c); return c; }
test("authenticates first with no URL secrets and maps instances and authenticated RTT", async () => {
  let now = 100; const c = make({ now: () => now }); const promise = c.instances();
  const s = FakeSocket.sockets[0]; expect(s.url).toBe("wss://relay.example/connect");
  s.open(); expect(s.sent).toHaveLength(1); expect(s.sent[0]).toMatchObject({ op: "viewer_auth", payload: { installation_id: target.installationId, token: "secret" } });
  s.reply({ authenticated: true, viewer_id: "viewer" }); await tick();
  s.reply({ instances: [{ id: "adb:A", serial: "A", name: "Alpha", active: true, w: 1920, h: 1080, fps: 60 }] });
  await expect(promise).resolves.toEqual([{ id: "adb:A", serial: "A", title: "Alpha", active: true, w: 1920, h: 1080, fps: 60 }]);
  const ping = c.ping(); await tick(); now = 137; expect(last(s.sent).op).toBe("instances"); s.reply({ instances: [] }); await expect(ping).resolves.toBe(37);
});
test("caps pending work at 32 including requests awaiting authentication", async () => {
  const c = make(); const promises = Array.from({ length: 32 }, () => c.instances().catch(e => e.code));
  await expect(c.instances()).rejects.toMatchObject({ code: "busy" });
  const s = await authenticate(); expect(s.sent).toHaveLength(33); c.dispose();
  expect(await Promise.all(promises)).toEqual(Array(32).fill("canceled"));
});
test("a new socket rejects old promises and authenticates freshly without replay", async () => {
  const c = make(); const s = await authenticate(); const old = c.select("A").catch(e => e.code); await tick(); const oldFrame = last(s.sent);
  s.close(); expect(await old).toBe("offline");
  const fresh = c.instances(); const next = await authenticate(FakeSocket.sockets[1]);
  expect(next.sent.map(f => f.op)).toEqual(["viewer_auth", "instances"]);
  expect(next.sent[0].id).not.toBe(s.sent[0].id);
  s.reply(selection, oldFrame); next.reply({ instances: [] }); await expect(fresh).resolves.toEqual([]);
});
test("canceling select closes its viewer connection and never replays the mutation", async () => {
  const c = make(); const s = await authenticate(); const abort = new AbortController();
  const result = c.select("A", { signal: abort.signal }).catch(e => e.code); await tick(); abort.abort();
  expect(await result).toBe("canceled"); expect(s.readyState).toBe(3);
  const fresh = c.instances(); const next = await authenticate(FakeSocket.sockets[1]);
  expect(next.sent.map(f => f.op)).toEqual(["viewer_auth", "instances"]); next.reply({ instances: [] }); await fresh;
});
test("canceling negotiate closes only its exact known session", async () => {
  const c = make({ now: () => 100 }); const s = await authenticate(); const abort = new AbortController();
  const remote = { ...selection, kind: "remote" as const };
  const result = c.negotiate(remote, "offer", { signal: abort.signal, deadline: 300 }).catch(e => e.code); await tick();
  expect(last(s.sent)).toMatchObject({ op: "negotiate", payload: { session_id: selection.session_id, generation: 1, offer: "offer", timeout_ms: 200 } });
  abort.abort(); expect(await result).toBe("canceled"); expect(last(s.sent)).toMatchObject({ op: "close", payload: { session_id: selection.session_id, generation: 1 } }); expect(s.readyState).toBe(1);
});
test("aborting a sent renewal retires its viewer even if a replacement was admitted", async () => {
  const c = make(); const s = await authenticate();
  const remote = { ...selection, kind: "remote" as const };
  const replacement = { ...selection, session_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", generation: 2 };
  const abort = new AbortController();
  const result = c.renew(remote, { signal: abort.signal }).catch(e => e.code);
  await tick(); const renewFrame = last(s.sent);
  abort.abort();
  expect(await result).toBe("canceled");
  expect(s.readyState).toBe(3);
  expect(s.sent.map(f => f.op)).toEqual(["viewer_auth", "renew"]);
  s.reply(replacement, renewFrame);
  const fresh = c.instances(); const next = await authenticate(FakeSocket.sockets[1]);
  expect(next.sent.map(f => f.op)).toEqual(["viewer_auth", "instances"]);
  next.reply({ instances: [] }); await fresh;
});
test("timing out a sent renewal retires the viewer with an unknown replacement", async () => {
  jest.useFakeTimers(); let now = 100;
  const c = make({ now: () => now }); const s = await authenticate();
  const result = c.renew({ ...selection, kind: "remote" }, { deadline: 200 }).catch(e => e.code);
  await tick(); expect(last(s.sent).op).toBe("renew");
  now = 200; jest.advanceTimersByTime(100);
  expect(await result).toBe("timeout");
  expect(s.readyState).toBe(3);
  expect(s.sent.map(f => f.op)).toEqual(["viewer_auth", "renew"]);
});
test("the absolute deadline includes authentication and rejects late answers", async () => {
  jest.useFakeTimers(); let now = 100; const c = make({ now: () => now });
  const result = c.negotiate({ ...selection, kind: "remote" }, "offer", { deadline: 200 }).catch(e => e.code);
  now = 180; const s = await authenticate(); expect(last(s.sent).payload.timeout_ms).toBe(20);
  now = 201; s.reply({ answer: "answer", session_id: selection.session_id, generation: 1 }); expect(await result).toBe("timeout"); expect(last(s.sent).op).toBe("close");
});
test("a deadline expires while authentication is still pending", async () => {
  jest.useFakeTimers();
  let now = 100;
  const c = make({ now: () => now });
  const result = c.select("A", { deadline: 200 }).catch(e => e.code);
  now = 200; jest.advanceTimersByTime(100);
  await expect(result).resolves.toBe("timeout");
  expect(FakeSocket.sockets[0].sent).toHaveLength(0);
});
test("a late select reply retires its viewer connection for broker cleanup", async () => {
  let now = 100;
  const c = make({ now: () => now });
  const s = await authenticate();
  const result = c.select("A", { deadline: 200 }).catch(e => e.code);
  await tick(); now = 201; s.reply(selection);
  await expect(result).resolves.toBe("timeout");
  expect(s.readyState).toBe(3);
});
test("returns exact typed selection, answer, renewal and bounded preview", async () => {
  const c = make(); const s = await authenticate(); const select = c.select("A"); await tick(); s.reply(selection);
  const remote = await select; expect(remote).toEqual({ ...selection, kind: "remote" });
  const negotiate = c.negotiate(remote, "offer", {}); await tick(); s.reply({ answer: "answer", session_id: selection.session_id, generation: 1 }); await expect(negotiate).resolves.toEqual({ answer: "answer", session_id: selection.session_id, generation: 1 });
  const preview = c.preview("A"); await tick(); s.reply({ mime: "image/jpeg", data_base64: "YWJj" }); await expect(preview).resolves.toEqual({ uri: "data:image/jpeg;base64,YWJj" });
  const renew = c.renew(remote); await tick(); s.reply({ ...selection, session_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", generation: 2 }); expect((await renew).generation).toBe(2);
  for (const run of [() => c.keyframe("A"), () => c.setQuality("A", "720")]) { const p = run(); await tick(); s.reply({ ok: true }); await p; }
  const close = c.closeSession(remote); await tick(); s.reply({ closed: true }); await close;
});
test.each(["offline", "not_paired"])("only not_paired triggers unauthorized (%s)", async code => {
  const unauthorized = jest.fn(); const c = make({}, unauthorized); const s = await authenticate(); const result = c.instances(); await tick(); s.error(code); await expect(result).rejects.toMatchObject({ code }); expect(unauthorized).toHaveBeenCalledTimes(code === "not_paired" ? 1 : 0);
});
test.each([
  '{"v":1,"v":1,"id":"00000000-0000-4000-8000-000000000002","ok":true,"result":{"instances":[]}}',
  "x".repeat(1024 * 1024 + 1),
  JSON.stringify({ v: 2, id: "00000000-0000-4000-8000-000000000002", ok: true, result: {} }),
])( "malformed or oversized replies retire the connection", async raw => {
  const c = make(); const s = await authenticate(); const result = c.instances(); await tick(); s.raw(raw); await expect(result).rejects.toMatchObject({ code: "invalid_request" }); expect(s.readyState).toBe(3);
});
test.each([
  { ...selection, whep_url: "http://private" }, { ...selection, generation: 0 },
  { ...selection, renew_after: 1 }, { ...selection, ice_servers: [{ urls: ["turn:relay"], username: "user" }] },
])( "rejects nonpublic or invalid selection results", async result => {
  const c = make(); const s = await authenticate(); const p = c.select("A"); await tick(); s.reply(result); await expect(p).rejects.toMatchObject({ code: "invalid_request" }); expect(s.readyState).toBe(3);
});
test("rejects invalid local inputs without sending them", async () => {
  const c = make(); const s = await authenticate();
  await expect(c.select("bad/serial")).rejects.toThrow(); await expect(c.negotiate({ ...selection, kind: "remote" }, "x".repeat(128 * 1024 + 1), {})).rejects.toThrow();
  expect(s.sent).toHaveLength(1);
});
