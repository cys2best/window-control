import React from "react";
import { render } from "@testing-library/react";
import { useServer } from "@wc/core";
import InstancesPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/core", () => ({
  useServer: jest.fn(),
}));

jest.mock("@wc/ui", () => ({
  InstanceList: () => <div data-testid="instance-list">InstanceList</div>,
}));

describe("InstancesPage authentication gating", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("when not ready, does not render InstanceList and does not navigate", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: false, authToken: null });
    const { queryByTestId } = render(<InstancesPage />);
    expect(queryByTestId("instance-list")).toBeNull();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when ready and !authToken, redirects to /login", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, authToken: null });
    const { queryByTestId } = render(<InstancesPage />);
    expect(queryByTestId("instance-list")).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  test("when ready and authToken is present, renders InstanceList", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, authToken: "test-token" });
    const { getByTestId } = render(<InstancesPage />);
    expect(getByTestId("instance-list")).toBeTruthy();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
