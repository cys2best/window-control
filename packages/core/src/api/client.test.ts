import { makeClient, ApiError } from "./client";

function okJson(body: any, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

beforeEach(() => {
  global.fetch = jest.fn(async () => okJson([])) as any;
});

test("adds exact bearer header to every protected request", async () => {
  global.fetch = jest.fn(async () => okJson([])) as any;
  const client = makeClient("https://host", "s3cret-token");
  await client.instances();
  await client.select("emulator-5554");
  await client.setQuality("emulator-5554", "720");

  for (const [, init] of (fetch as jest.Mock).mock.calls) {
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer s3cret-token"
    );
  }
});

test("no Authorization header when token is null", async () => {
  const client = makeClient("https://host", null);
  await client.instances();
  const [, init] = (fetch as jest.Mock).mock.calls[0];
  expect(new Headers(init?.headers).get("Authorization")).toBeNull();
});

test("parses the exact final selection shape", async () => {
  const body = {
    ok: true,
    id: "adb:emulator-5554",
    serial: "emulator-5554",
    name: "LDP-01",
    w: 1280,
    h: 720,
    whep_url: "https://host/whep/emulator-5554",
    whep_token: "whep-tok",
    signaling_url: "wss://relay/ws",
    signaling_token: "sig-tok",
    ice_servers: [{ urls: "stun:stun.example.com:3478" }],
    generation: 1,
  };
  global.fetch = jest.fn(async () => okJson(body)) as any;
  const client = makeClient("https://host", "tok");
  const sel = await client.select("emulator-5554");
  expect(sel).toEqual(body);
});

test("non-2xx responses throw ApiError with preserved status", async () => {
  global.fetch = jest.fn(async () => okJson({}, 401)) as any;
  const client = makeClient("https://host", "tok");
  await expect(client.select("emulator-5554")).rejects.toMatchObject({
    status: 401,
  });
  await expect(client.select("emulator-5554")).rejects.toBeInstanceOf(
    ApiError
  );
});

test("triggers onUnauthorized callback on 401 status", async () => {
  global.fetch = jest.fn(async () => okJson({}, 401)) as any;
  const onUnauthorized = jest.fn();
  const client = makeClient("https://host", "tok", onUnauthorized);
  await expect(client.instances()).rejects.toBeInstanceOf(ApiError);
  expect(onUnauthorized).toHaveBeenCalledTimes(1);
});


test("setQuality and keyframe send bearer auth", async () => {
  global.fetch = jest.fn(async () => okJson({})) as any;
  const client = makeClient("https://host", "tok");
  await client.keyframe("emulator-5554");
  await client.setQuality("emulator-5554", "auto");
  for (const [, init] of (fetch as jest.Mock).mock.calls) {
    expect(new Headers(init?.headers).get("Authorization")).toBe(
      "Bearer tok"
    );
  }
});

test("previewSource returns uri and bearer header when token present", () => {
  const client = makeClient("https://host", "tok");
  const src = client.previewSource("emulator-5554");
  expect(src.uri).toContain("/instances/emulator-5554/preview");
  expect(src.headers).toEqual({ Authorization: "Bearer tok" });
});

test("previewSource omits headers when token is null", () => {
  const client = makeClient("https://host", null);
  const src = client.previewSource("emulator-5554");
  expect(src.headers).toBeUndefined();
});
