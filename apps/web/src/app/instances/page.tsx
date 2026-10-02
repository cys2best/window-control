"use client";
import { useEffect } from "react";
import { InstanceList } from "@wc/ui";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

// Screens navigate by PascalCase route name (e.g. "Pair", "InstanceList")
// which doesn't lowercase-map onto this app's actual path segments 1:1.
const ROUTE_PATH: Record<string, string> = { Pair: "/pair", InstanceList: "/instances", Account: "/account" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function InstancesPage() {
  const router = useRouter();
  const { ready, paired } = useServer();

  useEffect(() => {
    if (!ready || paired === null) return;
    if (!paired) router.replace("/pair");
  }, [ready, paired, router]);

  if (!ready || !paired) return null;

  return (
    <InstanceList
      navigation={{
        navigate: (route: string, params?: any) =>
          router.push(route === "Stream" ? `/stream?serial=${encodeURIComponent(params.serial)}` : toPath(route)),
        replace: (route: string, params?: any) =>
          router.replace(route === "Stream" ? `/stream?serial=${encodeURIComponent(params.serial)}` : toPath(route)),
      }}
    />
  );
}
