import React from "react";
import { render, waitFor, act } from "@testing-library/react-native";
import { PanResponder } from "react-native";
import { Stream } from "./Stream";
import * as SC from "../api/ServerContext";
import * as Whep from "../webrtc/whep";
import * as Adaptive from "../quality/adaptive";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

jest.mock("expo-screen-orientation", () => ({
  lockAsync: jest.fn(),
  OrientationLock: { LANDSCAPE: "LANDSCAPE", PORTRAIT_UP: "PORTRAIT_UP" },
}));

jest.mock("react-native-webrtc", () => ({
  RTCView: ({ streamURL }: any) => {
    const { Text } = require("react-native");
    return require("react").createElement(Text, { testID: "stream-video" }, streamURL);
  },
  RTCPeerConnection: function () { return {}; },
}));

jest.mock("react-native-gesture-handler", () => {
  const actual = jest.requireActual("react-native-gesture-handler");
  const fakePanGesture = { runOnJS: () => fakePanGesture, activeOffsetY: () => fakePanGesture, failOffsetX: () => fakePanGesture, onEnd: () => fakePanGesture };
  return {
    ...actual,
    Gesture: { ...actual.Gesture, Pan: () => fakePanGesture },
    GestureDetector: ({ children }: any) => children,
  };
});

function makeFakeSession() {
  return {
    pc: { getStats: jest.fn(async () => new Map()) },
    input: {
      dragStart: jest.fn(),
      dragMove: jest.fn(),
      dragEnd: jest.fn(),
      scroll: jest.fn(),
      send: jest.fn(),
      close: jest.fn(),
    },
    close: jest.fn(async () => {}),
  };
}

function selectResp(overrides: any = {}) {
  return {
    ok: true, id: "A", serial: "A", name: "A", w: 1080, h: 1920,
    whep_url: "http://h/whep/A", whep_token: "tok-A",
    signaling_url: null, signaling_token: null,
    ice_servers: [{ urls: "stun:h:3478" }],
    generation: 1,
    ...overrides,
  };
}

afterEach(() => jest.restoreAllMocks());

function touch(x: number, y: number, count = 1) {
  return {
    nativeEvent: {
      locationX: x,
      locationY: y,
      touches: Array.from({ length: count }, () => ({ locationX: x, locationY: y })),
    },
  };
}

test("connects via client.select() + connectWhep() on mount, not the old input socket", async () => {
  const session = makeFakeSession();
  const connectWhepSpy = jest.spyOn(Whep, "connectWhep").mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await act(async () => {
    render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  });

  await waitFor(() => expect(client.select).toHaveBeenCalledWith("A"));
  await waitFor(() => expect(connectWhepSpy).toHaveBeenCalledWith(expect.objectContaining({
    whepUrl: "http://h/whep/A",
    whepToken: "tok-A",
    iceServers: [{ urls: "stun:h:3478" }],
  })));
  expect((client as any).inputWsUrl).toBeUndefined();
});

test("a disconnected WHEP state (closed input channel) triggers a fresh select/reconnect", async () => {
  const firstSession = makeFakeSession();
  const secondSession = makeFakeSession();
  let callCount = 0;
  const connectWhepSpy = jest.spyOn(Whep, "connectWhep").mockImplementation((opts: any) => {
    callCount += 1;
    if (callCount === 1) {
      // Fire the disconnected state asynchronously, like a real closed
      // input channel would, to trigger Stream's fresh select()/reconnect.
      setTimeout(() => opts.onState("disconnected"), 0);
      return Promise.resolve(firstSession as any);
    }
    return Promise.resolve(secondSession as any);
  });
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await act(async () => {
    render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  });

  await waitFor(() => expect(client.select).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(connectWhepSpy).toHaveBeenCalledTimes(2));
});

test("terminal input failure releases an active drag before reconnecting", async () => {
  const session = makeFakeSession();
  const createPanResponder = jest.spyOn(PanResponder, "create").mockImplementation(() => ({ panHandlers: {} }) as any);
  let onState!: (state: "connecting" | "connected" | "disconnected") => void;
  jest.spyOn(Whep, "connectWhep").mockImplementation((opts: any) => {
    onState = opts.onState;
    return Promise.resolve(session as any);
  });
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  await waitFor(() => expect(session.input.send).toHaveBeenCalledWith({ type: "idr" }));
  const pan = createPanResponder.mock.calls[0][0] as any;
  await act(async () => { pan.onPanResponderGrant(touch(10, 20)); });
  await act(async () => { pan.onPanResponderMove(touch(40, 60), { dx: 30, dy: 40 }); });

  await act(async () => { onState("disconnected"); });
  expect(session.input.dragEnd).toHaveBeenCalledTimes(1);
  expect(client.select).toHaveBeenCalledTimes(2);
});

test("adaptive stall leaves the connected peer in place", async () => {
  const session = makeFakeSession();
  let adaptiveOptions: any;
  jest.spyOn(Adaptive, "makeAdaptive").mockImplementation((opts: any) => {
    adaptiveOptions = opts;
    return { start: jest.fn(), stop: jest.fn(), pin: jest.fn(), setAuto: jest.fn() } as any;
  });
  jest.spyOn(Whep, "connectWhep").mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  await waitFor(() => expect(adaptiveOptions).toBeDefined());

  expect(adaptiveOptions.onStall).toBeUndefined();
  expect(client.select).toHaveBeenCalledTimes(1);
  expect(Whep.connectWhep).toHaveBeenCalledTimes(1);
  expect(session.close).not.toHaveBeenCalled();
});

test("does not reconnect after unmount closes the active session", async () => {
  const session = makeFakeSession();
  jest.spyOn(Whep, "connectWhep").mockImplementation((opts: any) => {
    session.close.mockImplementation(async () => { opts.onState("disconnected"); });
    return Promise.resolve(session as any);
  });
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  await waitFor(() => expect(Whep.connectWhep).toHaveBeenCalledTimes(1));
  await act(async () => { await result.unmount(); });
  expect(client.select).toHaveBeenCalledTimes(1);
});

test("unmount releases an active drag before closing its session", async () => {
  const session = makeFakeSession();
  const createPanResponder = jest.spyOn(PanResponder, "create").mockImplementation(() => ({ panHandlers: {} }) as any);
  jest.spyOn(Whep, "connectWhep").mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  await waitFor(() => expect(session.input.send).toHaveBeenCalledWith({ type: "idr" }));
  const pan = createPanResponder.mock.calls[0][0] as any;
  await act(async () => { pan.onPanResponderGrant(touch(10, 20)); });
  await act(async () => { await result.unmount(); });

  expect(session.input.dragEnd).toHaveBeenCalledTimes(1);
  expect(session.input.dragEnd.mock.invocationCallOrder[0])
    .toBeLessThan(session.close.mock.invocationCallOrder[0]);
});

test("keeps the current video visible until a replacement session is ready", async () => {
  const firstSession = makeFakeSession();
  const secondSession = makeFakeSession();
  let resolveSecond!: (session: any) => void;
  const secondReady = new Promise<any>((resolve) => { resolveSecond = resolve; });
  let calls = 0;
  jest.spyOn(Whep, "connectWhep").mockImplementation((opts: any) => {
    calls += 1;
    if (calls === 1) {
      opts.onStream({ toURL: () => "old-stream" });
      return Promise.resolve(firstSession as any);
    }
    opts.onStream({ toURL: () => "new-stream" });
    return secondReady;
  });
  const client = {
    select: jest.fn().mockImplementation(async (serial: string) => selectResp({ serial })),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  await waitFor(() => expect(result.getByTestId("stream-video").props.children).toBe("old-stream"));
  await result.rerender(<Stream route={{ params: { serial: "B" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  await waitFor(() => expect(Whep.connectWhep).toHaveBeenCalledTimes(2));
  expect(result.getByTestId("stream-video").props.children).toBe("old-stream");

  resolveSecond(secondSession);
  await waitFor(() => expect(result.getByTestId("stream-video").props.children).toBe("new-stream"));
});

test("keys route through the session's input sender, not a socket", async () => {
  const session = makeFakeSession();
  jest.spyOn(Whep, "connectWhep").mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  let result: any;
  await act(async () => {
    result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  });
  await waitFor(() => expect(session.input).toBeDefined());

  const textInput = result!.getByTestId("stream-key-input");
  await act(async () => {
    textInput.props.onKeyPress({ nativeEvent: { key: "Enter" } });
  });
  expect(session.input.send).toHaveBeenCalledWith({ type: "key", key: "Return" });
});

test("starts input health over the ready session sender", async () => {
  jest.useFakeTimers();
  try {
    const session = makeFakeSession();
    jest.spyOn(Whep, "connectWhep").mockResolvedValue(session as any);
    const client = {
      select: jest.fn().mockResolvedValue(selectResp()),
      instances: jest.fn().mockResolvedValue([]),
      setQuality: jest.fn(),
      keyframe: jest.fn(),
    };
    jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

    await act(async () => {
      await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
    });

    await waitFor(() => expect(session.input.send).toHaveBeenCalledWith({ type: "idr" }));
    await act(async () => { jest.advanceTimersByTime(2000); });
    expect(session.input.send).toHaveBeenCalledWith(expect.objectContaining({ type: "echo" }));
  } finally {
    jest.useRealTimers();
  }
});

test("starts a drag at touch begin, ends it when a second finger starts scrolling, and releases it on cancellation", async () => {
  const session = makeFakeSession();
  const createPanResponder = jest.spyOn(PanResponder, "create").mockImplementation(() => ({ panHandlers: {} }) as any);
  jest.spyOn(Whep, "connectWhep").mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} />);
  await waitFor(() => expect(Whep.connectWhep).toHaveBeenCalled());
  const pan = createPanResponder.mock.calls[0][0] as any;

  await act(async () => { pan.onPanResponderGrant(touch(10, 20)); });
  expect(session.input.dragStart).toHaveBeenCalledTimes(1);
  await act(async () => { pan.onPanResponderMove(touch(11, 21, 2), { dx: 1, dy: 1 }); });
  expect(session.input.dragEnd).toHaveBeenCalledTimes(1);
  await act(async () => { pan.onPanResponderTerminate(touch(12, 22)); });
  expect(session.input.dragEnd).toHaveBeenCalledTimes(1);

  await act(async () => { pan.onPanResponderGrant(touch(20, 30)); });
  await act(async () => { pan.onPanResponderTerminate(touch(20, 30)); });
  expect(session.input.dragEnd).toHaveBeenCalledTimes(2);
});
