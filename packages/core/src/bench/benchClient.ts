import { estimateOffset } from "./clock";
import type { ClockEstimate, PingSample } from "./clock";

export type EngineEntry = { kind: "input" | "frame"; key: number; t1Us: number; t2Us: number; t3Us: number };

export type BenchChannel = {
  send(message: { type: string; [key: string]: unknown }): void;
  subscribe(handler: (message: any) => void): () => void;
};

export class BenchTimeoutError extends Error {
  constructor() {
    super("engine bench mode off or not responding");
  }
}

const FETCH_COUNT = 500;

export function makeBenchClient(channel: BenchChannel, opts: { now: () => number; timeoutMs?: number }) {
  const timeoutMs = opts.timeoutMs ?? 2000;
  let nextId = 1;

  function request(message: { type: string; [key: string]: unknown }, matches: (reply: any) => boolean): Promise<any> {
    return new Promise((resolve, reject) => {
      const unsubscribe = channel.subscribe((reply) => {
        if (!matches(reply)) return;
        clearTimeout(timer);
        unsubscribe();
        resolve(reply);
      });
      const timer = setTimeout(() => {
        unsubscribe();
        reject(new BenchTimeoutError());
      }, timeoutMs);
      channel.send(message);
    });
  }

  async function ping(): Promise<PingSample> {
    const id = nextId++;
    const sendMs = opts.now();
    const reply = await request({ type: "bench.ping", id }, (m) => m?.type === "bench.pong" && m.id === id);
    return { sendMs, recvMs: opts.now(), engineUs: reply.engine_us };
  }

  async function syncClock(count = 50): Promise<ClockEstimate> {
    const samples: PingSample[] = [];
    for (let i = 0; i < count; i++) samples.push(await ping());
    return estimateOffset(samples);
  }

  async function start(): Promise<void> {
    await request({ type: "bench.start" }, (m) => m?.type === "bench.started");
  }

  async function fetchAll(): Promise<{ entries: EngineEntry[]; wrapped: boolean }> {
    const entries: EngineEntry[] = [];
    let wrapped = false;
    let from = 0;
    for (;;) {
      const chunk = await request({ type: "bench.fetch", from, count: FETCH_COUNT }, (m) => m?.type === "bench.chunk");
      wrapped = wrapped || chunk.wrapped === true;
      for (const [kind, key, t1Us, t2Us, t3Us] of chunk.entries as number[][]) {
        entries.push({ kind: kind === 0 ? "input" : "frame", key, t1Us, t2Us, t3Us });
      }
      from = chunk.from + chunk.entries.length;
      if (chunk.entries.length === 0 || from >= chunk.total) break;
    }
    return { entries, wrapped };
  }

  return { ping, syncClock, start, fetchAll };
}
