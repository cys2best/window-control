import React from "react";
import { render } from "@testing-library/react";
import { ServerProvider } from "@wc/core";
import BenchPage from "./page";
import { plainStorage, secureStorage } from "../../platform/storage";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams("serial=emulator-5556&n=5"),
}));

test("BenchPage renders under the server provider without starting a run", () => {
  expect(() =>
    render(
      <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage}>
        <BenchPage />
      </ServerProvider>
    )
  ).not.toThrow();
  expect((window as any).__benchResult).toBeUndefined();
});
