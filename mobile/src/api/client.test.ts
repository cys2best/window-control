import { makeClient, fetchServerInfo } from "./client";

const realFetch = global.fetch;
afterEach(() => { (global as any).fetch = realFetch; });

test("login() posts the token as JSON and reports success", async () => {
  const fetchImpl = jest.fn(async () => ({ ok: true })) as any;
  (global as any).fetch = fetchImpl;
  const client = makeClient("http://h:8080");
  const ok = await client.login("s3cret");
  expect(ok).toBe(true);
  expect(fetchImpl).toHaveBeenCalledWith("http://h:8080/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: "s3cret" }),
  });
});

test("login() reports failure on a non-ok response", async () => {
  (global as any).fetch = jest.fn(async () => ({ ok: false })) as any;
  const client = makeClient("http://h:8080");
  const ok = await client.login("wrong");
  expect(ok).toBe(false);
});

test("fetchServerInfo() parses the local_url response", async () => {
  const fetchImpl = jest.fn(async (url: string) => ({
    ok: true, json: async () => ({ local_url: "http://192.168.1.5:8080" }),
  })) as any;
  const info = await fetchServerInfo("https://pub", fetchImpl);
  expect(fetchImpl).toHaveBeenCalledWith("https://pub/server-info", expect.objectContaining({ signal: expect.anything() }));
  expect(info).toEqual({ local_url: "http://192.168.1.5:8080" });
});

test("fetchServerInfo() strips a trailing slash from the base URL", async () => {
  const fetchImpl = jest.fn(async (url: string) => ({ ok: true, json: async () => ({ local_url: "x" }) })) as any;
  await fetchServerInfo("https://pub/", fetchImpl);
  expect(fetchImpl).toHaveBeenCalledWith("https://pub/server-info", expect.anything());
});

test("fetchServerInfo() returns null on a non-ok response", async () => {
  const fetchImpl = jest.fn(async () => ({ ok: false })) as any;
  const info = await fetchServerInfo("https://pub", fetchImpl);
  expect(info).toBeNull();
});

test("fetchServerInfo() returns null instead of throwing when the fetch itself fails", async () => {
  const fetchImpl = jest.fn(async () => { throw new Error("network down"); }) as any;
  const info = await fetchServerInfo("https://pub", fetchImpl);
  expect(info).toBeNull();
});
