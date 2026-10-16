import { BenchTimeoutError, makeBenchClient } from "./benchClient";
import type { BenchChannel } from "./benchClient";

// Answers like engine/src/bench_messages.cpp.
function fakeEngine(entries: number[][], opts: { engineUs?: number; silent?: boolean } = {}) {
  const handlers = new Set<(m: any) => void>();
  const sent: any[] = [];
  const reply = (m: any) => queueMicrotask(() => handlers.forEach((h) => h(m)));
  const channel: BenchChannel = {
    subscribe(handler) { handlers.add(handler); return () => handlers.delete(handler); },
    send(message: any) {
      sent.push(message);
      if (opts.silent) return;
      if (message.type === "bench.ping") reply({ type: "bench.pong", id: message.id, engine_us: opts.engineUs ?? 7_000 });
      if (message.type === "bench.start") reply({ type: "bench.started", engine_us: 1 });
      if (message.type === "bench.fetch") {
        const from = message.from, count = Math.min(message.count, 2);
        reply({ type: "bench.chunk", from, total: entries.length, capacity: 65536, wrapped: false,
                entries: entries.slice(from, from + count) });
      }
    },
  };
  return { channel, sent };
}

test("ping pairs the reply by id and timestamps both ends", async () => {
  let now = 100;
  const { channel } = fakeEngine([]);
  const client = makeBenchClient(channel, { now: () => now++ });
  await expect(client.ping()).resolves.toEqual({ sendMs: 100, recvMs: 101, engineUs: 7_000 });
});

test("syncClock sends the requested number of pings", async () => {
  const { channel, sent } = fakeEngine([]);
  let now = 0;
  const client = makeBenchClient(channel, { now: () => (now += 1) });
  const estimate = await client.syncClock(5);
  expect(sent.filter((m) => m.type === "bench.ping")).toHaveLength(5);
  expect(estimate.samples).toBe(5);
});

test("fetchAll pages through the log and decodes compact entries", async () => {
  const { channel, sent } = fakeEngine([[0, 7, 1, 2, 0], [1, 900, 3, 4, 5], [1, 901, 6, 7, 8]]);
  const client = makeBenchClient(channel, { now: () => 0 });
  const result = await client.fetchAll();
  expect(sent.filter((m) => m.type === "bench.fetch").map((m) => m.from)).toEqual([0, 2]);
  expect(result.wrapped).toBe(false);
  expect(result.entries).toEqual([
    { kind: "input", key: 7, t1Us: 1, t2Us: 2, t3Us: 0 },
    { kind: "frame", key: 900, t1Us: 3, t2Us: 4, t3Us: 5 },
    { kind: "frame", key: 901, t1Us: 6, t2Us: 7, t3Us: 8 },
  ]);
});

test("a silent engine fails fast instead of hanging", async () => {
  jest.useFakeTimers();
  try {
    const { channel } = fakeEngine([], { silent: true });
    const client = makeBenchClient(channel, { now: () => 0, timeoutMs: 500 });
    const pending = client.ping();
    jest.advanceTimersByTime(500);
    await expect(pending).rejects.toBeInstanceOf(BenchTimeoutError);
    await expect(pending).rejects.toThrow("engine bench mode off or not responding");
  } finally {
    jest.useRealTimers();
  }
});
