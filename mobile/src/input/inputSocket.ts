export const clickMsg = (x: number, y: number) => ({ type: "click", x, y });
export const dragStartMsg = (x: number, y: number) => ({ type: "drag_start", x, y });
export const dragMoveMsg = (x: number, y: number, scroll: boolean) => ({ type: "drag_move", x, y, scroll });
export const dragEndMsg = (x: number, y: number, scroll?: boolean) =>
  scroll === undefined ? { type: "drag_end", x, y } : { type: "drag_end", x, y, scroll };
export const scrollMsg = (x: number, y: number, dy: number) => ({ type: "scroll", x, y, dy });
export const keyMsg = (key: string) => ({ type: "key", key });

type Opts = { WsImpl?: any; onNet?: (s: "good" | "bad") => void; onRtt?: (ms: number) => void };

export function makeInputSocket(url: string, opts: Opts = {}) {
  const Ws = opts.WsImpl || (globalThis as any).WebSocket;
  let ws: any = null;
  let retry = 1000;
  let echoTimer: any = null;
  let closed = false;
  let netCb = opts.onNet;

  const connect = () => {
    ws = new Ws(url);
    ws.onopen = () => {
      retry = 1000;
      netCb?.("good");
      clearInterval(echoTimer);
      echoTimer = setInterval(() => {
        if (ws && ws.readyState === (Ws.OPEN ?? 1)) ws.send(JSON.stringify({ type: "echo", t: Date.now() }));
      }, 2000);
    };
    ws.onmessage = (e: any) => {
      try { const m = JSON.parse(e.data); if (m.type === "echo" && m.t) opts.onRtt?.(Date.now() - m.t); } catch {}
    };
    ws.onclose = () => {
      clearInterval(echoTimer);
      if (closed) return;
      netCb?.("bad");
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 30000);
    };
    ws.onerror = () => { try { ws.close(); } catch {} };
  };
  connect();

  return {
    send(msg: object) { if (ws && ws.readyState === (Ws.OPEN ?? 1)) ws.send(JSON.stringify(msg)); },
    close() { closed = true; clearInterval(echoTimer); try { ws?.close(); } catch {} },
    onNet(cb: (s: "good" | "bad") => void) { netCb = cb; },
  };
}
