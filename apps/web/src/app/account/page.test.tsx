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
  Account: ({ navigation }: any) => <button onClick={() => navigation.replace("Pair")}>Unpair this device</button>,
}));

describe("AccountPage pairing gate", () => {
  beforeEach(() => jest.clearAllMocks());

  test("when not paired, redirects to /pair", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: false });
    const { queryByText } = render(<AccountPage />);
    expect(queryByText("Unpair this device")).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });

  test("when paired, renders the shared screen and maps the Pair route to /pair", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: true });
    const { getByText } = render(<AccountPage />);
    fireEvent.click(getByText("Unpair this device"));
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });
});
