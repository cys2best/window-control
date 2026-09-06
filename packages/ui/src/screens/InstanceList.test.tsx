import React from "react";
import { render, fireEvent, waitFor } from "@testing-library/react-native";
import { InstanceList } from "./InstanceList";
import * as SC from "@wc/core";

jest.mock("@wc/core", () => {
  const actual = jest.requireActual("@wc/core");
  return { ...actual, useServer: jest.fn() };
});

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

afterEach(() => jest.clearAllMocks());

test("renders instances and navigates on tap", async () => {
  const client = {
    instances: jest.fn().mockResolvedValue([
      { id: "adb:A", serial: "A", title: "LDP-01" },
      { id: "adb:B", serial: "B", title: "LDP-02" },
    ]),
    previewSource: (s: string) => ({ uri: `http://h/preview/${s}` }),
    keyframe: jest.fn().mockResolvedValue(undefined),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);
  const nav = { navigate: jest.fn() } as any;
  const { getByText } = await render(<InstanceList navigation={nav} />);
  await waitFor(() => getByText("LDP-01"));
  fireEvent.press(getByText("LDP-02"));
  await waitFor(() => expect(nav.navigate).toHaveBeenCalledWith("Stream", { serial: "B", title: "LDP-02" }));
  expect(client.keyframe).toHaveBeenCalledWith("B");
});

test("BottomNav setup button does not navigate to ServerSetup", async () => {
  const client = {
    instances: jest.fn().mockResolvedValue([]),
    previewSource: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);
  const nav = { navigate: jest.fn() } as any;
  const { getByLabelText } = await render(<InstanceList navigation={nav} />);
  fireEvent.press(getByLabelText("Setup"));
  expect(nav.navigate).not.toHaveBeenCalledWith("ServerSetup");
});

