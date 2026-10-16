import { test } from "node:test";
import assert from "node:assert/strict";
import { benchPageError, compareResults, formatComparison, formatSummary, resultBaseName } from "./report.mjs";

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

test("a missing bench page is reported as bench mode off", () => {
  assert.match(benchPageError(404), /bench mode off.*EMUCTRL_BENCH=1/);
  assert.match(benchPageError(null), /no response/);
  assert.equal(benchPageError(200), null);
});

test("summary splits the guest stage with the target page's own timings", () => {
  const r = result(90);
  r.targetTimings = [10, 20, 30].map((v) => ({ input_ms: v / 10, frame_ms: v, next_frame_ms: v + 16 }));
  const md = formatSummary(r);
  assert.match(md, /\| touch → page handler \| 3 \| 2\.0 \|/);
  assert.match(md, /\| touch → first frame drawn \| 3 \| 20\.0 \|/);
  assert.match(md, /\| touch → frame on screen \| 3 \| 36\.0 \|/);
  // guest p50 40 − on-screen p50 36 = 4 ms left for capture, encode and send.
  assert.match(md, /capture \+ encode \+ send ≈ 4\.0 ms/);
});

test("summary omits the guest split when the target page sent nothing", () => {
  assert.doesNotMatch(formatSummary(result(90)), /touch → page handler/);
});
