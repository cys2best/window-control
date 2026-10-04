import { parseReply } from "./protocol";

const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

test("rejects decoded duplicate envelope keys even when one spelling is escaped", () => {
  const raw = String.raw`{"v":1,"id":"${id}","ok":false,"\u006fk":true,"result":{"instances":[]}}`;
  expect(() => parseReply(raw)).toThrow("duplicate JSON key");
});

test("rejects decoded duplicate keys in a nested result", () => {
  const raw = String.raw`{"v":1,"id":"${id}","ok":true,"result":{"instances":[],"in\u0073tances":[1]}}`;
  expect(() => parseReply(raw)).toThrow("duplicate JSON key");
});

test("accepts a singly escaped key with its decoded meaning", () => {
  const raw = String.raw`{"v":1,"id":"${id}","\u006fk":true,"result":{"instances":[]}}`;
  expect(parseReply(raw).ok).toBe(true);
});
