import React from "react";
import { RTCView } from "react-native-webrtc";
import type { VideoViewProps } from "@wc/ui";

export function VideoView({ streamURL }: VideoViewProps) {
  return <RTCView streamURL={streamURL} objectFit="contain" pointerEvents="none" style={{ flex: 1 }} />;
}
