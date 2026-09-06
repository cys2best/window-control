import React from "react";
import type { Metadata, Viewport } from "next";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "EmuCtrl",
  manifest: "/manifest.json",
  // Home-screen installability + the iOS chrome-less presentation the old
  // hand-rolled client declared with raw <meta apple-mobile-web-app-*>
  // tags. Next emits the same tags from this block.
  appleWebApp: {
    capable: true,
    title: "EmuCtrl",
    statusBarStyle: "black-translucent",
  },
  // Next 15 renders appleWebApp.capable as the standardized
  // `mobile-web-app-capable` name only. Older iOS Safari (the primary
  // target here) still keys home-screen standalone mode off the
  // apple-prefixed name the deleted src/client/index.html shipped, so
  // emit that one too rather than quietly regress it.
  other: { "apple-mobile-web-app-capable": "yes" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Load-bearing for a touch-streaming client, not cosmetic: without the
  // zoom lock, pinch/double-tap gestures on the video surface fight the
  // touch input being relayed to the device. viewportFit: "cover" is what
  // makes the safe-area insets available under the iPhone notch/home bar.
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
  themeColor: "#000000",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
