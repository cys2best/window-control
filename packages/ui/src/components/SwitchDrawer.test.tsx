import React from "react";
import { fireEvent, render } from "@testing-library/react-native";
import type { ApiClient } from "@wc/core";
import { SwitchDrawer } from "./SwitchDrawer";
const instances = [{ id: "a", serial: "A", title: "Alpha", active: true, w: 1920, h: 1080, fps: 60 }, { id: "b", serial: "B", title: "Beta", active: false }];
test("lists real instance fields and picks a card", async () => {
  const onPick = jest.fn(); const view = await render(<SwitchDrawer instances={instances} activeSerial="B" client={{ preview: async (serial: string) => ({ uri: `https://host/${serial}`, headers: { Authorization: "Bearer x" } }) } as ApiClient} onPick={onPick} onClose={jest.fn()} />);
  expect(view.getByText("Alpha")).toBeTruthy(); expect(view.getByText("Beta")).toBeTruthy(); expect(view.getAllByText("LIVE")).toHaveLength(1); expect(view.getByText("1920×1080 · 60 FPS")).toBeTruthy();
  await fireEvent.press(view.getByLabelText("Beta")); expect(onPick).toHaveBeenCalledWith(instances[1]);
});
