export type HostReachability = {
  state: "checking" | "reachable" | "unreachable";
  route: "lan" | "relay";
  host: string;
  rttMs: number | null;
};

export function classifyHostRoute(base: string): "lan" | "relay" {
  const host = new URL(base).hostname;
  if (host === "localhost" || host === "127.0.0.1" || host === "::1") return "lan";
  if (/^10\./.test(host) || /^192\.168\./.test(host)) return "lan";
  const second = Number(host.split(".")[1]);
  if (/^172\./.test(host) && second >= 16 && second <= 31) return "lan";
  if (/^100\./.test(host)) return "lan";
  return "relay";
}

export async function probeHost(
  base: string,
  fetchImpl: typeof fetch = fetch,
  now: () => number = Date.now,
): Promise<HostReachability> {
  const url = new URL(base);
  const started = now();
  try {
    const response = await fetchImpl(`${base.replace(/\/+$/, "")}/auth/config`, { method: "GET" });
    if (!response.ok) throw new Error(String(response.status));
    return {
      state: "reachable",
      route: classifyHostRoute(base),
      host: url.host,
      rttMs: Math.max(0, now() - started),
    };
  } catch {
    return { state: "unreachable", route: classifyHostRoute(base), host: url.host, rttMs: null };
  }
}
