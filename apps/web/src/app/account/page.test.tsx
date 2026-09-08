import React from "react";
import { fireEvent, render } from "@testing-library/react";
import { useServer } from "@wc/core";
import AccountPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/core", () => ({ useServer: jest.fn() }));

jest.mock("@wc/ui", () => ({
  Account: ({ navigation }: any) => <button onClick={() => navigation.replace("Login")}>Sign out on this device</button>,
}));

describe("AccountPage authentication gating", () => {
  beforeEach(() => jest.clearAllMocks());

  test("when ready and unauthenticated, redirects to /login", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, authToken: null });
    const { queryByText } = render(<AccountPage />);
    expect(queryByText("Sign out on this device")).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });

  test("when authenticated, renders the shared screen and maps Login replacement to /login", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, authToken: "test-token" });
    const { getByText } = render(<AccountPage />);
    fireEvent.click(getByText("Sign out on this device"));
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });
});
