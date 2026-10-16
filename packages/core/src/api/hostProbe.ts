export type HostReachability = {
  state: "checking" | "reachable" | "unreachable";
  host: string;
  rttMs: number | null;
  // null until the host has answered; an unreachable host says nothing
  // about whether this device is paired.
  paired: boolean | null;
};

export async function probeHost(
  base: string,
  token: string | null = null,
  fetchImpl: typeof fetch = fetch,
  now: () => number = Date.now,
): Promise<HostReachability> {
  const url = new URL(base);
  const started = now();
  try {
    const response = await fetchImpl(`${base.replace(/\/+$/, "")}/pair/status`, {
      method: "GET",
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!response.ok) throw new Error(String(response.status));
    const rttMs = Math.max(0, now() - started);
    let paired = false;
    try {
      paired = (await response.json())?.paired === true;
    } catch {}
    return { state: "reachable", host: url.host, rttMs, paired };
  } catch {
    return { state: "unreachable", host: url.host, rttMs: null, paired: null };
  }
}
