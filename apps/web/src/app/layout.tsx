"use client";
import React from "react";
import { ServerProvider } from "@wc/core";
import { plainStorage, secureStorage } from "../platform/storage";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage}>
          {children}
        </ServerProvider>
      </body>
    </html>
  );
}
