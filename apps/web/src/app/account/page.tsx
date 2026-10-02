"use client";
import { useEffect } from "react";
import { Account } from "@wc/ui";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

const ROUTE_PATH: Record<string, string> = { Pair: "/pair", InstanceList: "/instances", Account: "/account" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function AccountPage() {
  const router = useRouter();
  const { ready, paired } = useServer();

  useEffect(() => {
    if (!ready || paired === null) return;
    if (!paired) router.replace("/pair");
  }, [ready, paired, router]);

  if (!ready || !paired) return null;

  return <Account navigation={{ navigate: (route: string) => router.push(toPath(route)), replace: (route: string) => router.replace(toPath(route)) }} />;
}
