"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useServer } from "@wc/core";
import { runBench } from "./runBench";

const intParam = (params: URLSearchParams, name: string, fallback: number) => {
  const value = Number.parseInt(params.get(name) ?? "", 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};

export default function BenchPageClient() {
  const router = useRouter();
  const params = useSearchParams();
  const { ready, paired, client } = useServer();
  const videoRef = useRef<HTMLVideoElement>(null);
  const started = useRef(false);
  const [status, setStatus] = useState("waiting for server");

  useEffect(() => {
    if (!ready || paired === null) return;
    if (!paired) { router.replace("/pair"); return; }
    if (started.current || !videoRef.current || !client) return;
    started.current = true;

    runBench({
      select: (serial) => client.select(serial),
      setQuality: (serial, tier) => client.setQuality(serial, tier),
      serial: params.get("serial") ?? "",
      tier: params.get("tier") ?? undefined,
      // Same origin as this page: the host serves both in bench mode.
      targetTimings: {
        clear: async () => { await fetch("/bench/target-timings", { method: "DELETE" }); },
        list: async () => (await fetch("/bench/target-timings")).json(),
      },
      count: intParam(params, "n", 200),
      warmup: intParam(params, "warmup", 10),
      video: videoRef.current,
      onStatus: setStatus,
    }).then((result) => {
      (window as any).__benchResult = result;
      setStatus(result.validity.failed ? `done (failed: ${result.validity.reasons.join("; ")})` : "done");
    }).catch((error) => {
      const message = error instanceof Error ? error.message : String(error);
      (window as any).__benchError = message;
      setStatus(`error: ${message}`);
    });
  }, [ready, paired, client, params, router]);

  return (
    <div style={{ position: "fixed", inset: 0, background: "#222" }}>
      <video ref={videoRef} autoPlay playsInline muted
        style={{ width: "100%", height: "100%", objectFit: "contain" }} />
      {/* Kept in a corner: the flip detector samples only the centre. */}
      <div style={{ position: "absolute", left: 8, bottom: 8, color: "#ccc", font: "12px monospace" }}>
        bench: {status}
      </div>
    </div>
  );
}
