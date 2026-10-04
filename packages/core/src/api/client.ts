import { httpUrl } from "./urls";
import type { RemoteSelection, RequestOptions } from "../remote/protocol";

export type Instance = {
  id: string;
  serial: string;
  title: string;
  w?: number;
  h?: number;
  fps?: number;
  active: boolean;
};

export type IceServer = {
  urls: string | string[];
  username?: string;
  credential?: string;
};

export type SelectResp = {
  ok: true;
  id: string;
  serial: string;
  name: string;
  w: number;
  h: number;
  whep_url: string;
  whep_token: string;
  ice_servers: IceServer[];
  generation: number;
  // Quality tier the host is encoding at; absent on hosts older than 3.2.
  tier?: string;
};
export type LocalSelection = SelectResp & { kind: "local" };
export type Selection = LocalSelection | RemoteSelection;
export type PreviewSource = { uri: string; headers?: { Authorization: string } };

export class ApiError extends Error {
  status: number;
  constructor(path: string, status: number) {
    super(`${path} ${status}`);
    this.status = status;
  }
}

function serialOf(raw: any): string {
  const id: string = raw.id ?? raw.serial ?? "";
  return raw.serial ?? (id.startsWith("adb:") ? id.slice(4) : id);
}

export function makeClient(
  base: string,
  authToken: string | null,
  onUnauthorized?: () => void
) {
  let disposed = false;
  const request = async (path: string, init: RequestInit = {}, opts?: RequestOptions) => {
    if (disposed || opts?.signal?.aborted) throw new Error("client canceled");
    if (opts?.deadline !== undefined && (!Number.isFinite(opts.deadline) || opts.deadline <= performance.now())) throw new Error("request timed out");
    const requestHeaders = new Headers(init.headers);
    if (authToken) requestHeaders.set("Authorization", `Bearer ${authToken}`);
    const response = await fetch(httpUrl(base, path), {
      ...init,
      signal: opts?.signal,
      headers: requestHeaders,
    });
    if (!response.ok) {
      if (response.status === 401 && onUnauthorized) {
        try { onUnauthorized(); } catch {}
      }
      throw new ApiError(path, response.status);
    }
    return response;
  };

  return {
    async instances(): Promise<Instance[]> {
      const r = await request("/instances");
      const list = await r.json();
      return (list as any[]).map((d) => ({
        id: d.id ?? d.serial,
        serial: serialOf(d),
        title: d.title ?? d.name ?? serialOf(d),
        w: d.w,
        h: d.h,
        fps: typeof d.fps === "number" ? d.fps : undefined,
        active: d.active === true,
      }));
    },
    async ping(): Promise<number> {
      const started = Date.now();
      await request("/pair/status");
      return Math.max(0, Date.now() - started);
    },
    kind: "local" as const,
    async select(serial: string, opts?: RequestOptions): Promise<LocalSelection> {
      const r = await request(`/instances/${serial}/select`, { method: "POST" }, opts);
      return { ...await r.json(), kind: "local" };
    },
    async keyframe(serial: string): Promise<void> {
      try { await request(`/instances/${serial}/keyframe`, { method: "POST" }); } catch {}
    },
    async setQuality(serial: string, tier: string): Promise<void> {
      try {
        await request(`/instances/${serial}/quality`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tier }),
        });
      } catch {}
    },
    previewSource(serial: string): PreviewSource {
      const cleanSerial = serial.startsWith("adb:") ? serial.slice(4) : serial;
      const tokenParam = authToken ? `&token=${encodeURIComponent(authToken)}` : "";
      const uri = httpUrl(base, `/instances/${cleanSerial}/preview?t=${Date.now()}${tokenParam}`);
      return authToken ? { uri, headers: { Authorization: `Bearer ${authToken}` } } : { uri };
    },
    async preview(serial: string, opts?: RequestOptions): Promise<PreviewSource> {
      if (disposed || opts?.signal?.aborted) throw new Error("client canceled");
      if (opts?.deadline !== undefined && (!Number.isFinite(opts.deadline) || opts.deadline <= performance.now())) throw new Error("request timed out");
      return this.previewSource(serial);
    },
    dispose(): void { disposed = true; },
  };
}
export type LocalApiClient = ReturnType<typeof makeClient>;
