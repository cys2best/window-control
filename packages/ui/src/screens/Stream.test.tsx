import React from "react";
import { render, waitFor, act, fireEvent } from "@testing-library/react-native";
import { PanResponder, StyleSheet } from "react-native";
import { Stream } from "./Stream";
import * as SC from "@wc/core";
import * as Core from "@wc/core";
import * as Adaptive from "@wc/core";

jest.mock("@wc/core", () => {
  const actual = jest.requireActual("@wc/core");
  return {
    ...actual,
    useServer: jest.fn(),
    connectEngineSession: jest.fn(),
    makeAdaptive: jest.fn(),
    makeTelemetrySampler: jest.fn(),
    makeStallWatchdog: jest.fn(),
  };
});

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

jest.mock("expo-screen-orientation", () => ({
  lockAsync: jest.fn(),
  OrientationLock: { LANDSCAPE: "LANDSCAPE", PORTRAIT_UP: "PORTRAIT_UP" },
}));

function FakeRTCPeerConnection() { return {}; }
function FakeVideoView({ stream }: any) {
  const { Text } = require("react-native");
  return require("react").createElement(Text, { testID: "stream-video" }, String((stream as any).toURL?.() ?? stream));
}

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
    kind: "local" as const,
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

function makeFakeSampler() {
  return { start: jest.fn(), stop: jest.fn(), setInputRtt: jest.fn() };
}

function makeFakeAdaptive() {
  return { start: jest.fn(), stop: jest.fn(), pin: jest.fn(), setAuto: jest.fn() };
}

function selectResp(overrides: any = {}) {
  return {
    ok: true, id: "A", serial: "A", name: "A", w: 1080, h: 1920,
    whep_url: "http://h/whep/A", whep_token: "tok-A",
    ice_servers: [{ urls: "stun:h:3478" }],
    generation: 1,
    ...overrides,
  };
}

afterEach(() => {
  jest.restoreAllMocks();
  jest.clearAllMocks();
});

beforeEach(() => {
  (Core.makeTelemetrySampler as jest.Mock).mockReturnValue(makeFakeSampler());
  (Adaptive.makeAdaptive as jest.Mock).mockReturnValue(makeFakeAdaptive());
  (Core.makeStallWatchdog as jest.Mock).mockReturnValue({ start: jest.fn(), stop: jest.fn() });
});

function touch(x: number, y: number, count = 1) {
  return {
    nativeEvent: {
      locationX: x,
      locationY: y,
      touches: Array.from({ length: count }, () => ({ locationX: x, locationY: y })),
    },
  };
}

test("connects via client.select() + connectEngineSession() on mount, not the old input socket", async () => {
  const session = makeFakeSession();
  const connectEngineSessionSpy = (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", authToken: "auth-tok-123", client, setBase: jest.fn(), ready: true } as any);

  await act(async () => {
    render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  });

  await waitFor(() => expect(client.select).toHaveBeenCalledWith("A"));
  await waitFor(() => expect(connectEngineSessionSpy).toHaveBeenCalledWith(expect.objectContaining({
    selection: expect.objectContaining({
      whep_url: "http://h/whep/A",
    }),
    RTCImpl: FakeRTCPeerConnection,
  })));
  expect((client as any).inputWsUrl).toBeUndefined();
});

test("keeps the video surface clear of both edge controls", async () => {
  const session = makeFakeSession();
  (Core.connectEngineSession as jest.Mock).mockImplementation(async (opts: any) => {
    opts.onStream({ toURL: () => "visible-stream" });
    return session as any;
  });
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({
    base: "http://h", authToken: "auth-tok-123", client, setBase: jest.fn(), ready: true,
  } as any);

  const result = await render(
    <Stream route={{ params: { serial: "A" } }}
      navigation={{ navigate: jest.fn(), setParams: jest.fn() }}
      RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />,
  );

  const video = await result.findByTestId("stream-video");
  expect(StyleSheet.flatten(video.parent?.props.style)).toEqual(
    expect.objectContaining({ marginHorizontal: 68 }),
  );
});

test("a disconnected state (closed input channel) triggers a fresh select/reconnect", async () => {
  const firstSession = makeFakeSession();
  const secondSession = makeFakeSession();
  let callCount = 0;
  const connectEngineSessionSpy = (Core.connectEngineSession as jest.Mock).mockImplementation((opts: any) => {
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
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", authToken: null, client, setBase: jest.fn(), ready: true } as any);

  await act(async () => {
    render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  });

  await waitFor(() => expect(client.select).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(connectEngineSessionSpy).toHaveBeenCalledTimes(2));
});

test("terminal input failure releases an active drag before reconnecting", async () => {
  const session = makeFakeSession();
  const createPanResponder = jest.spyOn(PanResponder, "create").mockImplementation(() => ({ panHandlers: {} }) as any);
  let onState!: (state: "connecting" | "connected" | "disconnected") => void;
  (Core.connectEngineSession as jest.Mock).mockImplementation((opts: any) => {
    onState = opts.onState;
    return Promise.resolve(session as any);
  });
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(session.input.send).toHaveBeenCalledWith({ type: "idr" }));
  const pan = createPanResponder.mock.calls[0][0] as any;
  await act(async () => { pan.onPanResponderGrant(touch(10, 20)); });
  await act(async () => { pan.onPanResponderMove(touch(40, 60), { dx: 30, dy: 40 }); });

  await act(async () => { onState("disconnected"); });
  expect(session.input.dragEnd).toHaveBeenCalledTimes(1);
  expect(client.select).toHaveBeenCalledTimes(2);
});

test("adaptive stall requests a keyframe and leaves the connected peer in place", async () => {
  const session = makeFakeSession();
  let adaptiveOptions: any;
  const adaptive = makeFakeAdaptive();
  (Adaptive.makeAdaptive as jest.Mock).mockImplementation((opts: any) => {
    adaptiveOptions = opts;
    return adaptive as any;
  });
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(adaptiveOptions).toBeDefined());

  await waitFor(() => expect(adaptive.start).toHaveBeenCalledWith(session.pc));
  session.input.send.mockClear();
  adaptiveOptions.onStall();

  expect(session.input.send).toHaveBeenCalledWith({ type: "idr" });
  expect(client.select).toHaveBeenCalledTimes(1);
  expect(Core.connectEngineSession).toHaveBeenCalledTimes(1);
  expect(session.close).not.toHaveBeenCalled();
});

test("does not reconnect after unmount closes the active session", async () => {
  const session = makeFakeSession();
  (Core.connectEngineSession as jest.Mock).mockImplementation((opts: any) => {
    session.close.mockImplementation(async () => { opts.onState("disconnected"); });
    return Promise.resolve(session as any);
  });
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(Core.connectEngineSession).toHaveBeenCalledTimes(1));
  await act(async () => { await result.unmount(); });
  expect(client.select).toHaveBeenCalledTimes(1);
});

test("unmount releases an active drag before closing its session", async () => {
  const session = makeFakeSession();
  const createPanResponder = jest.spyOn(PanResponder, "create").mockImplementation(() => ({ panHandlers: {} }) as any);
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
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
  (Core.connectEngineSession as jest.Mock).mockImplementation((opts: any) => {
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
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(result.getByTestId("stream-video").props.children).toBe("old-stream"));
  await result.rerender(<Stream route={{ params: { serial: "B" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(Core.connectEngineSession).toHaveBeenCalledTimes(2));
  expect(result.getByTestId("stream-video").props.children).toBe("old-stream");

  resolveSecond(secondSession);
  await waitFor(() => expect(result.getByTestId("stream-video").props.children).toBe("new-stream"));
});

test("keys route through the session's input sender, not a socket", async () => {
  const session = makeFakeSession();
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  let result: any;
  await act(async () => {
    result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
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
    (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
    const client = {
      select: jest.fn().mockResolvedValue(selectResp()),
      instances: jest.fn().mockResolvedValue([]),
      setQuality: jest.fn(),
      keyframe: jest.fn(),
    };
    (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

    await act(async () => {
      await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
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
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(Core.connectEngineSession).toHaveBeenCalled());
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

test("a two-finger gesture at or below the HUD threshold never scrolls", async () => {
  const session = makeFakeSession();
  const createPanResponder = jest.spyOn(PanResponder, "create").mockImplementation(() => ({ panHandlers: {} }) as any);
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const result = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(Core.connectEngineSession).toHaveBeenCalled());
  const pan = createPanResponder.mock.calls[0][0] as any;

  await act(async () => { pan.onPanResponderGrant(touch(10, 20, 2)); });
  await act(async () => { pan.onPanResponderMove(touch(12, 22, 2), { dx: 2, dy: 2 }); });
  await act(async () => { pan.onPanResponderMove(touch(13, 23, 2), { dx: 3, dy: 3 }); });
  await act(async () => { pan.onPanResponderRelease(touch(13, 23, 2)); });

  expect(session.input.scroll).not.toHaveBeenCalled();
  expect(result.getByText("DECODE")).toBeTruthy();
});

test("two-finger scroll begins after crossing the HUD threshold with a fresh baseline", async () => {
  const session = makeFakeSession();
  const createPanResponder = jest.spyOn(PanResponder, "create").mockImplementation(() => ({ panHandlers: {} }) as any);
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()),
    instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(),
    keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(Core.connectEngineSession).toHaveBeenCalled());
  const pan = createPanResponder.mock.calls[0][0] as any;

  await act(async () => { pan.onPanResponderGrant(touch(10, 20, 2)); });
  await act(async () => { pan.onPanResponderMove(touch(14, 24, 2), { dx: 0, dy: 4 }); });
  expect(session.input.scroll).not.toHaveBeenCalled();
  await act(async () => { pan.onPanResponderMove(touch(15, 25, 2), { dx: 0, dy: 5 }); });

  expect(session.input.scroll).toHaveBeenCalledTimes(1);
});

test("starts the winning telemetry sampler and sends it input RTT readings", async () => {
  const session = makeFakeSession();
  const sampler = makeFakeSampler();
  let onInputRtt!: (ms: number) => void;
  (Core.makeTelemetrySampler as jest.Mock).mockReturnValue(sampler);
  (Core.connectEngineSession as jest.Mock).mockImplementation((opts: any) => {
    onInputRtt = opts.onInputRtt;
    return Promise.resolve(session as any);
  });
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()), instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(), keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(sampler.start).toHaveBeenCalledTimes(1));
  await act(async () => { onInputRtt(23); });

  expect(Core.makeTelemetrySampler).toHaveBeenCalledWith(expect.objectContaining({ pc: session.pc, transport: "local" }));
  expect(sampler.setInputRtt).toHaveBeenCalledWith(23);
});

test("stops the prior telemetry sampler before a replacement session becomes current", async () => {
  const firstSession = makeFakeSession();
  const secondSession = makeFakeSession();
  const firstSampler = makeFakeSampler();
  const secondSampler = makeFakeSampler();
  (Core.makeTelemetrySampler as jest.Mock).mockReturnValueOnce(firstSampler).mockReturnValueOnce(secondSampler);
  (Core.connectEngineSession as jest.Mock)
    .mockResolvedValueOnce(firstSession as any)
    .mockResolvedValueOnce(secondSession as any);
  const client = {
    select: jest.fn().mockImplementation(async (serial: string) => selectResp({ serial })), instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(), keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const view = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(firstSampler.start).toHaveBeenCalledTimes(1));
  await view.rerender(<Stream route={{ params: { serial: "B" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(secondSampler.start).toHaveBeenCalledTimes(1));

  expect(firstSampler.stop).toHaveBeenCalledTimes(1);
  expect(firstSampler.stop.mock.invocationCallOrder[0]).toBeLessThan(secondSampler.start.mock.invocationCallOrder[0]);
});

test("applies a saved manual quality through the adaptive pin path when the session wins", async () => {
  const session = makeFakeSession();
  const adaptive = makeFakeAdaptive();
  (Adaptive.makeAdaptive as jest.Mock).mockReturnValue(adaptive);
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()), instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(), keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({
    base: "http://h", client, setBase: jest.fn(), ready: true,
    preferences: { quality: "1080", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true },
  } as any);

  await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(adaptive.pin).toHaveBeenCalledWith("1080"));
});

test("reconnecting after a saved quality change pins the replacement controller to the latest tier", async () => {
  const firstSession = makeFakeSession();
  const replacementSession = makeFakeSession();
  const firstAdaptive = makeFakeAdaptive();
  const replacementAdaptive = makeFakeAdaptive();
  let onState!: (state: "connecting" | "connected" | "disconnected") => void;
  let preferences: Core.StreamPreferences = { quality: "720", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true };
  (Core.connectEngineSession as jest.Mock).mockImplementationOnce((opts: any) => {
    onState = opts.onState;
    return Promise.resolve(firstSession as any);
  }).mockResolvedValueOnce(replacementSession as any);
  (Adaptive.makeAdaptive as jest.Mock).mockReturnValueOnce(firstAdaptive).mockReturnValueOnce(replacementAdaptive);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()), instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(), keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockImplementation(() => ({ base: "http://h", client, setBase: jest.fn(), ready: true, preferences }) as any);
  const navigation = { navigate: jest.fn(), setParams: jest.fn() };
  const view = await render(<Stream route={{ params: { serial: "A" } }} navigation={navigation} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(firstAdaptive.pin).toHaveBeenCalledWith("720"));

  preferences = { ...preferences, quality: "1080" };
  await view.rerender(<Stream route={{ params: { serial: "A" } }} navigation={navigation} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(firstAdaptive.pin).toHaveBeenCalledWith("1080"));
  expect(Core.connectEngineSession).toHaveBeenCalledTimes(1);
  await act(async () => { onState("disconnected"); });

  await waitFor(() => expect(Core.connectEngineSession).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(replacementAdaptive.pin).toHaveBeenCalledWith("1080"));
});

test("a re-render with a new navigation object keeps the live session", async () => {
  // The web pages build `navigation` inline, so it is a new object on every
  // render of the page (for example each 30s host probe). That must not tear
  // the stream down and reconnect it.
  const session = makeFakeSession();
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()), instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(), keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);
  const element = () => (
    <Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), replace: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />
  );

  const view = await render(element());
  await waitFor(() => expect(Core.connectEngineSession).toHaveBeenCalledTimes(1));

  await view.rerender(element());
  await view.rerender(element());

  expect(client.select).toHaveBeenCalledTimes(1);
  expect(client.instances).toHaveBeenCalledTimes(1);
  expect(Core.connectEngineSession).toHaveBeenCalledTimes(1);
  expect(session.close).not.toHaveBeenCalled();
});

test("the swap control moves below the diagnostic HUD while it is shown", async () => {
  const { StyleSheet } = require("react-native");
  const session = makeFakeSession();
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()), instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(), keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({
    base: "http://h", client, setBase: jest.fn(), ready: true,
    preferences: { quality: "auto", showHudOnConnect: true, haptics: false, hideRailWhilePlaying: false },
  } as any);

  const view = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  const hud = await view.findByTestId("diagnostic-hud");
  const slotTop = () => StyleSheet.flatten(view.getByTestId("swap-control").props.style).top;

  await fireEvent(hud, "layout", { nativeEvent: { layout: { x: 0, y: 0, width: 68, height: 204 } } });
  expect(slotTop()).toBe(204);

  await fireEvent.press(view.getByLabelText(/diagnostics|signal/i));
  await waitFor(() => expect(view.queryByTestId("diagnostic-hud")).toBeNull());
  expect(slotTop()).toBe(0);
});

test("a decoder stall asks the host for a keyframe and leaves the peer connected", async () => {
  const session = makeFakeSession();
  const watchdog = { start: jest.fn(), stop: jest.fn() };
  let watchdogOptions: any;
  (Core.makeStallWatchdog as jest.Mock).mockImplementation((opts: any) => { watchdogOptions = opts; return watchdog; });
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(session as any);
  const client = {
    select: jest.fn().mockResolvedValue(selectResp()), instances: jest.fn().mockResolvedValue([]),
    setQuality: jest.fn(), keyframe: jest.fn(),
  };
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);

  const view = await render(<Stream route={{ params: { serial: "A" } }} navigation={{ navigate: jest.fn(), setParams: jest.fn() }} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView} />);
  await waitFor(() => expect(watchdog.start).toHaveBeenCalledTimes(1));
  expect(watchdogOptions.pc).toBe(session.pc);
  const keyframesBefore = client.keyframe.mock.calls.length;

  watchdogOptions.onStall();

  expect(client.keyframe.mock.calls.slice(keyframesBefore)).toEqual([["A"]]);
  expect(client.select).toHaveBeenCalledTimes(1);
  expect(session.close).not.toHaveBeenCalled();

  await view.unmount();
  expect(watchdog.stop).toHaveBeenCalled();
});

function remoteResp(overrides: any = {}) {
  return { kind: "remote", ok: true, id: "A", serial: "A", name: "A", w: 1920, h: 1080, tier: "720", session_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", generation: 1,
    ice_servers: [{urls:["turn:relay"],username:"old",credential:"old"}], expires_at: Math.floor(Date.now()/1000)+3600, renew_after:3300, relay_available:true, ...overrides };
}
function remoteClient() {
  return { kind:"remote", select:jest.fn().mockResolvedValue(remoteResp()), instances:jest.fn().mockResolvedValue([]), closeSession:jest.fn(async()=>{}), setQuality:jest.fn(async(_serial: string, _tier: string)=>{}), keyframe:jest.fn(async()=>{}) };
}
const remoteElement = (serial = "A") => <Stream route={{params:{serial}}} navigation={{navigate:jest.fn(),replace:jest.fn(),setParams:jest.fn()}} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView}/>;

test("remote select and negotiation share one abort signal and deadline from before select", async () => {
  jest.useFakeTimers();
  try {
    const client=remoteClient(); let resolve!: (v:any)=>void;
    client.select.mockImplementation(()=>new Promise(r=>{resolve=r;}));
    (SC.useServer as jest.Mock).mockReturnValue({client});
    (Core.connectEngineSession as jest.Mock).mockResolvedValue({...makeFakeSession(),kind:"remote"});
    const before=performance.now(); const view=await render(remoteElement());
    await act(async()=>{jest.advanceTimersByTime(12000);resolve(remoteResp());});
    expect(client.select.mock.calls[0][1]).toEqual(expect.objectContaining({deadline:before+30000,signal:expect.anything()}));
    expect((Core.connectEngineSession as jest.Mock).mock.calls[0][0]).toEqual(expect.objectContaining({client,deadline:before+30000,signal:client.select.mock.calls[0][1].signal}));
    await view.unmount(); expect(client.select.mock.calls[0][1].signal.aborted).toBe(true);
  } finally {jest.useRealTimers();}
});
test("a superseded late remote selection is closed exactly and never starts a peer", async()=>{
  const client=remoteClient(); let resolve!: (v:any)=>void;
  client.select.mockImplementationOnce(()=>new Promise(r=>{resolve=r;}));
  (SC.useServer as jest.Mock).mockReturnValue({client});
  const successor={...makeFakeSession(),kind:"remote"};
  (Core.connectEngineSession as jest.Mock).mockResolvedValue(successor);
  const view=await render(remoteElement()); await view.rerender(remoteElement("B"));
  await act(async()=>{resolve(remoteResp());});
  expect(client.closeSession).toHaveBeenCalledTimes(1);
  expect(client.closeSession).toHaveBeenCalledWith(expect.objectContaining({session_id:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}));
  expect(Core.connectEngineSession).toHaveBeenCalledTimes(1); expect(successor.close).not.toHaveBeenCalled();
});
test("scheduled renewal retires old peer and sampler before fresh admission at original issuance plus 3300 seconds", async()=>{
  jest.useFakeTimers();
  try {
    const client=remoteClient(), first={...makeFakeSession(),kind:"remote"}, second={...makeFakeSession(),kind:"remote"};
    const oldSampler=makeFakeSampler(), nextSampler=makeFakeSampler();
    (Core.makeTelemetrySampler as jest.Mock).mockReturnValueOnce(oldSampler).mockReturnValueOnce(nextSampler);
    const selected=remoteResp({expires_at:Math.floor(Date.now()/1000)+3500}); // issued 100 seconds ago
    const fresh=remoteResp({session_id:"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",generation:2,expires_at:Math.floor(Date.now()/1000)+6800,ice_servers:[{urls:["turn:relay"],username:"fresh",credential:"fresh"}]});
    client.select.mockResolvedValueOnce(selected).mockResolvedValueOnce(fresh);
    let finishClose!: ()=>void;
    first.close.mockImplementation(()=>new Promise<void>(r=>{finishClose=r;}));
    (Core.connectEngineSession as jest.Mock).mockResolvedValueOnce(first).mockResolvedValueOnce(second);
    (SC.useServer as jest.Mock).mockReturnValue({client});
    const view=await render(remoteElement());
    await act(async()=>{jest.advanceTimersByTime(3199999);}); expect(client.select).toHaveBeenCalledTimes(1);
    await act(async()=>{jest.advanceTimersByTime(1);});
    expect(first.close).toHaveBeenCalledTimes(1); expect(oldSampler.stop).toHaveBeenCalledTimes(1);
    expect(client.select).toHaveBeenCalledTimes(1);
    await act(async()=>{finishClose();});
    expect(client.select).toHaveBeenCalledTimes(2);
    expect((Core.connectEngineSession as jest.Mock).mock.calls[1][0].selection).toEqual(fresh);
    expect(nextSampler.start).toHaveBeenCalledTimes(1); await view.unmount();
  } finally {jest.useRealTimers();}
});
test("remote quality replacement preserves a downgrade and fences retired adaptive callbacks", async()=>{
  const real=jest.requireActual("@wc/core");
  const controllers:any[]=[], callbacks:any[]=[];
  (Adaptive.makeAdaptive as jest.Mock).mockImplementation((opts:any)=>{callbacks.push(opts); const c=real.makeAdaptive(opts);controllers.push(c);return c;});
  const client=remoteClient(); client.select.mockResolvedValueOnce(remoteResp({tier:"1080"})).mockResolvedValue(remoteResp({tier:"360",generation:2}));
  const first={...makeFakeSession(),kind:"remote"},second={...makeFakeSession(),kind:"remote"};
  (Core.connectEngineSession as jest.Mock).mockResolvedValueOnce(first).mockResolvedValue(second);
  (SC.useServer as jest.Mock).mockReturnValue({client,preferences:{...Core.DEFAULT_STREAM_PREFERENCES,quality:"auto"}});
  const view=await render(remoteElement());
  await act(async()=>{controllers[0].pin("360");});
  expect(client.select).toHaveBeenCalledTimes(2); expect(first.close).toHaveBeenCalledTimes(1);
  expect(controllers[1].current()).toBe("360");
  await act(async()=>{callbacks[0].onApply("720");controllers[1].pin("360");});
  expect(client.setQuality).toHaveBeenCalledTimes(1); expect(client.select).toHaveBeenCalledTimes(2);
  await view.unmount();
});
test("late instance unauthorized from the old client cannot clear or navigate the new target", async()=>{
  const old=remoteClient(), next=remoteClient(), clearAuth=jest.fn(), navigate=jest.fn(); let reject!: (e:any)=>void;
  old.instances.mockImplementation(()=>new Promise((_,r)=>{reject=r;}));
  let client=old;
  (SC.useServer as jest.Mock).mockImplementation(()=>({client,clearAuth}));
  (Core.connectEngineSession as jest.Mock).mockResolvedValue({...makeFakeSession(),kind:"remote"});
  const element=()=> <Stream route={{params:{serial:"A"}}} navigation={{navigate}} RTCImpl={FakeRTCPeerConnection} VideoView={FakeVideoView}/>;
  const view=await render(element()); client=next;await view.rerender(element());
  await act(async()=>{reject({status:401});});
  expect(clearAuth).not.toHaveBeenCalled(); expect(navigate).not.toHaveBeenCalled();
});

test("rapid remote switches keep the oldest pending retirement ahead of every new select", async () => {
  const client = remoteClient();
  const first = { ...makeFakeSession(), kind: "remote" };
  const successor = { ...makeFakeSession(), kind: "remote" };
  let finishClose!: () => void;
  first.close.mockImplementation(() => new Promise<void>(r => { finishClose = r; }));
  (SC.useServer as jest.Mock).mockReturnValue({ client });
  (Core.connectEngineSession as jest.Mock).mockResolvedValueOnce(first).mockResolvedValue(successor);
  const view = await render(remoteElement());
  await view.rerender(remoteElement("B"));
  await view.rerender(remoteElement("C"));
  expect(first.close).toHaveBeenCalledTimes(1);
  expect(client.select).toHaveBeenCalledTimes(1);
  await act(async () => { finishClose(); });
  expect(client.select.mock.calls.map(call => call[0])).toEqual(["A", "C"]);
  expect(Core.connectEngineSession).toHaveBeenCalledTimes(2);
});

test("explicit remote recovery retries the auto starting tier after a within-session downgrade", async () => {
  const real = jest.requireActual("@wc/core"), controllers: any[] = [];
  (Adaptive.makeAdaptive as jest.Mock).mockImplementation((opts: any) => {
    const value = real.makeAdaptive(opts); controllers.push(value); return value;
  });
  const client = remoteClient();
  let tier = "1080";
  client.select.mockImplementation(async () => remoteResp({ tier }));
  client.setQuality.mockImplementation(async (_serial: string, value: string) => { tier = value; });
  (SC.useServer as jest.Mock).mockReturnValue({ client, preferences: { ...Core.DEFAULT_STREAM_PREFERENCES, quality: "auto" } });
  (Core.connectEngineSession as jest.Mock).mockImplementation(async () => ({ ...makeFakeSession(), kind: "remote" }));
  const view = await render(remoteElement());
  await act(async () => { controllers[0].pin("360"); });
  expect(controllers[1].current()).toBe("360");
  await act(async () => { (Core.connectEngineSession as jest.Mock).mock.calls[1][0].onState("disconnected"); });
  expect(client.setQuality.mock.calls.map(call => call[1])).toEqual(["360", "1080"]);
  expect(controllers[2].current()).toBe("1080");
  await view.unmount();
});

test("a canceled quality transition settles its mutation before a successor is selected", async () => {
  const real = jest.requireActual("@wc/core"), controllers: any[] = [];
  (Adaptive.makeAdaptive as jest.Mock).mockImplementation((opts: any) => {
    const value = real.makeAdaptive(opts); controllers.push(value); return value;
  });
  const client = remoteClient();
  let finishQuality!: () => void;
  client.setQuality.mockImplementation(() => new Promise<void>(r => { finishQuality = r; }));
  (SC.useServer as jest.Mock).mockReturnValue({ client, preferences: { ...Core.DEFAULT_STREAM_PREFERENCES, quality: "auto" } });
  (Core.connectEngineSession as jest.Mock).mockImplementation(async () => ({ ...makeFakeSession(), kind: "remote" }));
  const view = await render(remoteElement());
  await act(async () => { controllers[0].pin("360"); });
  await view.rerender(remoteElement("B"));
  expect(client.select).toHaveBeenCalledTimes(1);
  await act(async () => { finishQuality(); });
  expect(client.select.mock.calls.map(call => call[0])).toEqual(["A", "B"]);
  expect(Core.connectEngineSession).toHaveBeenCalledTimes(2);
  await view.unmount();
});

test("failed cleanup on a replaced client cannot block another installation", async () => {
  const old = remoteClient(), next = remoteClient();
  const first = { ...makeFakeSession(), kind: "remote" };
  first.close.mockRejectedValue(new Error("offline"));
  let client = old;
  (SC.useServer as jest.Mock).mockImplementation(() => ({ client }));
  (Core.connectEngineSession as jest.Mock).mockResolvedValueOnce(first).mockResolvedValue({ ...makeFakeSession(), kind: "remote" });
  const view = await render(remoteElement());
  client = next; await view.rerender(remoteElement());
  expect(next.select).toHaveBeenCalledTimes(1);
  expect(Core.connectEngineSession).toHaveBeenCalledTimes(2);
  await view.unmount();
});
