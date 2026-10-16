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
    (useServer as jest.Mock).mockReturnValue({ ready: false, paired: null });
    render(<RootPage />);
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when ready but the host has not answered yet, does not navigate", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: null });
    render(<RootPage />);
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when ready and not paired, router.replace(\"/pair\") is called", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: false });
    render(<RootPage />);
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });

  test("when ready and paired without a token, router.replace(\"/instances\") is called", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: true, authToken: null });
    render(<RootPage />);
    expect(replaceMock).toHaveBeenCalledWith("/instances");
  });
});
