import { diffTyped } from "./typing";

test("appended text is sent as text", () => {
  expect(diffTyped("he", "hello")).toEqual({ backspaces: 0, text: "llo" });
});

test("deleted characters become backspaces", () => {
  expect(diffTyped("hello", "hel")).toEqual({ backspaces: 2, text: "" });
});

test("a keyboard rewriting the end (Telex, autocorrect) deletes then retypes", () => {
  // Vietnamese Telex: "tieen" is rewritten to "tiên" in place.
  expect(diffTyped("tiee", "tiê")).toEqual({ backspaces: 2, text: "ê" });
  expect(diffTyped("teh ", "the ")).toEqual({ backspaces: 3, text: "he " });
});

test("whole code points are counted, so emoji take one backspace", () => {
  expect(diffTyped("a😀", "a")).toEqual({ backspaces: 1, text: "" });
  expect(diffTyped("a", "a😀")).toEqual({ backspaces: 0, text: "😀" });
});

test("no change sends nothing", () => {
  expect(diffTyped("same", "same")).toEqual({ backspaces: 0, text: "" });
});
