import { clockDriftUs, engineToLocalMs, estimateOffset } from "./clock";

test("uses the lowest-RTT sample and its midpoint", () => {
  const estimate = estimateOffset([
    { sendMs: 0, recvMs: 20, engineUs: 5_000_000 },   // rtt 20
    { sendMs: 100, recvMs: 102, engineUs: 5_101_000 }, // rtt 2, midpoint 101 ms
    { sendMs: 200, recvMs: 210, engineUs: 5_300_000 }, // rtt 10
  ]);
  expect(estimate).toEqual({ offsetUs: 5_101_000 - 101_000, rttMs: 2, samples: 3 });
});

test("converts engine time back to the receiver clock", () => {
  const { offsetUs } = estimateOffset([{ sendMs: 10, recvMs: 12, engineUs: 9_011_000 }]);
  expect(engineToLocalMs(9_011_000, offsetUs)).toBeCloseTo(11, 6);
  expect(engineToLocalMs(9_021_000, offsetUs)).toBeCloseTo(21, 6);
});

test("drift is the absolute offset change", () => {
  const a = { offsetUs: 1_000, rttMs: 1, samples: 1 };
  const b = { offsetUs: -1_500, rttMs: 1, samples: 1 };
  expect(clockDriftUs(a, b)).toBe(2_500);
});

test("refuses to estimate without samples", () => {
  expect(() => estimateOffset([])).toThrow("no clock samples");
});
