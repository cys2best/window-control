import { remoteDeviceTokenKey } from "./target";

test("remote token keys are origin-specific, collision-free and native-storage safe", () => {
  const id = "a".repeat(32);
  const key = remoteDeviceTokenKey("https://relay.example", id);
  expect(key).toMatch(/^[A-Za-z0-9._-]+$/);
  expect(remoteDeviceTokenKey("https://relay.example:443/pair", id)).toBe(key);
  expect(remoteDeviceTokenKey("https://relay.example:8443", id)).not.toBe(key);
  expect(remoteDeviceTokenKey("https://relay.example_3a8443", id)).not.toBe(remoteDeviceTokenKey("https://relay.example:8443", id));
  expect(remoteDeviceTokenKey("https://relay.example", "b".repeat(32))).not.toBe(key);
  expect(() => remoteDeviceTokenKey("https://relay.example", "bad")).toThrow();
});
