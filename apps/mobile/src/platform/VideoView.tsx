import React from "react";
import { RTCView } from "react-native-webrtc";
import type { VideoViewProps } from "@wc/ui";

export function VideoView({ stream }: VideoViewProps) {
  return <RTCView streamURL={(stream as any).toURL()} objectFit="contain" pointerEvents="none" style={{ flex: 1 }} />;
}
