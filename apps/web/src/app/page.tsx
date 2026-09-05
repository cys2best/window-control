"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

// Mirrors apps/mobile/src/navigation/Root.tsx's initialRoute decision
// (!base -> ServerSetup, !authToken -> Login, else -> InstanceList) so a
// static export root page reaches the same landing screen mobile does.
export default function RootPage() {
  const router = useRouter();
  const { ready, base, authToken } = useServer();
  useEffect(() => {
    if (!ready) return;
    if (!base) router.replace("/setup");
    else if (!authToken) router.replace("/login");
    else router.replace("/instances");
  }, [ready, base, authToken, router]);
  return null;
}
