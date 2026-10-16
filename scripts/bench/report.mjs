// Formatting and comparison for latency bench results (see run.mjs).
export const STAGE_ORDER = [
  "input_network", "engine_input", "guest", "engine_video", "network_receive", "receiver", "tap_to_photon",
];

const ms = (value) => (typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "–");
const pad = (n) => String(n).padStart(2, "0");

export function resultBaseName(date, label) {
  const stamp = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}${pad(date.getMinutes())}`;
  return `${stamp}-${label}`;
}

// Same rank interpolation as packages/core/src/bench/analyze.ts.
function percentile(sorted, p) {
  if (sorted.length === 0) return NaN;
  const rank = (sorted.length - 1) * p;
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - lo);
}

const GUEST_SPLIT = [
  ["input_ms", "touch → page handler"],
  ["frame_ms", "touch → first frame drawn"],
  ["next_frame_ms", "touch → frame on screen"],
];

// The target page's own timings divide the guest stage into Android + page
// work and what is left for scrcpy's capture, encode and send.
function formatGuestSplit(result) {
  const timings = result.targetTimings ?? [];
  if (timings.length === 0) return [];
  const lines = [
    "",
    "Inside the guest (target page's own timings):",
    "",
    "| step | n | p50 ms | p95 ms |",
    "|---|---|---|---|",
  ];
  const p50s = {};
  for (const [key, label] of GUEST_SPLIT) {
    const sorted = timings.map((t) => t[key]).sort((a, b) => a - b);
    p50s[key] = percentile(sorted, 0.5);
    lines.push(`| ${label} | ${sorted.length} | ${ms(p50s[key])} | ${ms(percentile(sorted, 0.95))} |`);
  }
  const guest = result.stats.guest;
  if (guest) {
    lines.push("", `capture + encode + send ≈ ${ms(guest.p50 - p50s.next_frame_ms)} ms (guest p50 − frame on screen p50)`);
  }
  return lines;
}

export function formatSummary(result) {
  const { meta, stats, validity, missRate } = result;
  const lines = [
    `# Latency bench — ${meta.label}`,
    "",
    `commit: ${meta.commit} · serial: ${meta.serial} · tier: ${meta.tier} · transport: ${meta.transport} · ` +
      `taps: ${meta.count} (+${meta.warmup} warm-up) · misses: ${(missRate * 100).toFixed(1)}% · ` +
      `clock drift: ${(meta.driftUs / 1000).toFixed(2)} ms`,
    "",
    `status: ${validity.failed ? "FAILED" : "ok"}${validity.reasons.length ? ` — ${validity.reasons.join("; ")}` : ""}`,
    "",
    "| stage | n | p50 ms | p95 ms | p99 ms | mean ms | p50 ± |",
    "|---|---|---|---|---|---|---|",
  ];
  for (const stage of STAGE_ORDER) {
    const s = stats[stage];
    if (!s) continue;
    lines.push(`| ${stage} | ${s.count} | ${ms(s.p50)} | ${ms(s.p95)} | ${ms(s.p99)} | ${ms(s.mean)} | ${ms(s.p50CiHalf)} |`);
  }
  lines.push(...formatGuestSplit(result));
  return lines.join("\n") + "\n";
}

export function compareResults(a, b) {
  const rows = [];
  for (const stage of STAGE_ORDER) {
    const sa = a.stats[stage];
    const sb = b.stats[stage];
    if (!sa || !sb) continue;
    const deltaP50 = sb.p50 - sa.p50;
    rows.push({
      stage,
      p50A: sa.p50, p50B: sb.p50, deltaP50,
      p95A: sa.p95, p95B: sb.p95, deltaP95: sb.p95 - sa.p95,
      significant: Math.abs(deltaP50) > Math.max(sa.p50CiHalf, sb.p50CiHalf),
    });
  }
  return rows;
}

export function formatComparison(rows) {
  const lines = [
    "| stage | p50 A | p50 B | Δp50 | Δp95 | beyond noise |",
    "|---|---|---|---|---|---|",
  ];
  for (const r of rows) {
    lines.push(`| ${r.stage} | ${ms(r.p50A)} | ${ms(r.p50B)} | ${ms(r.deltaP50)} | ${ms(r.deltaP95)} | ${r.significant ? "yes" : "no"} |`);
  }
  return lines.join("\n") + "\n";
}

// The host registers /bench only in bench mode; anything but 200 means the
// page will never post a result, so fail now instead of waiting out the run.
export function benchPageError(status) {
  if (status === null || status === undefined) return "no response from host /bench";
  if (status === 404) return "host bench mode off (/bench returned 404); start the host with EMUCTRL_BENCH=1";
  if (status !== 200) return `host /bench returned HTTP ${status}`;
  return null;
}
