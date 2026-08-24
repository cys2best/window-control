import React from "react";
import { render, fireEvent, waitFor } from "@testing-library/react-native";
import { InstanceList } from "./InstanceList";
import * as SC from "../api/ServerContext";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

test("renders instances and navigates on tap", async () => {
  const client = {
    instances: jest.fn().mockResolvedValue([
      { id: "adb:A", serial: "A", title: "LDP-01" },
      { id: "adb:B", serial: "B", title: "LDP-02" },
    ]),
    previewUrl: (s: string) => `http://h/preview/${s}`,
    keyframe: jest.fn().mockResolvedValue(undefined),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);
  const nav = { navigate: jest.fn() } as any;
  const { getByText } = await render(<InstanceList navigation={nav} />);
  await waitFor(() => getByText("LDP-01"));
  fireEvent.press(getByText("LDP-02"));
  await waitFor(() => expect(nav.navigate).toHaveBeenCalledWith("Stream", { serial: "B", title: "LDP-02" }));
  expect(client.keyframe).toHaveBeenCalledWith("B");
});
