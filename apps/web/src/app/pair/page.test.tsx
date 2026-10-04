/** @jest-environment-options {"url":"https://remote.example"} */
import React from "react";
import { fireEvent, render } from "@testing-library/react";
import PairPage from "./page";

const replaceMock = jest.fn();
const pairProps = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/ui", () => ({
  Pair: (props: any) => { pairProps(props); return <button onClick={() => props.navigation.replace("InstanceList")}>Pair</button>; },
}));

test("a successful pairing lands on /instances", () => {
  const { getByText } = render(<PairPage />);
  fireEvent.click(getByText("Pair"));
  expect(replaceMock).toHaveBeenCalledWith("/instances");
});

test("reads a trusted fragment on the client and passes explicit remote options", () => {
  process.env.NEXT_PUBLIC_REMOTE_SERVICE_URL = "https://remote.example";
  window.history.replaceState({}, "", "/pair#invite=abcdefghijklmnopqrstuv");
  render(<PairPage />);
  expect(pairProps).toHaveBeenLastCalledWith(expect.objectContaining({ route: { params: { invitation: "https://remote.example/pair#invite=abcdefghijklmnopqrstuv" } } }));
  delete process.env.NEXT_PUBLIC_REMOTE_SERVICE_URL;
});

test("wrong origin passes no invitation and no automatic transport", () => {
  process.env.NEXT_PUBLIC_REMOTE_SERVICE_URL = "https://another.example";
  window.history.replaceState({}, "", "/pair#invite=abcdefghijklmnopqrstuv");
  render(<PairPage />);
  expect(pairProps).toHaveBeenLastCalledWith(expect.objectContaining({ route: { params: { invitation: undefined } } }));
  delete process.env.NEXT_PUBLIC_REMOTE_SERVICE_URL;
});
