import React from "react";
import { render, waitFor } from "@testing-library/react";
import { deviceTokenKey, useServer } from "@wc/core";
import Providers from "./providers";
function Status() {
  const { ready, base, authToken, target } = useServer();
  return <span>{ready ? `${target?.kind}|${base}|${authToken ?? "Pair"}` : "loading"}</span>;
}
beforeEach(() => localStorage.clear());
test("provider hydrates and retains the saved local token across app renders", async () => {
  localStorage.setItem("wc_base", "http://192.168.1.8:8080");
  localStorage.setItem(deviceTokenKey("http://192.168.1.8:8080"), "saved-local-token");
  const view = render(<Providers><Status /></Providers>);
  expect(await view.findByText("local|http://192.168.1.8:8080|saved-local-token")).toBeTruthy();
  view.rerender(<Providers><Status /></Providers>);
  expect(view.getByText("local|http://192.168.1.8:8080|saved-local-token")).toBeTruthy();
  expect(localStorage.getItem(deviceTokenKey("http://192.168.1.8:8080"))).toBe("saved-local-token");
});
test("provider without a stored token preserves the local Pair flow", async () => {
  localStorage.setItem("wc_base", "http://192.168.1.8:8080");
  const view = render(<Providers><Status /></Providers>);
  expect(await view.findByText("local|http://192.168.1.8:8080|Pair")).toBeTruthy();
});
