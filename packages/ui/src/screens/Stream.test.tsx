import React from "react";
import { render, waitFor, act } from "@testing-library/react-native";
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
    signaling_url: "wss://relay.example.com/ws", signaling_token: null, public_session: "user1.A",
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
      signaling_url: "wss://relay.example.com/ws",
      public_session: "user1.A",
    }),
    authToken: "auth-tok-123",
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

test("adaptive stall leaves the connected peer in place", async () => {
  const session = makeFakeSession();
  let adaptiveOptions: any;
  (Adaptive.makeAdaptive as jest.Mock).mockImplementation((opts: any) => {
    adaptiveOptions = opts;
    return { start: jest.fn(), stop: jest.fn(), pin: jest.fn(), setAuto: jest.fn() } as any;
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

  expect(adaptiveOptions.onStall).toBeUndefined();
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
  let preferences = { quality: "720", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true } as const;
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
  await act(async () => { onState("disconnected"); });

  await waitFor(() => expect(replacementAdaptive.pin).toHaveBeenCalledWith("1080"));
});
