"use client";
import { useEffect } from "react";
import { Account } from "@wc/ui";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

const ROUTE_PATH: Record<string, string> = { Login: "/login", InstanceList: "/instances", Account: "/account" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function AccountPage() {
  const router = useRouter();
  const { ready, authToken } = useServer();

  useEffect(() => {
    if (!ready) return;
    if (!authToken) router.replace("/login");
  }, [ready, authToken, router]);

  if (!ready || !authToken) return null;

  return <Account navigation={{ navigate: (route: string) => router.push(toPath(route)), replace: (route: string) => router.replace(toPath(route)) }} />;
}
