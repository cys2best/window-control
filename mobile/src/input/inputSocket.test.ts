import { clickMsg, dragMoveMsg, scrollMsg, keyMsg, makeInputSocket } from "./inputSocket";

test("message builders match the server contract", () => {
  expect(clickMsg(0.1, 0.2)).toEqual({ type: "click", x: 0.1, y: 0.2 });
  expect(dragMoveMsg(0.3, 0.4, true)).toEqual({ type: "drag_move", x: 0.3, y: 0.4, scroll: true });
  expect(scrollMsg(0.5, 0.6, -1)).toEqual({ type: "scroll", x: 0.5, y: 0.6, dy: -1 });
  expect(keyMsg("Return")).toEqual({ type: "key", key: "Return" });
});

test("send serializes to JSON over the socket", () => {
  const sent: string[] = [];
  class FakeWs {
    readyState = 1; static OPEN = 1;
    onopen?: () => void; onclose?: () => void; onmessage?: (e: any) => void; onerror?: () => void;
    send(s: string) { sent.push(s); }
    close() {}
    constructor() { setTimeout(() => this.onopen && this.onopen(), 0); }
  }
  const sock = makeInputSocket("ws://h/input", { WsImpl: FakeWs as any });
  sock.send(clickMsg(0.5, 0.5));
  expect(JSON.parse(sent[0])).toEqual({ type: "click", x: 0.5, y: 0.5 });
  sock.close();
});
