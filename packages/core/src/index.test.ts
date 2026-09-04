import { CORE_PACKAGE_READY } from "./index";

test("core package resolves", () => {
  expect(CORE_PACKAGE_READY).toBe(true);
});
