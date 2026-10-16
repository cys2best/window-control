import { httpUrl } from "./urls";

export type PairResult = { token: string } | { error: string };

// expo-secure-store accepts only [A-Za-z0-9._-] in a key, so the host part
// is flattened rather than used verbatim.
export function deviceTokenKey(base: string): string {
  return `wc_device_token.${base.replace(/[^A-Za-z0-9._-]/g, "_")}`;
}

export async function pairDevice(
  base: string,
  code: string,
  deviceName: string,
  fetchImpl: typeof fetch = fetch,
): Promise<PairResult> {
  let response: Response;
  try {
    response = await fetchImpl(httpUrl(base, "/pair"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, device_name: deviceName }),
    });
  } catch {
    return { error: "Can't reach the host. Check the address and that EmuCtrl is running." };
  }
  if (response.status === 403) {
    return { error: "That code is wrong or has expired. Click Pair device on the PC for a new one." };
  }
  if (!response.ok) return { error: `Pairing failed (${response.status})` };
  const body = await response.json().catch(() => null);
  return body && typeof body.token === "string" && body.token
    ? { token: body.token }
    : { error: "Pairing failed: the host sent no token" };
}
