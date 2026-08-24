import { fetchServerInfo } from "./client";

export type Probe = { base: string; status: number }; // status: 200 (reachable, no/valid auth) or 401 (reachable, needs login)

/**
 * Resolve the server base URL without any manual entry:
 *  1. Fast path -- try the last-known-good base alone (works most launches,
 *     avoids the bootstrap round-trip below).
 *  2. Bootstrap -- ask the stable public URL for the server's *current*
 *     local IP via /server-info, then race [local, public] as candidates.
 *     Both are known-good at that point, so nothing baked/stale is involved
 *     (unlike shipping a fixed local IP at build time, which goes stale the
 *     moment the host's Tailscale/LAN IP changes).
 * A 401 counts as "found a real server" -- it just needs a login. The
 * caller decides what to do with that status.
 */
export async function discoverServer(
  publicUrl: string,
  opts: { cachedBase?: string | null; fetchImpl?: typeof fetch; timeoutMs?: number } = {}
): Promise<Probe | null> {
  const doFetch = opts.fetchImpl || fetch;
  const timeoutMs = opts.timeoutMs ?? 2500;

  const probe = async (base: string): Promise<Probe | null> => {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const r = await doFetch(base + "/instances", { signal: ctrl.signal } as any);
      return (r.status === 200 || r.status === 401) ? { base, status: r.status } : null;
    } catch {
      return null;
    } finally {
      clearTimeout(t);
    }
  };

  if (opts.cachedBase) {
    const hit = await probe(opts.cachedBase);
    if (hit) return hit;
  }

  let candidates = [publicUrl];
  const info = await fetchServerInfo(publicUrl, doFetch, timeoutMs);
  if (info && info.local_url) {
    candidates = [info.local_url, publicUrl];
  }
  // If /server-info itself was unreachable, candidates stays [publicUrl]
  // alone -- probing it again below fails the same way and correctly
  // returns null rather than throwing.

  const results = await Promise.all(candidates.map((u) => probe(u)));
  return results.find((r) => r !== null) ?? null;
}
