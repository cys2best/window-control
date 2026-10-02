import { createInputSender } from "./inputChannel";

function createFrames() {
  let nextId = 1;
  const queued = new Map<number, () => void>();
  return {
    schedule(callback: () => void) {
      const id = nextId++;
      queued.set(id, callback);
      return id;
    },
    cancel(id: number) {
      queued.delete(id);
    },
    flushOne(): boolean {
      const entry = queued.entries().next().value;
      if (!entry) return false;
      const [id, callback] = entry;
      queued.delete(id);
      callback();
      return true;
    },
    get size() {
      return queued.size;
    },
  };
}

function createChannel() {
  return {
    readyState: "open",
    bufferedAmount: 0,
    sent: [] as string[],
    listeners: {} as Record<string, Function[]>,
    send(payload: string) {
      this.sent.push(payload);
    },
    addEventListener(type: string, listener: Function) {
      (this.listeners[type] ||= []).push(listener);
    },
    json() {
      return this.sent.map((payload) => JSON.parse(payload));
    },
  };
}

test("coalesces drag moves and flushes the last coordinate before end", () => {
  const channel = createChannel();
  const frames = createFrames();
  const sender = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel, highWaterMark: 16 });

  sender.dragStart(0.1, 0.2);
  sender.dragMove(0.3, 0.4);
  sender.dragMove(0.8, 0.9);
  frames.flushOne();
  sender.dragEnd(0.9, 1.0);

  expect(channel.json()).toEqual([
    { type: "drag_start", x: 0.1, y: 0.2 },
    { type: "drag_move", x: 0.8, y: 0.9 },
    { type: "drag_move", x: 0.9, y: 1.0 },
    { type: "drag_end", x: 0.9, y: 1.0 },
  ]);
});

test("accumulates scroll while buffered and preserves current direction", () => {
  const channel = createChannel();
  const frames = createFrames();
  const sender = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel, highWaterMark: 16 });
  channel.bufferedAmount = 32;
  sender.scroll(0.5, 0.5, -0.04);
  sender.scroll(0.5, 0.5, -0.06);
  frames.flushOne();
  expect(channel.sent.length).toBe(0);
  channel.bufferedAmount = 0;
  frames.flushOne();
  expect(channel.json()[0]).toEqual({ type: "scroll", x: 0.5, y: 0.5, dy: -0.1 });
});

test("high-buffer flush retries via bufferedamountlow", () => {
  const channel = createChannel();
  const frames = createFrames();
  const sender = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel, highWaterMark: 16 });
  channel.bufferedAmount = 100;
  sender.dragMove(0.4, 0.5);
  frames.flushOne();
  expect(channel.sent.length).toBe(0);
  channel.bufferedAmount = 0;
  channel.listeners["bufferedamountlow"].forEach((fn) => fn());
  frames.flushOne();
  expect(channel.json()).toEqual([{ type: "drag_move", x: 0.4, y: 0.5 }]);
});

test("sends discrete key, IDR, and echo messages immediately only when open", () => {
  const channel = createChannel();
  const sender = createInputSender(channel);
  sender.send({ type: "key", key: "Return" });
  sender.send({ type: "idr" });
  sender.send({ type: "echo", ts: 12 });
  channel.readyState = "closed";
  sender.send({ type: "key", key: "Escape" });
  expect(channel.json()).toEqual([
    { type: "key", key: "Return" },
    { type: "idr" },
    { type: "echo", ts: 12 },
  ]);
});

test("sends a tap as immediate drag start and end without a click message", () => {
  const channel = createChannel();
  const sender = createInputSender(channel);
  sender.dragStart(0.2, 0.3);
  sender.dragEnd(0.2, 0.3);
  expect(channel.json()).toEqual([
    { type: "drag_start", x: 0.2, y: 0.3 },
    { type: "drag_end", x: 0.2, y: 0.3 },
  ]);
});

test("clamps accumulated normalized scroll to one bounded delta", () => {
  const channel = createChannel();
  const frames = createFrames();
  const sender = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.scroll(0.1, 0.2, 0.2);
  sender.scroll(0.1, 0.2, 0.2);
  frames.flushOne();
  expect(channel.json()).toEqual([{ type: "scroll", x: 0.1, y: 0.2, dy: 0.25 }]);
});

test("keeps pending scroll scheduled when drag end cancels a move frame", () => {
  const channel = createChannel();
  const frames = createFrames();
  const sender = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.dragStart(0.1, 0.2);
  sender.dragMove(0.3, 0.4);
  sender.scroll(0.5, 0.6, -0.1);
  sender.dragEnd(0.3, 0.4);
  frames.flushOne();
  expect(channel.json()).toEqual([
    { type: "drag_start", x: 0.1, y: 0.2 },
    { type: "drag_move", x: 0.3, y: 0.4 },
    { type: "drag_end", x: 0.3, y: 0.4 },
    { type: "scroll", x: 0.5, y: 0.6, dy: -0.1 },
  ]);
});

test("close cancels scheduled motion and prevents post-close frames", () => {
  const channel = createChannel();
  const frames = createFrames();
  const sender = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.dragMove(0.4, 0.5);
  sender.close();
  expect(frames.size).toBe(0);
  expect(frames.flushOne()).toBe(false);
  expect(channel.json()).toEqual([]);
});

test("drops queued motion when the channel closes", () => {
  const channel = createChannel();
  const frames = createFrames();
  const sender = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  sender.dragMove(0.4, 0.5);
  channel.readyState = "closed";
  frames.flushOne();
  expect(frames.size).toBe(0);
  expect(channel.json()).toEqual([]);
});

test("a fresh sender does not inherit pending motion from a closed sender (generation isolation)", () => {
  const channel = createChannel();
  const frames = createFrames();
  const first = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  first.dragMove(0.4, 0.5);
  first.close();
  const second = createInputSender(channel, { schedule: frames.schedule, cancel: frames.cancel });
  second.scroll(0.6, 0.7, 0.1);
  frames.flushOne();
  expect(channel.json()).toEqual([{ type: "scroll", x: 0.6, y: 0.7, dy: 0.1 }]);
});

test("uses a default 16ms setTimeout scheduler when none is injected", () => {
  jest.useFakeTimers();
  try {
    const channel = createChannel();
    const sender = createInputSender(channel);
    sender.dragMove(0.4, 0.5);
    expect(channel.sent.length).toBe(0);
    jest.advanceTimersByTime(16);
    expect(channel.json()).toEqual([{ type: "drag_move", x: 0.4, y: 0.5 }]);
    sender.close();
  } finally {
    jest.useRealTimers();
  }
});
