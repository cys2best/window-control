"use client";
import { useEffect, useMemo, useState } from "react";
import { parseRemoteInvite } from "@wc/core";
import { Pair } from "@wc/ui";
import { useRouter } from "next/navigation";

// Screens navigate by PascalCase route name (e.g. "Pair", "InstanceList")
// which doesn't lowercase-map onto this app's actual path segments 1:1.
const ROUTE_PATH: Record<string, string> = { Pair: "/pair", InstanceList: "/instances", Account: "/account" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function PairPage() {
  const router = useRouter();
  const configuredOrigin = process.env.NEXT_PUBLIC_REMOTE_SERVICE_URL;
  const remoteOptions = useMemo(() => configuredOrigin ? { trustedOrigin: configuredOrigin, requestId: () => crypto.randomUUID() } : undefined, [configuredOrigin]);
  const [invitation, setInvitation] = useState<string>();
  useEffect(() => {
    const read = () => {
      try {
        if (!configuredOrigin) throw new Error("unconfigured service");
        parseRemoteInvite(window.location.href, configuredOrigin);
        setInvitation(window.location.href);
      } catch { setInvitation(undefined); }
    };
    read(); window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, [configuredOrigin]);
  return (
    <Pair
      route={{ params: { invitation } }}
      remoteOptions={remoteOptions}
      navigation={{
        navigate: (route: string) => router.push(toPath(route)),
        replace: (route: string) => router.replace(toPath(route)),
      }}
    />
  );
}
