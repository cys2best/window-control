import React, { useEffect, useRef } from "react";
import type { VideoViewProps } from "@wc/ui";

export function VideoView({ stream }: VideoViewProps) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.srcObject = stream;
  }, [stream]);
  return (
    <video
      ref={ref}
      autoPlay
      playsInline
      muted
      style={{ width: "100%", height: "100%", objectFit: "contain" }}
    />
  );
}
