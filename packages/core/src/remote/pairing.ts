import { parseReply, trustedServiceUrl, validateInput } from "./protocol";
import type { RemoteClientOptions } from "./client";

export function parseRemoteInvite(url: string, trustedOrigin: string): { serviceUrl: string; handle: string } {
  const invite = new URL(url);
  const serviceUrl = trustedServiceUrl(invite.origin, trustedOrigin);
  if (invite.origin !== serviceUrl || invite.username || invite.password || invite.pathname !== "/pair" || invite.search) throw new Error("invalid invitation");
  const params = new URLSearchParams(invite.hash.slice(1));
  if (Array.from(params.keys()).length !== 1 || params.getAll("invite").length !== 1) throw new Error("invalid invitation");
  const handle = params.get("invite")!;
  if (!handle || !/^[A-Za-z0-9_.:-]+$/.test(handle)) throw new Error("invalid invitation");
  return { serviceUrl, handle };
}

export async function pairRemote(serviceUrl: string, handle: string, code: string, deviceName: string, options: RemoteClientOptions): Promise<{ installationId: string; token: string }> {
  const origin = trustedServiceUrl(serviceUrl, options.trustedOrigin, options.allowInsecureLocalhost);
  validateInput(handle, "handle"); validateInput(code, "code"); validateInput(deviceName, "device_name");
  const Socket = options.WebSocketImpl ?? WebSocket;
  const requestId = options.requestId();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(requestId)) throw new Error("invalid request ID");
  return new Promise((resolve, reject) => {
    const socket = new Socket(`${origin.replace(/^http/, "ws")}/connect`);
    let settled = false;
    const fail = (error: Error) => { if (settled) return; settled = true; socket.close(); reject(error); };
    socket.onopen = () => {
      try { socket.send(JSON.stringify({ v: 1, id: requestId, op: "pair", payload: { handle, code, device_name: deviceName } })); }
      catch { fail(new Error("pairing unavailable")); }
    };
    socket.onmessage = ({ data }: { data: string }) => {
      if (settled) return;
      try {
        const reply = parseReply(data);
        if (reply.id !== requestId) return;
        if (!reply.ok) throw new Error(reply.error!.code);
        const result = reply.result!;
        if (Object.keys(result).length !== 2 || !/^[0-9a-f]{32}$/.test(String(result.installation_id)) || typeof result.token !== "string" || !result.token || result.token.length > 256) throw new Error("invalid pairing result");
        settled = true; socket.close(); resolve({ installationId: result.installation_id as string, token: result.token });
      } catch (error) { fail(error as Error); }
    };
    socket.onerror = () => fail(new Error("pairing unavailable"));
    socket.onclose = () => fail(new Error("pairing offline"));
  });
}
