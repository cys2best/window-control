"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

export default function RootPage() {
  const router = useRouter();
  const { ready, paired } = useServer();
  useEffect(() => {
    // paired stays null until the host has answered /pair/status; routing on
    // it rather than on a stored token is what lets the PC's own window
    // (loopback, no token) skip the pairing screen.
    if (!ready || paired === null) return;
    router.replace(paired ? "/instances" : "/pair");
  }, [ready, paired, router]);
  return null;
}
