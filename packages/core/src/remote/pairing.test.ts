import { parseRemoteInvite, pairRemote } from "./pairing";
import { FakeSocket, options, tick, installationId } from "./testUtils";
beforeEach(() => { FakeSocket.sockets = []; });
test("rejects an invitation outside the configured trust boundary", () => {
  const origin = "https://relay.example";
  expect(parseRemoteInvite(`${origin}/pair#invite=abc`, origin)).toEqual({ serviceUrl: origin, handle: "abc" });
  for (const url of ["http://relay.example/pair#invite=abc", "https://evil.example/pair#invite=abc",
    "https://user@relay.example/pair#invite=abc", `${origin}/other#invite=abc`,
    `${origin}/pair#invite=abc&invite=def`, `${origin}/pair`, `${origin}/pair?token=secret#invite=abc`])
    expect(() => parseRemoteInvite(url, origin)).toThrow();
});
test("pairs in the first frame without URL secrets and closes after the reply", async () => {
  const promise = pairRemote("https://relay.example", "a".repeat(22), "123456", "Phone", options());
  const socket = FakeSocket.sockets[0]; socket.open();
  expect(socket.url).toBe("wss://relay.example/connect");
  expect(socket.sent[0]).toMatchObject({ v: 1, op: "pair", payload: { handle: "a".repeat(22), code: "123456", device_name: "Phone" } });
  socket.reply({ installation_id: installationId, token: "secret" });
  await expect(promise).resolves.toEqual({ installationId, token: "secret" });
  expect(socket.readyState).toBe(3);
});
test("rejects untrusted pairing or bad code before opening a socket", async () => {
  await expect(pairRemote("https://evil.example", "a".repeat(22), "123456", "Phone", options())).rejects.toThrow();
  await expect(pairRemote("https://relay.example", "a".repeat(22), "no", "Phone", options())).rejects.toThrow();
  await tick(); expect(FakeSocket.sockets).toHaveLength(0);
});
test("ignores a reply for a different pairing request", async () => {
  const promise = pairRemote("https://relay.example", "a".repeat(22), "123456", "Phone", options());
  const socket = FakeSocket.sockets[0]; socket.open();
  socket.raw(JSON.stringify({ v: 1, id: "ffffffff-ffff-4fff-8fff-ffffffffffff", ok: true,
    result: { installation_id: installationId, token: "wrong" } }));
  socket.reply({ installation_id: installationId, token: "right" });
  await expect(promise).resolves.toEqual({ installationId, token: "right" });
});
