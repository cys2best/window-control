import { UI_PACKAGE_READY } from "./index";

test("ui package resolves", () => {
  expect(UI_PACKAGE_READY).toBe(true);
});
