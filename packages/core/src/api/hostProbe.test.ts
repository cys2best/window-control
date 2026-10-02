import * as hostProbe from "./hostProbe";
import { probeHost } from "./hostProbe";

test("reports measured reachability and the paired flag", async () => {
  let clock = 100;
  const fetchImpl = jest.fn().mockImplementation(async () => {
    clock = 128;
    return { ok: true, json: async () => ({ paired: true }) };
  });

  await expect(probeHost("http://192.168.1.8:8080", "dev-tok", fetchImpl as any, () => clock)).resolves.toEqual({
    state: "reachable", host: "192.168.1.8:8080", rttMs: 28, paired: true,
  });
  expect(fetchImpl).toHaveBeenCalledWith("http://192.168.1.8:8080/pair/status", {
    method: "GET", headers: { Authorization: "Bearer dev-tok" },
  });
});

test("sends no Authorization header without a token", async () => {
  const fetchImpl = jest.fn(async () => ({ ok: true, json: async () => ({ paired: false }) }));
  const result = await probeHost("http://192.168.1.8:8080/", null, fetchImpl as any);
  expect(result.paired).toBe(false);
  expect(fetchImpl).toHaveBeenCalledWith("http://192.168.1.8:8080/pair/status", {
    method: "GET", headers: undefined,
  });
});

test.each([
  [{ ok: true, json: async () => ({}) }],
  [{ ok: true, json: async () => { throw new Error("not json"); } }],
  [{ ok: true }],
])("a reachable host with an unusable body counts as not paired", async (response) => {
  const result = await probeHost("http://h:8080", null, (async () => response) as any);
  expect(result).toMatchObject({ state: "reachable", paired: false });
});

test.each([
  [async () => { throw new Error("offline"); }],
  [async () => ({ ok: false, status: 403 })],
])("an unreachable or refusing host leaves paired unknown", async (fetchImpl) => {
  await expect(probeHost("http://h:8080", "tok", fetchImpl as any)).resolves.toEqual({
    state: "unreachable", host: "h:8080", rttMs: null, paired: null,
  });
});

test("route classification is gone", () => {
  expect((hostProbe as any).classifyHostRoute).toBeUndefined();
});
