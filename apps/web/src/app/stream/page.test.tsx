import React from "react";
import { render } from "@testing-library/react";
import { ServerProvider } from "@wc/core";
import StreamPage from "./page";
import { plainStorage, secureStorage } from "../../platform/storage";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams("serial=adb:emulator-5554"),
}));

// StreamPage assumes it's mounted under the root layout's <ServerProvider>
// (as it is in the real app) — a bare render() doesn't apply app-router
// layouts, so this test reconstructs that one level of nesting.
test("StreamPage renders without a platform-adapter crash", () => {
  expect(() =>
    render(
      <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage}>
        <StreamPage />
      </ServerProvider>
    )
  ).not.toThrow();
});
