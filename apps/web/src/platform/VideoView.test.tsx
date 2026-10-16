import React from "react";
import { render } from "@testing-library/react";
import { VideoView } from "./VideoView";

test("VideoView assigns the given MediaStream to the video element's srcObject", () => {
  const fakeStream = {} as MediaStream;
  const { container } = render(<VideoView stream={fakeStream} />);
  const video = container.querySelector("video") as HTMLVideoElement;
  expect(video.srcObject).toBe(fakeStream);
});
