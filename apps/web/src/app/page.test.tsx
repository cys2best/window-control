import React from "react";
import { render } from "@testing-library/react";
import { useServer } from "@wc/core";
import RootPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/core", () => ({
  useServer: jest.fn(),
}));

describe("RootPage redirection", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("when not ready, does not navigate", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: false, authToken: null });
    render(<RootPage />);
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when ready and !authToken, router.replace(\"/login\") is called", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, authToken: null });
    render(<RootPage />);
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  test("when ready and authToken is present, router.replace(\"/instances\") is called", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, authToken: "test-token" });
    render(<RootPage />);
    expect(replaceMock).toHaveBeenCalledWith("/instances");
  });
});
