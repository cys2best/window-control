"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

export default function RootPage() {
  const router = useRouter();
  const { ready, paired, target, hostReachability } = useServer();
  // A local address that never answers /pair/status is no host at all (e.g.
  // the hosted remote web before any pairing); waiting would render nothing.
  const noLocalHost = target?.kind === "local" && hostReachability?.state === "unreachable";
  useEffect(() => {
    // paired stays null until the host has answered /pair/status; routing on
    // it rather than on a stored token is what lets the PC's own window
    // (loopback, no token) skip the pairing screen.
    if (!ready) return;
    if (paired === null) {
      if (noLocalHost) router.replace("/pair");
      return;
    }
    router.replace(paired ? "/instances" : "/pair");
  }, [ready, paired, noLocalHost, router]);
  return null;
}
