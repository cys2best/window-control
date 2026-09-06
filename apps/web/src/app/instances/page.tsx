"use client";
import { useEffect } from "react";
import { InstanceList } from "@wc/ui";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

// Screens navigate by PascalCase route name (e.g. "Login", "InstanceList")
// which doesn't lowercase-map onto this app's actual path segments 1:1.
const ROUTE_PATH: Record<string, string> = { Login: "/login", InstanceList: "/instances" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function InstancesPage() {
  const router = useRouter();
  const { ready, authToken } = useServer();

  useEffect(() => {
    if (!ready) return;
    if (!authToken) router.replace("/login");
  }, [ready, authToken, router]);

  if (!ready || !authToken) return null;

  return (
    <InstanceList
      navigation={{
        navigate: (route: string, params?: any) =>
          router.push(route === "Stream" ? `/stream?serial=${encodeURIComponent(params.serial)}` : toPath(route)),
      }}
    />
  );
}
