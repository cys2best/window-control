import { useEffect, useMemo, useState } from "react";
import type { ApiClient, PreviewSource } from "@wc/core";

// One queue across list and drawer rows. A canceled request owns its release,
// even when the underlying transport cannot stop its promise.
const MAX_ACTIVE = 2;
let active = 0;
const waiting: Array<() => void> = [];
function drain() {
  while (active < MAX_ACTIVE && waiting.length) waiting.shift()!();
}
function enqueue(work: (signal: AbortSignal) => Promise<void>): () => void {
  const controller = new AbortController();
  let started = false, released = false;
  const release = () => {
    if (!started || released) return;
    released = true; active--; drain();
  };
  const start = () => {
    if (controller.signal.aborted) return;
    started = true; active++;
    void Promise.resolve().then(() => {
      if (!controller.signal.aborted) return work(controller.signal);
    }).catch(() => {}).finally(release);
  };
  waiting.push(start); drain();
  return () => {
    controller.abort();
    const index = waiting.indexOf(start);
    if (index !== -1) waiting.splice(index, 1);
    release();
  };
}

export function useInstancePreview(client: ApiClient | null, serial: string): PreviewSource | null {
  const identity = useMemo(() => ({ client, serial }), [client, serial]);
  const [result, setResult] = useState<{ identity: typeof identity; source: PreviewSource } | null>(null);
  useEffect(() => {
    if (!client) return;
    return enqueue(async signal => {
      const source = await client.preview(serial, { signal, deadline: performance.now() + 30_000 });
      if (!signal.aborted) setResult({ identity, source });
    });
  }, [identity]);
  return result?.identity === identity ? result.source : null;
}
