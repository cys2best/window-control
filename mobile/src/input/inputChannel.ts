export type InputMessage = { type: string; [key: string]: unknown };

export type InputSender = {
  dragStart(x: number, y: number): void;
  dragMove(x: number, y: number): void;
  dragEnd(x: number, y: number): void;
  scroll(x: number, y: number, dy: number): void;
  send(message: InputMessage): void;
  close(): void;
};

const MAX_SCROLL_DELTA = 0.25;
const DEFAULT_HIGH_WATER_MARK = 16 * 1024;
const DEFAULT_FRAME_MS = 16;

type Scheduler = (callback: () => void) => any;
type Canceler = (handle: any) => void;

type InputChannelOpts = {
  schedule?: Scheduler;
  cancel?: Canceler;
  highWaterMark?: number;
};

type Pending = { x: number; y: number };
type PendingScroll = { x: number; y: number; dy: number };

export function createInputSender(channel: any, opts: InputChannelOpts = {}): InputSender {
  const schedule: Scheduler =
    opts.schedule || ((cb) => setTimeout(cb, DEFAULT_FRAME_MS));
  const cancel: Canceler = opts.cancel || ((handle) => clearTimeout(handle));
  const highWaterMark = opts.highWaterMark === undefined ? DEFAULT_HIGH_WATER_MARK : opts.highWaterMark;

  let pendingMove: Pending | null = null;
  let pendingScroll: PendingScroll | null = null;
  let frame: any = null;
  let dragMoved = false;
  let closed = false;
  let generation = 0;

  function isOpen(): boolean {
    return !closed && !!channel && channel.readyState === "open";
  }

  function send(message: InputMessage): void {
    if (!isOpen()) return;
    channel.send(JSON.stringify(message));
  }

  function hasMotion(): boolean {
    return pendingMove !== null || pendingScroll !== null;
  }

  function scheduleFlush(): void {
    if (closed || frame !== null || !hasMotion()) return;
    const scheduledGeneration = generation;
    frame = schedule(() => {
      frame = null;
      if (closed || scheduledGeneration !== generation) return;
      flush();
    });
  }

  function flush(): void {
    if (!hasMotion()) return;
    if (!isOpen()) {
      pendingMove = null;
      pendingScroll = null;
      return;
    }
    if (channel.bufferedAmount > highWaterMark) {
      scheduleFlush();
      return;
    }
    if (pendingMove !== null) {
      send({ type: "drag_move", x: pendingMove.x, y: pendingMove.y });
      pendingMove = null;
    }
    if (pendingScroll !== null) {
      send({ type: "scroll", x: pendingScroll.x, y: pendingScroll.y, dy: pendingScroll.dy });
      pendingScroll = null;
    }
  }

  function wakeMotion(): void {
    if (!closed && hasMotion() && channel.bufferedAmount <= highWaterMark) scheduleFlush();
  }

  if (channel && typeof channel.addEventListener === "function") {
    channel.addEventListener("bufferedamountlow", wakeMotion);
  }

  return {
    send,
    dragStart(x: number, y: number) {
      if (closed) return;
      dragMoved = false;
      send({ type: "drag_start", x, y });
    },
    dragMove(x: number, y: number) {
      if (closed) return;
      dragMoved = true;
      pendingMove = { x, y };
      scheduleFlush();
    },
    dragEnd(x: number, y: number) {
      if (closed) return;
      if (frame !== null) {
        cancel(frame);
        frame = null;
      }
      if (dragMoved) {
        pendingMove = null;
        send({ type: "drag_move", x, y });
      }
      send({ type: "drag_end", x, y });
      dragMoved = false;
      pendingMove = null;
      scheduleFlush();
    },
    scroll(x: number, y: number, dy: number) {
      if (closed) return;
      const accumulated = (pendingScroll ? pendingScroll.dy : 0) + dy;
      pendingScroll = {
        x,
        y,
        dy: Math.max(-MAX_SCROLL_DELTA, Math.min(MAX_SCROLL_DELTA, accumulated)),
      };
      scheduleFlush();
    },
    close() {
      if (closed) return;
      closed = true;
      generation += 1;
      if (frame !== null) cancel(frame);
      frame = null;
      pendingMove = null;
      pendingScroll = null;
      dragMoved = false;
    },
  };
}
