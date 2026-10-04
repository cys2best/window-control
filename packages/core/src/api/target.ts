export type ServerTarget =
  | { kind: "local"; base: string }
  | { kind: "remote"; serviceUrl: string; installationId: string };

export function remoteDeviceTokenKey(serviceUrl: string, installationId: string): string {
  if (!/^[0-9a-f]{32}$/.test(installationId)) throw new Error("invalid installation");
  const origin = new URL(serviceUrl).origin;
  let encodedOrigin = "";
  for (let i = 0; i < origin.length; i++) {
    encodedOrigin += origin.charCodeAt(i).toString(16).padStart(4, "0");
  }
  return `wc_remote_device_token.${encodedOrigin}.${installationId}`;
}
