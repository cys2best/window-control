"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

export default function RootPage() {
  const router = useRouter();
  const { ready, authToken } = useServer();
  useEffect(() => {
    if (!ready) return;
    if (!authToken) router.replace("/login");
    else router.replace("/instances");
  }, [ready, authToken, router]);
  return null;
}
