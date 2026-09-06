import { CORE_PACKAGE_READY, connectSignalingViewer } from "./index";

test("core package resolves", () => {
  expect(CORE_PACKAGE_READY).toBe(true);
  expect(typeof connectSignalingViewer).toBe("function");
});
