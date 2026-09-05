"use client";
import { InstanceList } from "@wc/ui";
import { useRouter } from "next/navigation";

// Screens navigate by PascalCase route name (e.g. "ServerSetup", "InstanceList")
// which doesn't lowercase-map onto this app's actual path segments 1:1.
const ROUTE_PATH: Record<string, string> = { Login: "/login", ServerSetup: "/setup", InstanceList: "/instances" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function InstancesPage() {
  const router = useRouter();
  return (
    <InstanceList
      navigation={{
        navigate: (route: string, params?: any) =>
          router.push(route === "Stream" ? `/stream?serial=${encodeURIComponent(params.serial)}` : toPath(route)),
      }}
    />
  );
}
