"use client";
import { Stream } from "@wc/ui";
import { useRouter, useSearchParams } from "next/navigation";
import { VideoView } from "../../platform/VideoView";

// Screens navigate by PascalCase route name (e.g. "ServerSetup", "InstanceList")
// which doesn't lowercase-map onto this app's actual path segments 1:1.
const ROUTE_PATH: Record<string, string> = { Login: "/login", ServerSetup: "/setup", InstanceList: "/instances" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

// `/stream` takes its instance serial from a query param, not a `[serial]`
// dynamic route segment. `output: "export"` (next.config.js) pre-renders one
// static HTML shell per dynamic-segment value at build time, with no server
// to render anything else on request — incompatible with instance serials
// that are only known at runtime with unbounded, arbitrary values (confirmed
// by direct experiment: even a placeholder generateStaticParams() entry that
// satisfies `next build` still 500s on a real serial in `next dev`, since
// `output: "export"` rejects any param not in that static list). A query
// param keeps this one static page for every serial; StreamPageClient reads
// it at runtime like any other client-side value.
const serialFromParams = (params: URLSearchParams) => params.get("serial") ?? "";

export default function StreamPageClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const serial = serialFromParams(searchParams);
  return (
    <Stream
      route={{ params: { serial, title: serial } }}
      navigation={{
        navigate: (route: string) => router.push(toPath(route)),
        setParams: (p: any) => router.push(`/stream?serial=${encodeURIComponent(p.serial)}`),
      }}
      RTCImpl={typeof window !== "undefined" ? window.RTCPeerConnection : undefined}
      VideoView={VideoView}
    />
  );
}
