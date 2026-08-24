import React from "react";
import { act, render, fireEvent, waitFor } from "@testing-library/react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { ServerProvider } from "../api/ServerContext";
import { ServerSetup } from "./ServerSetup";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

test("rejects a malformed URL with an inline error", async () => {
  const nav = { replace: jest.fn() } as any;
  const { getByPlaceholderText, getByText } = await render(
    <ServerProvider><ServerSetup navigation={nav} /></ServerProvider>);
  await act(async () => {
    fireEvent.changeText(getByPlaceholderText(/http:\/\//), "not a url");
  });
  await act(async () => {
    fireEvent.press(getByText("Start streaming"));
  });
  await waitFor(() => getByText(/Enter a full URL/i));
  expect(nav.replace).not.toHaveBeenCalled();
});
