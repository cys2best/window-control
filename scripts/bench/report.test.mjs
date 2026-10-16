import { test } from "node:test";
import assert from "node:assert/strict";
import { compareResults, formatComparison, formatSummary, resultBaseName } from "./report.mjs";

const stage = (p50, p95, ci) => ({ count: 100, mean: p50, p50, p95, p99: p95 + 5, p50CiHalf: ci });
const result = (p50) => ({
  meta: { serial: "emulator-5556", tier: "720", transport: "host", driftUs: 300, count: 100, warmup: 10, commit: "abc1234", label: "x" },
  stats: { tap_to_photon: stage(p50, p50 + 20, 2), guest: stage(40, 60, 1) },
  validity: { failed: false, reasons: [], crossClockReliable: true, breakdownAvailable: true },
  missRate: 0,
});

test("names result files by local minute and label", () => {
  assert.equal(resultBaseName(new Date(2026, 9, 16, 9, 5), "baseline"), "2026-10-16T0905-baseline");
});

test("summary lists stages in pipeline order with p50/p95/p99", () => {
  const md = formatSummary(result(90));
  assert.match(md, /\| guest \| 100 \| 40\.0 \| 60\.0 \| 65\.0 \|/);
  assert.ok(md.indexOf("guest") < md.indexOf("tap_to_photon"));
  assert.match(md, /transport: host/);
});

test("compare flags only changes larger than the noise", () => {
  const rows = compareResults(result(90), result(80));
  const headline = rows.find((r) => r.stage === "tap_to_photon");
  assert.equal(headline.deltaP50, -10);
  assert.equal(headline.significant, true);
  assert.equal(rows.find((r) => r.stage === "guest").significant, false);
  assert.match(formatComparison(rows), /tap_to_photon \| 90\.0 \| 80\.0 \| -10\.0 \| .* \| yes/);
});

test("compare skips stages missing from either run", () => {
  const a = result(90);
  delete a.stats.guest;
  assert.deepEqual(compareResults(a, result(80)).map((r) => r.stage), ["tap_to_photon"]);
});
