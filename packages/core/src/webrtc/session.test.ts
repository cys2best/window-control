import { connectEngineSession } from "./session";

function fakePc() {
  const listeners: Record<string, Function[]> = {};
  const dc = {
    readyState: "connecting",
    bufferedAmount: 0,
    sent: [] as string[],
    closed: false,
    listeners: {} as Record<string, Function[]>,
    send(payload: string) {
      this.sent.push(payload);
    },
    close() {
      this.closed = true;
      this.readyState = "closed";
    },
    addEventListener(type: string, fn: Function) {
      (this.listeners[type] ||= []).push(fn);
    },
    _fire(type: string, e?: any) {
      (this.listeners[type] || []).forEach((f) => f(e));
    },
  };
  const pc = {
    iceConnectionState: "new",
    iceGatheringState: "complete",
    localDescription: { sdp: "OFFER" },
    dc,
    calls: [] as string[],
    listeners,
    addEventListener: (k: string, f: Function) => {
      (listeners[k] ||= []).push(f);
    },
    removeEventListener: () => {},
    addTransceiver: (...args: any[]) => {
      pc.calls.push("addTransceiver");
      return { receiver: {} };
    },
    createDataChannel: (...args: any[]) => {
      pc.calls.push("createDataChannel:" + args[0]);
      return dc;
    },
    createOffer: async () => {
      pc.calls.push("createOffer");
      return { type: "offer", sdp: "OFFER" };
    },
    setLocalDescription: async () => {
      pc.calls.push("setLocalDescription");
    },
    setRemoteDescription: jest.fn(async () => {
      pc.calls.push("setRemoteDescription");
    }),
    close: jest.fn(),
    _fire: (k: string, e: any) => (listeners[k] || []).forEach((f) => f(e)),
  } as any;
  return pc;
}

describe("connectEngineSession", () => {
  test("connectEngineSession adopts the faster transport and closes the other (local wins fast)", async () => {
    let localClosed = false;
    let publicClosed = false;

    const session = await connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      authToken: "jwt",
      startLocalImpl: async () => {
        // Local wins fast
        return {
          kind: "local",
          stream: { id: "stream-local" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {
            localClosed = true;
          },
        };
      },
      startPublicImpl: async () => {
        await new Promise((r) => setTimeout(r, 50));
        return {
          kind: "public",
          stream: { id: "stream-public" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {
            publicClosed = true;
          },
        };
      },
    });

    expect(session.kind).toBe("local");
    expect(session.stream).toEqual({ id: "stream-local" });

    // The slower public attempt should be closed
    await new Promise((r) => setTimeout(r, 80));
    expect(publicClosed).toBe(true);
    expect(localClosed).toBe(false);

    // Closing the adopted session closes local
    await session.close();
    expect(localClosed).toBe(true);
  });

  test("connectEngineSession adopts the faster transport and closes the other (public wins fast)", async () => {
    let localClosed = false;
    let publicClosed = false;

    const session = await connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      authToken: "jwt",
      startLocalImpl: async () => {
        await new Promise((r) => setTimeout(r, 60));
        return {
          kind: "local",
          stream: { id: "stream-local" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {
            localClosed = true;
          },
        };
      },
      startPublicImpl: async () => {
        // Public wins fast
        return {
          kind: "public",
          stream: { id: "stream-public" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {
            publicClosed = true;
          },
        };
      },
    });

    expect(session.kind).toBe("public");
    expect(session.stream).toEqual({ id: "stream-public" });

    // Slower local attempt should be closed
    await new Promise((r) => setTimeout(r, 80));
    expect(localClosed).toBe(true);
    expect(publicClosed).toBe(false);

    // Closing the adopted session closes public
    await session.close();
    expect(publicClosed).toBe(true);
  });

  test("falls back to public when local fails", async () => {
    let publicClosed = false;

    const session = await connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      authToken: "jwt",
      startLocalImpl: async () => {
        throw new Error("WHEP 404 Not Found");
      },
      startPublicImpl: async () => {
        await new Promise((r) => setTimeout(r, 30));
        return {
          kind: "public",
          stream: { id: "stream-pub" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {
            publicClosed = true;
          },
        };
      },
    });

    expect(session.kind).toBe("public");
    expect(publicClosed).toBe(false);
  });

  test("falls back to local when public fails", async () => {
    let localClosed = false;

    const session = await connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      authToken: "jwt",
      startLocalImpl: async () => {
        await new Promise((r) => setTimeout(r, 30));
        return {
          kind: "local",
          stream: { id: "stream-loc" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {
            localClosed = true;
          },
        };
      },
      startPublicImpl: async () => {
        throw new Error("Signaling connection closed");
      },
    });

    expect(session.kind).toBe("local");
    expect(localClosed).toBe(false);
  });

  test("rejects if all configured transports fail", async () => {
    const promise = connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      authToken: "jwt",
      startLocalImpl: async () => {
        throw new Error("Local failed");
      },
      startPublicImpl: async () => {
        throw new Error("Public failed");
      },
    });

    await expect(promise).rejects.toThrow("All engine session attempts failed");
  });

  test("rejects if no transports are configured", async () => {
    const promise = connectEngineSession({
      selection: {
        whep_url: "",
        whep_token: "",
        signaling_url: null,
        public_session: null,
        ice_servers: [],
      } as any,
      authToken: "jwt",
    });

    await expect(promise).rejects.toThrow("No engine session transport is configured");
  });

  test("only runs local when public is not configured", async () => {
    let publicCalled = false;
    let localCalled = false;

    const session = await connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: null,
        public_session: null,
        ice_servers: [],
      } as any,
      startLocalImpl: async () => {
        localCalled = true;
        return {
          kind: "local",
          stream: {} as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {},
        };
      },
      startPublicImpl: async () => {
        publicCalled = true;
        return {} as any;
      },
    });

    expect(localCalled).toBe(true);
    expect(publicCalled).toBe(false);
    expect(session.kind).toBe("local");
  });

  test("only runs public when local is not configured", async () => {
    let publicCalled = false;
    let localCalled = false;

    const session = await connectEngineSession({
      selection: {
        whep_url: "",
        whep_token: "",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      startLocalImpl: async () => {
        localCalled = true;
        return {} as any;
      },
      startPublicImpl: async () => {
        publicCalled = true;
        return {
          kind: "public",
          stream: {} as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {},
        };
      },
    });

    expect(publicCalled).toBe(true);
    expect(localCalled).toBe(false);
    expect(session.kind).toBe("public");
  });

  test("forwards callbacks (onStream, onInputRtt, onState) from winner", async () => {
    const states: string[] = [];
    const streams: any[] = [];
    const rtts: number[] = [];

    const session = await connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      onState: (st) => states.push(st),
      onStream: (st) => streams.push(st),
      onInputRtt: (ms) => rtts.push(ms),
      startLocalImpl: async () => {
        return {
          kind: "local",
          stream: { id: "local-vid" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {},
        };
      },
      startPublicImpl: async () => {
        await new Promise((r) => setTimeout(r, 50));
        return {
          kind: "public",
          stream: { id: "pub-vid" } as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {},
        };
      },
    });

    expect(session.kind).toBe("local");
    expect(states).toContain("connecting");
    expect(states).toContain("connected");
    expect(streams).toEqual([{ id: "local-vid" }]);
  });

  test("uses defaultStartLocal with connectWhepImpl when startLocalImpl not specified", async () => {
    let whepCalled = false;
    const session = await connectEngineSession({
      selection: {
        whep_url: "http://192.168.1.10:8080/whep",
        whep_token: "tokLocal",
        signaling_url: null,
        public_session: null,
        ice_servers: [],
      } as any,
      connectWhepImpl: async (opts) => {
        whepCalled = true;
        opts.onStream({ id: "whep-stream" });
        return {
          pc: {} as any,
          input: { close: () => {}, send: () => {} } as any,
          close: async () => {},
        };
      },
    });

    expect(whepCalled).toBe(true);
    expect(session.kind).toBe("local");
    expect(session.stream).toEqual({ id: "whep-stream" });
  });

  test("uses defaultStartPublic with connectSignalingViewerImpl when startPublicImpl not specified", async () => {
    const pc = fakePc();
    let signalingCalled = false;

    const promise = connectEngineSession({
      selection: {
        whep_url: "",
        whep_token: "",
        signaling_url: "wss://relay.example.com/ws",
        public_session: "user.inst1",
        ice_servers: [],
      } as any,
      authToken: "auth-token",
      RTCImpl: function () {
        return pc;
      } as any,
      connectSignalingViewerImpl: async (opts: any) => {
        signalingCalled = true;
        expect(opts.signalingUrl).toBe("wss://relay.example.com/ws");
        expect(opts.sessionId).toBe("user.inst1");
        expect(opts.token).toBe("auth-token");
        return {
          answerSdp: "REMOTE_ANSWER",
          close: () => {},
        };
      },
    });

    await Promise.resolve();
    await Promise.resolve();
    // Simulate public connection events
    pc._fire("track", { track: { kind: "video" }, streams: [{ id: "public-stream" }] });
    pc.iceConnectionState = "connected";
    pc._fire("iceconnectionstatechange", {});
    pc.dc._fire("open", {});

    const session = await promise;
    expect(signalingCalled).toBe(true);
    expect(session.kind).toBe("public");
    expect(session.stream).toEqual({ id: "public-stream" });
  });
});
