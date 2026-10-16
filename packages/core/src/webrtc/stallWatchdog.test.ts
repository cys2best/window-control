import { makeStallWatchdog } from "./stallWatchdog";

function video(framesDecoded: number, packetsReceived: number) {
  return new Map([["v", { type: "inbound-rtp", kind: "video", framesDecoded, packetsReceived }]]);
}

function watchdogFor(reports: Map<string, any>[], overrides: Record<string, unknown> = {}) {
  let now = 0;
  const onStall = jest.fn();
  const watchdog = makeStallWatchdog({
    pc: { getStats: jest.fn().mockImplementation(async () => reports.shift()) },
    onStall,
    now: () => now,
    ...overrides,
  });
  const tick = async (advanceMs = 1_000) => { now += advanceMs; await watchdog.sample(); };
  return { watchdog, onStall, tick };
}

test("asks for a keyframe when packets keep arriving but no frame decodes", async () => {
  const { onStall, tick } = watchdogFor([video(100, 1_000), video(100, 1_080)]);

  await tick();
  expect(onStall).not.toHaveBeenCalled();
  await tick();

  expect(onStall).toHaveBeenCalledTimes(1);
});

test("stays quiet while frames keep decoding", async () => {
  const { onStall, tick } = watchdogFor([video(100, 1_000), video(124, 1_080), video(150, 1_160)]);

  await tick(); await tick(); await tick();

  expect(onStall).not.toHaveBeenCalled();
});

test("a still screen sends no packets and is not a stall", async () => {
  // scrcpy emits nothing while the device screen does not change.
  const { onStall, tick } = watchdogFor([video(100, 1_000), video(100, 1_000), video(100, 1_000)]);

  await tick(); await tick(); await tick();

  expect(onStall).not.toHaveBeenCalled();
});

test("repeats the request no faster than the minimum gap while the stall lasts", async () => {
  const { onStall, tick } = watchdogFor([
    video(100, 1_000), video(100, 1_050), video(100, 1_100), video(100, 1_150), video(100, 1_200),
  ]);

  await tick();        // baseline
  await tick();        // t=2s: stalled -> request
  await tick();        // t=3s: 1s since the request -> wait
  expect(onStall).toHaveBeenCalledTimes(1);
  await tick();        // t=4s: 2s since the request -> again
  await tick();        // t=5s
  expect(onStall).toHaveBeenCalledTimes(2);
});

test.each([
  [new Map()],
  [new Map([["a", { type: "inbound-rtp", kind: "audio", framesDecoded: 1, packetsReceived: 9 }]])],
  [new Map([["v", { type: "inbound-rtp", kind: "video" }]])],
])("reports with no usable video counters are ignored", async (report) => {
  const { onStall, tick } = watchdogFor([video(100, 1_000), report as any, video(100, 1_050)]);

  await tick(); await tick(); await tick();

  // The unusable report resets the baseline, so the third one has nothing
  // to compare against yet.
  expect(onStall).not.toHaveBeenCalled();
});

test("a failing getStats is swallowed and sampling carries on", async () => {
  const onStall = jest.fn();
  const getStats = jest.fn()
    .mockResolvedValueOnce(video(100, 1_000))
    .mockRejectedValueOnce(new Error("peer closed"))
    .mockResolvedValueOnce(video(100, 1_050))
    .mockResolvedValueOnce(video(100, 1_100));
  let now = 0;
  const watchdog = makeStallWatchdog({ pc: { getStats }, onStall, now: () => now });
  for (let i = 0; i < 4; i += 1) { now += 1_000; await watchdog.sample(); }

  expect(onStall).toHaveBeenCalledTimes(1);
});

test("start samples on an interval and stop ends it", async () => {
  jest.useFakeTimers();
  try {
    const getStats = jest.fn().mockResolvedValue(video(100, 1_000));
    const watchdog = makeStallWatchdog({ pc: { getStats }, onStall: jest.fn() });
    watchdog.start();
    jest.advanceTimersByTime(3_000);
    expect(getStats).toHaveBeenCalledTimes(3);
    watchdog.stop();
    jest.advanceTimersByTime(3_000);
    expect(getStats).toHaveBeenCalledTimes(3);
  } finally {
    jest.useRealTimers();
  }
});
