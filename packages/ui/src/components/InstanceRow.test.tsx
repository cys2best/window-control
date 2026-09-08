import React from "react";
import { Animated, Image } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";
import { InstanceRow } from "./InstanceRow";

const instance = { id: "adb:A", serial: "A", title: "LDP-01", active: true };
const previewSource = { uri: "http://preview", headers: { Authorization: "Bearer real-token" } };

afterEach(() => jest.restoreAllMocks());

test("active instance renders LIVE and only response-backed chips with its authenticated preview", async () => {
  const onPress = jest.fn();
  const screen = await render(<InstanceRow instance={{ ...instance, w: 1920, h: 1080 }} previewSource={previewSource} onPress={onPress} />);
  expect(screen.getByText("LIVE")).toBeTruthy();
  expect(screen.getByText("1920×1080")).toBeTruthy();
  expect(screen.queryByText(/FPS/)).toBeNull();
  expect(screen.getByTestId("instance-preview").props.source).toEqual(previewSource);
  await fireEvent.press(screen.getByText("LDP-01"));
  expect(onPress).toHaveBeenCalledTimes(1);
});

test("inactive instances omit LIVE and incomplete resolution but show supplied FPS, including zero", async () => {
  const screen = await render(<InstanceRow instance={{ ...instance, active: false, w: 1920, fps: 60 }} previewSource={previewSource} onPress={() => {}} />);
  expect(screen.getByText("60 FPS")).toBeTruthy();
  expect(screen.queryByText("LIVE")).toBeNull();
  expect(screen.queryByText(/×/)).toBeNull();
  expect(screen.queryByTestId("instance-scanline")).toBeNull();
  await screen.rerender(<InstanceRow instance={{ ...instance, active: false, fps: 0 }} previewSource={previewSource} onPress={() => {}} />);
  expect(screen.getByText("0 FPS")).toBeTruthy();
});

test("active scanline runs for four seconds and stops when inactive or unmounted", async () => {
  const start = jest.fn();
  const stop = jest.fn();
  jest.spyOn(Animated, "loop").mockReturnValue({ start, stop, reset: jest.fn() });
  const timing = jest.spyOn(Animated, "timing");
  const screen = await render(<InstanceRow instance={instance} previewSource={previewSource} onPress={() => {}} />);
  expect(screen.getByTestId("instance-scanline")).toBeTruthy();
  expect(timing).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ duration: 4000, toValue: 1, useNativeDriver: true, isInteraction: false }));
  expect(start).toHaveBeenCalledTimes(1);
  await screen.rerender(<InstanceRow instance={{ ...instance, active: false }} previewSource={previewSource} onPress={() => {}} />);
  expect(stop).toHaveBeenCalledTimes(1);
  expect(screen.queryByTestId("instance-scanline")).toBeNull();
  await screen.rerender(<InstanceRow instance={instance} previewSource={previewSource} onPress={() => {}} />);
  await screen.unmount();
  expect(stop).toHaveBeenCalledTimes(2);
});
