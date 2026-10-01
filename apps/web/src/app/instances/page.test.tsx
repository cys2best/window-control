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

describe("InstancesPage pairing gate", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test.each([
    [{ ready: false, paired: null }],
    [{ ready: true, paired: null }],
  ])("while pairing is unknown (%j), renders nothing and does not navigate", (state) => {
    (useServer as jest.Mock).mockReturnValue(state);
    const { queryByTestId } = render(<InstancesPage />);
    expect(queryByTestId("instance-list")).toBeNull();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when not paired, redirects to /pair", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: false });
    const { queryByTestId } = render(<InstancesPage />);
    expect(queryByTestId("instance-list")).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });

  test("when paired, renders InstanceList even without a token", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: true, authToken: null });
    const { getByTestId } = render(<InstancesPage />);
    expect(getByTestId("instance-list")).toBeTruthy();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
