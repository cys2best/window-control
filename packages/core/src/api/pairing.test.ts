import { deviceTokenKey, pairDevice } from "./pairing";

function response(status: number, body?: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) throw new Error("no body");
      return body;
    },
  } as Response;
}

test("posts the code and device name and returns the token", async () => {
  const fetchImpl = jest.fn(async () => response(200, { token: "dev-tok" }));
  await expect(pairDevice("http://192.168.1.8:8080", "123456", "iPhone", fetchImpl as any))
    .resolves.toEqual({ token: "dev-tok" });
  expect(fetchImpl).toHaveBeenCalledWith("http://192.168.1.8:8080/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: "123456", device_name: "iPhone" }),
  });
});

test("a rejected code explains how to get a new one", async () => {
  const fetchImpl = jest.fn(async () => response(403, { detail: "Invalid or expired pairing code" }));
  const result = await pairDevice("http://h", "000000", "iPhone", fetchImpl as any);
  expect(result).toEqual({ error: "That code is wrong or has expired. Click Pair device on the PC for a new one." });
});

test("an unreachable host is reported, not thrown", async () => {
  const fetchImpl = jest.fn(async () => { throw new Error("offline"); });
  const result = await pairDevice("http://h", "123456", "iPhone", fetchImpl as any);
  expect(result).toEqual({ error: "Can't reach the host. Check the address and that EmuCtrl is running." });
});

test.each([
  [500, undefined, "Pairing failed (500)"],
  [200, {}, "Pairing failed: the host sent no token"],
  [200, undefined, "Pairing failed: the host sent no token"],
  [200, { token: "" }, "Pairing failed: the host sent no token"],
])("status %s with body %j is an error", async (status, body, message) => {
  const fetchImpl = jest.fn(async () => response(status as number, body));
  await expect(pairDevice("http://h", "123456", "iPhone", fetchImpl as any))
    .resolves.toEqual({ error: message });
});

test("token keys are per host and safe for expo-secure-store", () => {
  const key = deviceTokenKey("http://100.101.102.103:8080");
  expect(key).toMatch(/^[A-Za-z0-9._-]+$/);
  expect(key).not.toBe(deviceTokenKey("http://192.168.1.8:8080"));
  expect(deviceTokenKey("http://[fd7a:115c:a1e0::1]:8080")).toMatch(/^[A-Za-z0-9._-]+$/);
});
