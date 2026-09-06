import { connectSignalingViewer } from "./signaling";

class FakeWebSocket {
  url: string;
  sent: string[] = [];
  listeners: Record<string, ((e: any) => void)[]> = {};
  readyState = 0; // CONNECTING
  closed = false;

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, cb: (e: any) => void) {
    this.listeners[type] = this.listeners[type] || [];
    this.listeners[type].push(cb);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.closed = true;
    this.readyState = 3;
  }

  simulateOpen() {
    this.readyState = 1;
    (this.listeners["open"] || []).forEach((cb) => cb({}));
  }

  simulateMessage(data: string) {
    (this.listeners["message"] || []).forEach((cb) => cb({ data }));
  }

  simulateError(err: any = {}) {
    (this.listeners["error"] || []).forEach((cb) => cb(err));
  }

  simulateClose(code = 1000, reason = "") {
    this.readyState = 3;
    (this.listeners["close"] || []).forEach((cb) => cb({ code, reason }));
  }
}

describe("connectSignalingViewer", () => {
  test("sends offer SDP and receives answer", async () => {
    let createdWs: FakeWebSocket | null = null;
    const promise = connectSignalingViewer({
      signalingUrl: "wss://relay.example.com/ws",
      sessionId: "user123.instance1",
      token: "tokenABC",
      offerSdp: "v=0\r\no=offer...",
      WebSocketImpl: (url: string) => {
        createdWs = new FakeWebSocket(url);
        return createdWs;
      },
      timeoutMs: 1000,
    });

    expect(createdWs).not.toBeNull();
    expect(createdWs!.url).toContain("session=user123.instance1");
    expect(createdWs!.url).toContain("role=viewer");
    expect(createdWs!.url).toContain("token=tokenABC");

    createdWs!.simulateOpen();
    expect(createdWs!.sent).toEqual(["v=0\r\no=offer..."]);

    createdWs!.simulateMessage("v=0\r\no=answer...");
    const answer = await promise;
    expect(answer.answerSdp).toBe("v=0\r\no=answer...");

    answer.close();
    expect(createdWs!.closed).toBe(true);
  });

  test("omits token query param if token is empty", async () => {
    let createdWs: FakeWebSocket | null = null;
    const promise = connectSignalingViewer({
      signalingUrl: "wss://relay.example.com/ws",
      sessionId: "user123.instance1",
      token: "",
      offerSdp: "v=0\r\no=offer...",
      WebSocketImpl: (url: string) => {
        createdWs = new FakeWebSocket(url);
        return createdWs;
      },
      timeoutMs: 1000,
    });

    expect(createdWs).not.toBeNull();
    const url = new URL(createdWs!.url);
    expect(url.searchParams.get("session")).toBe("user123.instance1");
    expect(url.searchParams.get("role")).toBe("viewer");
    expect(url.searchParams.has("token")).toBe(false);

    createdWs!.simulateOpen();
    createdWs!.simulateMessage("v=0\r\no=answer...");
    await promise;
  });

  test("rejects when WebSocket emits error", async () => {
    let createdWs: FakeWebSocket | null = null;
    const promise = connectSignalingViewer({
      signalingUrl: "wss://relay.example.com/ws",
      sessionId: "user123.instance1",
      token: "tok",
      offerSdp: "offer",
      WebSocketImpl: (url: string) => {
        createdWs = new FakeWebSocket(url);
        return createdWs;
      },
      timeoutMs: 1000,
    });

    createdWs!.simulateOpen();
    createdWs!.simulateError();
    await expect(promise).rejects.toThrow("Signaling WebSocket error");
  });

  test("rejects when WebSocket closes before answer", async () => {
    let createdWs: FakeWebSocket | null = null;
    const promise = connectSignalingViewer({
      signalingUrl: "wss://relay.example.com/ws",
      sessionId: "user123.instance1",
      token: "tok",
      offerSdp: "offer",
      WebSocketImpl: (url: string) => {
        createdWs = new FakeWebSocket(url);
        return createdWs;
      },
      timeoutMs: 1000,
    });

    createdWs!.simulateOpen();
    createdWs!.simulateClose(1008, "role already taken");
    await expect(promise).rejects.toThrow("Signaling closed: role already taken");
  });

  test("rejects on timeout", async () => {
    jest.useFakeTimers();
    try {
      let createdWs: FakeWebSocket | null = null;
      const promise = connectSignalingViewer({
        signalingUrl: "wss://relay.example.com/ws",
        sessionId: "user123.instance1",
        token: "tok",
        offerSdp: "offer",
        WebSocketImpl: (url: string) => {
          createdWs = new FakeWebSocket(url);
          return createdWs;
        },
        timeoutMs: 500,
      });

      const assertion = expect(promise).rejects.toThrow("Signaling timeout");
      jest.advanceTimersByTime(500);
      await assertion;
      expect(createdWs!.closed).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });

  test("supports legacy onopen/onmessage/onerror/onclose handlers when addEventListener is absent", async () => {
    const fakeWs: any = {
      url: "",
      sent: [] as string[],
      readyState: 0,
      send(data: string) { this.sent.push(data); },
      close() { this.readyState = 3; },
    };

    const promise = connectSignalingViewer({
      signalingUrl: "wss://relay.example.com/ws",
      sessionId: "user123.instance1",
      token: "tok",
      offerSdp: "offer-sdp",
      WebSocketImpl: function (url: string) {
        fakeWs.url = url;
        return fakeWs;
      },
      timeoutMs: 1000,
    });

    expect(typeof fakeWs.onopen).toBe("function");
    expect(typeof fakeWs.onmessage).toBe("function");

    fakeWs.onopen();
    expect(fakeWs.sent).toEqual(["offer-sdp"]);

    fakeWs.onmessage({ data: "answer-sdp" });
    const result = await promise;
    expect(result.answerSdp).toBe("answer-sdp");
  });

  test("sends offer immediately if readyState is already OPEN", async () => {
    const fakeWs: any = {
      url: "",
      sent: [] as string[],
      readyState: 1, // OPEN
      OPEN: 1,
      send(data: string) { this.sent.push(data); },
      close() { this.readyState = 3; },
      addEventListener: jest.fn(),
    };

    const promise = connectSignalingViewer({
      signalingUrl: "wss://relay.example.com/ws",
      sessionId: "user123.instance1",
      offerSdp: "immediate-offer",
      WebSocketImpl: function (url: string) {
        fakeWs.url = url;
        return fakeWs;
      },
      timeoutMs: 1000,
    });

    expect(fakeWs.sent).toEqual(["immediate-offer"]);
    // Since readyState was OPEN, open listener was not attached
    expect(fakeWs.addEventListener).not.toHaveBeenCalledWith("open", expect.any(Function));

    // Deliver message
    const messageHandler = fakeWs.addEventListener.mock.calls.find(
      (call: any[]) => call[0] === "message"
    )[1];
    messageHandler({ data: "immediate-answer" });

    const result = await promise;
    expect(result.answerSdp).toBe("immediate-answer");
  });
});
