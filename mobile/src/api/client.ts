import { httpUrl, wsUrl } from "./urls";

export type Instance = { id: string; serial: string; title: string; w?: number; h?: number };
export type SelectResp = {
  ok: boolean; serial: string; name: string; w: number; h: number;
  whep_url: string; stun_url: string;
  signaling_url: string | null;
  ice_servers: { urls: string; username?: string; credential?: string }[];
};

function serialOf(raw: any): string {
  const id: string = raw.id ?? raw.serial ?? "";
  return raw.serial ?? (id.startsWith("adb:") ? id.slice(4) : id);
}

// Standalone (not base-relative like makeClient's methods) because discovery
// needs to probe /server-info on a candidate URL *before* any base is known.
export async function fetchServerInfo(
  url: string, fetchImpl: typeof fetch = fetch, timeoutMs = 2500
): Promise<{ local_url: string } | null> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetchImpl(url.replace(/\/+$/, "") + "/server-info", { signal: ctrl.signal } as any);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

export function makeClient(base: string) {
  return {
    async instances(): Promise<Instance[]> {
      const r = await fetch(httpUrl(base, "/instances"));
      const list = await r.json();
      return (list as any[]).map((d) => ({
        id: d.id ?? d.serial,
        serial: serialOf(d),
        title: d.title ?? d.name ?? serialOf(d),
        w: d.w, h: d.h,
      }));
    },
    async select(serial: string): Promise<SelectResp> {
      const r = await fetch(httpUrl(base, `/instances/${serial}/select`), { method: "POST" });
      if (!r.ok) throw new Error(`select ${r.status}`);
      return r.json();
    },
    async keyframe(serial: string): Promise<void> {
      try { await fetch(httpUrl(base, `/instances/${serial}/keyframe`), { method: "POST" }); } catch {}
    },
    async setQuality(serial: string, tier: string): Promise<void> {
      try {
        await fetch(httpUrl(base, `/instances/${serial}/quality`), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tier }),
        });
      } catch {}
    },
    previewUrl(serial: string): string {
      return httpUrl(base, `/instances/${serial}/preview?t=${Date.now()}`);
    },
    inputWsUrl(): string { return wsUrl(base, "/input"); },
    async login(token: string): Promise<boolean> {
      const r = await fetch(httpUrl(base, "/login"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      return r.ok;
    },
  };
}
