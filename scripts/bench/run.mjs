#!/usr/bin/env node
// Lab latency bench runner. See scripts/bench/README.md.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";
import { benchPageError, compareResults, formatComparison, formatSummary, resultBaseName } from "./report.mjs";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    host: { type: "string" },
    serial: { type: "string" },
    n: { type: "string", default: "200" },
    warmup: { type: "string", default: "10" },
    label: { type: "string", default: "run" },
    setup: { type: "boolean", default: false },
    compare: { type: "boolean", default: false },
    profile: { type: "string", default: path.join(os.homedir(), ".emuctrl-bench-profile") },
    out: { type: "string", default: "bench-results" },
    // Branded browser to drive: "chrome" or "msedge". Playwright's bundled
    // Chromium has no H.264 decoder, so it cannot play the stream.
    channel: { type: "string", default: "chrome" },
  },
});

function fail(message) {
  console.error(`bench: ${message}`);
  process.exit(1);
}

async function launch() {
  return chromium.launchPersistentContext(values.profile, {
    channel: values.channel,
    headless: false, // a hidden tab throttles requestVideoFrameCallback
    viewport: { width: 1280, height: 800 },
  });
}

async function setup() {
  if (!values.host) fail("--host is required");
  const context = await launch();
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(`${values.host}/pair`);
  console.log("Pair this browser in the opened window, then close it.");
  await context.waitForEvent("close", { timeout: 0 });
}

async function run() {
  if (!values.host || !values.serial) fail("--host and --serial are required");
  const count = Number(values.n);
  const warmup = Number(values.warmup);
  const context = await launch();
  try {
    const page = context.pages()[0] ?? (await context.newPage());
    const url = `${values.host}/bench?serial=${encodeURIComponent(values.serial)}&n=${count}&warmup=${warmup}`;
    const response = await page.goto(url);
    const pageError = benchPageError(response?.status() ?? null);
    if (pageError) {
      await context.close();
      fail(pageError);
    }
    // About 0.5 s per tap plus a 0.7 s worst-case gap, plus setup and fetch.
    const timeout = ((count + warmup) * 2.7 + 120) * 1000;
    await page.waitForFunction(() => window.__benchResult || window.__benchError, null, { timeout, polling: 500 });
    const error = await page.evaluate(() => window.__benchError);
    if (error) fail(error);
    const result = await page.evaluate(() => window.__benchResult);

    result.meta.label = values.label;
    result.meta.commit = execFileSync("git", ["rev-parse", "--short", "HEAD"]).toString().trim();
    mkdirSync(values.out, { recursive: true });
    const base = path.join(values.out, resultBaseName(new Date(), values.label));
    const summary = formatSummary(result);
    writeFileSync(`${base}.json`, JSON.stringify(result, null, 2));
    writeFileSync(`${base}.md`, summary);
    console.log(summary);
    console.log(`wrote ${base}.json and ${base}.md`);
    process.exitCode = result.validity.failed ? 2 : 0;
  } finally {
    await context.close();
  }
}

function compare() {
  if (positionals.length !== 2) fail("--compare needs two result files");
  const [a, b] = positionals.map((file) => JSON.parse(readFileSync(file, "utf8")));
  console.log(`A: ${positionals[0]}\nB: ${positionals[1]}\n`);
  console.log(formatComparison(compareResults(a, b)));
}

if (values.compare) compare();
else if (values.setup) await setup();
else await run();
