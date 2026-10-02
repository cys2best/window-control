export function normalizeBase(input: string): string {
  const t = input.trim().replace(/\/+$/, "");
  if (!/^https?:\/\/\S+/.test(t)) throw new Error("invalid url");
  return t;
}

export function httpUrl(base: string, path: string): string {
  return base + path;
}

export function wsUrl(base: string, path: string): string {
  const swapped = base.startsWith("https")
    ? "wss" + base.slice(5)
    : "ws" + base.slice(4);
  return swapped + path;
}
