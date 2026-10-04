"use client";
import React from "react";
import { ServerProvider } from "@wc/core";
import { plainStorage, secureStorage } from "../platform/storage";

const remoteOrigin = process.env.NEXT_PUBLIC_REMOTE_SERVICE_URL;
const remoteOptions = remoteOrigin ? { trustedOrigin: remoteOrigin, requestId: () => crypto.randomUUID() } : undefined;

// Split out of layout.tsx so that file can stay a server component: Next
// forbids `metadata`/`viewport` exports from a "use client" module, and
// the PWA manifest / viewport-lock / apple-mobile-web-app tags this app
// needs are declared through exactly those exports.
export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage} remoteOptions={remoteOptions}>
      {children}
    </ServerProvider>
  );
}
