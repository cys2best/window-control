import React from "react";
import { render } from "@testing-library/react-native";
import { BrandMark } from "./BrandMark";

test("BrandMark renders the locked 96-unit geometry", async () => {
  const { getByLabelText } = await render(<BrandMark size={46} />);
  expect(getByLabelText("EmuCtrl").props.width).toBe(46);
  expect(getByLabelText("EmuCtrl").props).toMatchObject({ vbWidth: 96, vbHeight: 96 });
});
