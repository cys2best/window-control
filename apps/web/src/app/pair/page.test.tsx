import React from "react";
import { fireEvent, render } from "@testing-library/react";
import PairPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/ui", () => ({
  Pair: ({ navigation }: any) => <button onClick={() => navigation.replace("InstanceList")}>Pair</button>,
}));

test("a successful pairing lands on /instances", () => {
  const { getByText } = render(<PairPage />);
  fireEvent.click(getByText("Pair"));
  expect(replaceMock).toHaveBeenCalledWith("/instances");
});
