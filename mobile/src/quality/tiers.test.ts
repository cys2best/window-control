import { TIER_ORDER, stepTier, shouldDowngrade, nextBadStreak, DOWNGRADE_STREAK } from "./tiers";

test("stepTier clamps at both ends", () => {
  expect(stepTier("480", -1)).toBe("480");
  expect(stepTier("1440", 1)).toBe("1440");
  expect(stepTier("720", -1)).toBe("480");
  expect(stepTier("720", 1)).toBe("1080");
});

test("shouldDowngrade triggers on loss or rtt", () => {
  expect(shouldDowngrade(0.09, 100)).toBe(true);
  expect(shouldDowngrade(0.0, 500)).toBe(true);
  expect(shouldDowngrade(0.02, 100)).toBe(false);
});

test("bad streak accumulates then resets", () => {
  let s = 0;
  s = nextBadStreak(s, true); s = nextBadStreak(s, true); s = nextBadStreak(s, true);
  expect(s).toBe(DOWNGRADE_STREAK);
  expect(nextBadStreak(s, false)).toBe(0);
});
