import { httpUrl } from "./urls";

export type Instance = { id: string; serial: string; title: string; w?: number; h?: number };

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
  signaling_url: string | null;
  public_session: string | null;
  ice_servers: IceServer[];
  generation: number;
};

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

export function makeClient(base: string, authToken: string | null) {
  const request = async (path: string, init: RequestInit = {}) => {
    const requestHeaders = new Headers(init.headers);
    if (authToken) requestHeaders.set("Authorization", `Bearer ${authToken}`);
    const response = await fetch(httpUrl(base, path), {
      ...init,
      headers: requestHeaders,
    });
    if (!response.ok) throw new ApiError(path, response.status);
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
        w: d.w, h: d.h,
      }));
    },
    async select(serial: string): Promise<SelectResp> {
      const r = await request(`/instances/${serial}/select`, { method: "POST" });
      return r.json();
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
    previewSource(serial: string): { uri: string; headers?: { Authorization: string } } {
      const uri = httpUrl(base, `/instances/${serial}/preview?t=${Date.now()}`);
      return authToken ? { uri, headers: { Authorization: `Bearer ${authToken}` } } : { uri };
    },
  };
}
