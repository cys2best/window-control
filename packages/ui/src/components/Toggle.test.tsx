import React from "react";
import { fireEvent, render } from "@testing-library/react-native";
import { Toggle } from "./Toggle";

test("Toggle exposes switch semantics and flips its value", async () => {
  const onChange = jest.fn();
  const { getByRole } = await render(<Toggle label="Touch haptics" value={false} onChange={onChange} />);
  fireEvent.press(getByRole("switch"));
  expect(onChange).toHaveBeenCalledWith(true);
});
