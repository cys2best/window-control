import { CORE_PACKAGE_READY, connectEngineSession, pairDevice } from "./index";

test("core package resolves", () => {
  expect(CORE_PACKAGE_READY).toBe(true);
  expect(typeof connectEngineSession).toBe("function");
  expect(typeof pairDevice).toBe("function");
});
