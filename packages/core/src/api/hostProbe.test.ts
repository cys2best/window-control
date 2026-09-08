import { classifyHostRoute, probeHost } from "./hostProbe";

test.each([
  ["http://192.168.1.8:8080", "lan"],
  ["http://100.90.2.4:8080", "lan"],
  ["http://localhost:8080", "lan"],
  ["https://relay.emuctrl.example", "relay"],
])("classifies %s as %s", (base, expected) => {
  expect(classifyHostRoute(base)).toBe(expected);
});

test("probeHost reports measured reachability without invented data", async () => {
  let clock = 100;
  const fetchImpl = jest.fn().mockImplementation(async () => {
    clock = 128;
    return { ok: true };
  });

  await expect(probeHost("http://192.168.1.8:8080", fetchImpl as any, () => clock)).resolves.toEqual({
    state: "reachable", route: "lan", host: "192.168.1.8:8080", rttMs: 28,
  });
});
