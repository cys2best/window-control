export const TIER_ORDER = ["480", "720", "1080", "1440"] as const;
export const DOWNGRADE_STREAK = 3;

export function stepTier(current: string, dir: -1 | 1): string {
  const i = TIER_ORDER.indexOf(current as (typeof TIER_ORDER)[number]);
  const base = i === -1 ? 1 : i; // default to "720" if unknown
  const j = Math.max(0, Math.min(TIER_ORDER.length - 1, base + dir));
  return TIER_ORDER[j];
}

export function shouldDowngrade(loss: number, rttMs: number): boolean {
  return loss > 0.08 || rttMs > 400;
}

export function nextBadStreak(prev: number, congested: boolean): number {
  return congested ? prev + 1 : 0;
}
