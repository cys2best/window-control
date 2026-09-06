export type SignalingViewerOpts = {
  signalingUrl: string;
  sessionId: string;
  token: string;
  offerSdp: string;
  WebSocketImpl?: any;
  timeoutMs?: number;
};

export type SignalingResult = {
  answerSdp: string;
  close: () => void;
};

export function connectSignalingViewer(opts: SignalingViewerOpts): Promise<SignalingResult> {
  const WS = opts.WebSocketImpl || (typeof WebSocket !== "undefined" ? WebSocket : (globalThis as any).WebSocket);
  if (!WS) {
    throw new Error("WebSocket implementation not found");
  }

  const timeoutMs = opts.timeoutMs ?? 8000;
  const url = new URL(opts.signalingUrl);
  url.searchParams.set("session", opts.sessionId);
  url.searchParams.set("role", "viewer");
  if (opts.token) url.searchParams.set("token", opts.token);

  let ws: any;
  try {
    ws = new WS(url.toString());
  } catch (err) {
    if (typeof WS === "function") {
      ws = WS(url.toString());
    } else {
      throw err;
    }
  }

  return new Promise((resolve, reject) => {
    let resolved = false;
    let timer: any = null;

    const cleanup = () => {
      if (timer) clearTimeout(timer);
    };

    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          try {
            ws.close();
          } catch {}
          reject(new Error("Signaling timeout"));
        }
      }, timeoutMs);
    }

    const listen = (type: string, fn: (e: any) => void) => {
      if (typeof ws.addEventListener === "function") {
        ws.addEventListener(type, fn);
      } else {
        ws[`on${type}`] = fn;
      }
    };

    listen("open", () => {
      ws.send(opts.offerSdp);
    });

    listen("message", (event: any) => {
      if (resolved) return;
      resolved = true;
      cleanup();
      resolve({
        answerSdp: event.data,
        close: () => {
          try {
            ws.close();
          } catch {}
        },
      });
    });

    listen("error", () => {
      if (resolved) return;
      resolved = true;
      cleanup();
      reject(new Error("Signaling WebSocket error"));
    });

    listen("close", (e: any) => {
      if (resolved) return;
      resolved = true;
      cleanup();
      reject(new Error(`Signaling closed: ${e?.reason || "unknown"}`));
    });
  });
}
