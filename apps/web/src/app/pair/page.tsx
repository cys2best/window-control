"use client";
import { Pair } from "@wc/ui";
import { useRouter } from "next/navigation";

// Screens navigate by PascalCase route name (e.g. "Pair", "InstanceList")
// which doesn't lowercase-map onto this app's actual path segments 1:1.
const ROUTE_PATH: Record<string, string> = { Pair: "/pair", InstanceList: "/instances", Account: "/account" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function PairPage() {
  const router = useRouter();
  return (
    <Pair
      navigation={{
        navigate: (route: string) => router.push(toPath(route)),
        replace: (route: string) => router.replace(toPath(route)),
      }}
    />
  );
}
