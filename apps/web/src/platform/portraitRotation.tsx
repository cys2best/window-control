"use client";
import React, { useEffect, useState } from "react";

type Viewport = { width: number; height: number; touch: boolean };

function read(): Viewport {
  if (typeof window === "undefined") return { width: 0, height: 0, touch: false };
  return {
    width: window.innerWidth,
    height: window.innerHeight,
    touch: window.matchMedia?.("(pointer: coarse)").matches ?? false,
  };
}

// A phone kept rotation-locked in portrait still gets a landscape stream:
// the screen is drawn viewport-height wide and turned 90° clockwise. Touch
// screens only; desktop windows, however narrow, are never rotated. Native
// apps rotate through expo-screen-orientation instead and never mount this.
// children receives the viewport width while rotated, for touch mapping.
export function PortraitRotation({ children }: { children: (portraitViewportWidth: number | undefined) => React.ReactNode }) {
  const [viewport, setViewport] = useState<Viewport>(read);
  useEffect(() => {
    const update = () => setViewport(read());
    update();
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
    };
  }, []);
  const rotated = viewport.touch && viewport.height > viewport.width;
  const style: React.CSSProperties = rotated
    ? {
        position: "fixed", top: 0, left: 0, display: "flex", flexDirection: "column",
        width: `${viewport.height}px`, height: `${viewport.width}px`,
        transform: `translateX(${viewport.width}px) rotate(90deg)`, transformOrigin: "top left",
      }
    : { flex: 1, display: "flex", flexDirection: "column" };
  return <div style={style}>{children(rotated ? viewport.width : undefined)}</div>;
}
