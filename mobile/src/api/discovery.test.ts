import { discoverServer } from "./discovery";

function fakeFetch(handlers: Record<string, () => Promise<{ status: number; ok: boolean; json?: () => Promise<any> }>>) {
  return jest.fn(async (url: string) => {
    for (const prefix of Object.keys(handlers)) {
      if (url.startsWith(prefix)) return handlers[prefix]();
    }
    throw new Error(`unexpected fetch: ${url}`);
  }) as any;
}

test("cached-base fast path short-circuits the bootstrap+race", async () => {
  const fetchImpl = fakeFetch({
    "http://cached:8080/instances": async () => ({ status: 200, ok: true }),
  });
  const result = await discoverServer("https://pub", { cachedBase: "http://cached:8080", fetchImpl });
  expect(result).toEqual({ base: "http://cached:8080", status: 200 });
  // Only the cache probe should have fired -- no /server-info bootstrap call.
  expect(fetchImpl).toHaveBeenCalledTimes(1);
});

test("a failed cache falls through to /server-info, then races [local_url, publicUrl]", async () => {
  const fetchImpl = fakeFetch({
    "http://cached:8080/instances": async () => { throw new Error("unreachable"); },
    "https://pub/server-info": async () => ({ status: 200, ok: true, json: async () => ({ local_url: "http://local:8080" }) }),
    "http://local:8080/instances": async () => ({ status: 200, ok: true }),
    "https://pub/instances": async () => ({ status: 200, ok: true }),
  });
  const result = await discoverServer("https://pub", { cachedBase: "http://cached:8080", fetchImpl });
  // local_url is listed first in candidates, so it wins when both succeed.
  expect(result).toEqual({ base: "http://local:8080", status: 200 });
  const urls = fetchImpl.mock.calls.map((c: any[]) => c[0]);
  expect(urls).toContain("https://pub/server-info");
  expect(urls).toContain("http://local:8080/instances");
  expect(urls).toContain("https://pub/instances");
});

test("/server-info itself unreachable still returns a result rather than throwing", async () => {
  const fetchImpl = fakeFetch({
    "https://pub/server-info": async () => { throw new Error("down"); },
    "https://pub/instances": async () => ({ status: 200, ok: true }),
  });
  const result = await discoverServer("https://pub", { fetchImpl });
  expect(result).toEqual({ base: "https://pub", status: 200 });
});

test("/server-info unreachable and public URL itself unreachable returns null, not a throw", async () => {
  const fetchImpl = fakeFetch({
    "https://pub/server-info": async () => { throw new Error("down"); },
    "https://pub/instances": async () => { throw new Error("down"); },
  });
  await expect(discoverServer("https://pub", { fetchImpl })).resolves.toBeNull();
});

test("concurrent race resolves to the first 200/401 candidate, not the first to merely respond", async () => {
  const fetchImpl = fakeFetch({
    "https://pub/server-info": async () => ({ status: 200, ok: true, json: async () => ({ local_url: "http://local:8080" }) }),
    // local responds fast but with a non-2xx/401 status -- probe() must
    // treat it as a miss, not a hit, even though it "responded" first.
    "http://local:8080/instances": async () => ({ status: 500, ok: false }),
    "https://pub/instances": async () => ({ status: 401, ok: false }),
  });
  const result = await discoverServer("https://pub", { fetchImpl });
  expect(result).toEqual({ base: "https://pub", status: 401 });
});

test("both candidates failed returns null", async () => {
  const fetchImpl = fakeFetch({
    "https://pub/server-info": async () => ({ status: 200, ok: true, json: async () => ({ local_url: "http://local:8080" }) }),
    "http://local:8080/instances": async () => { throw new Error("down"); },
    "https://pub/instances": async () => ({ status: 500, ok: false }),
  });
  const result = await discoverServer("https://pub", { fetchImpl });
  expect(result).toBeNull();
});

test("a cached base whose host matches the public URL does NOT short-circuit -- it still races local_url (finding #7)", async () => {
  // A cached base equal to the public tunnel URL means discovery landed on
  // the slower relay path at some point in the past. Short-circuiting on
  // it here would pin all traffic (not just video) to the VPS relay
  // forever, even once the phone is back on the same Tailscale network as
  // the server. The bootstrap+race must still run so a faster local_url
  // can win.
  const fetchImpl = fakeFetch({
    "https://pub/server-info": async () => ({ status: 200, ok: true, json: async () => ({ local_url: "http://local:8080" }) }),
    "http://local:8080/instances": async () => ({ status: 200, ok: true }),
    "https://pub/instances": async () => ({ status: 200, ok: true }),
  });
  const result = await discoverServer("https://pub", { cachedBase: "https://pub", fetchImpl });
  // If the cache hit had short-circuited, this would have resolved to
  // {base: "https://pub", status: 200} immediately, without ever touching
  // /server-info or local_url. Resolving to local_url instead proves the
  // bootstrap+race actually ran.
  expect(result).toEqual({ base: "http://local:8080", status: 200 });
  const urls = fetchImpl.mock.calls.map((c: any[]) => c[0]);
  expect(urls).toContain("https://pub/server-info");
  expect(urls).toContain("http://local:8080/instances");
});

test("401 counts as a found server (needs login), not a failure", async () => {
  const fetchImpl = fakeFetch({
    "http://cached:8080/instances": async () => ({ status: 401, ok: false }),
  });
  const result = await discoverServer("https://pub", { cachedBase: "http://cached:8080", fetchImpl });
  expect(result).toEqual({ base: "http://cached:8080", status: 401 });
});
