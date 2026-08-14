import { normalizeBase, wsUrl, httpUrl } from "./urls";

test("normalizeBase strips trailing slash and validates", () => {
  expect(normalizeBase("http://100.86.14.2:8080/")).toBe("http://100.86.14.2:8080");
  expect(() => normalizeBase("ftp://x")).toThrow();
  expect(() => normalizeBase("not a url")).toThrow();
});

test("wsUrl swaps scheme", () => {
  expect(wsUrl("http://h:8080", "/input")).toBe("ws://h:8080/input");
  expect(wsUrl("https://h", "/input")).toBe("wss://h/input");
});

test("httpUrl joins", () => {
  expect(httpUrl("http://h:8080", "/instances")).toBe("http://h:8080/instances");
});
