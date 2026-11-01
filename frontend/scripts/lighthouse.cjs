#!/usr/bin/env node
/**
 * Lighthouse (mobile) on the public site's sample pages and its free tools, against a server that is already up:
 *
 *   docker run -d --rm --name listyro-lh --network etsy-assistant_default -p 3000:3000 listyro/frontend:latest
 *   node scripts/lighthouse.cjs [http://localhost:3000]
 *
 * Fails when a page is under the targets. Only our own hosts: this is never pointed at anything else.
 * The tool is fetched at this pinned version when it runs, so it is not in the production image.
 */
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const VERSION = "12.8.2";
const BASE = process.argv[2] || "http://localhost:3000";
const OURS = new Set(["localhost", "127.0.0.1", "listyro.com"]);
if (!OURS.has(new URL(BASE).hostname)) throw new Error(`our own hosts only (${[...OURS].join(", ")}), not ${BASE}`);

const PAGES = ["/", "/pricing", "/features/ai-listing-writer", "/guides/etsy-title-rules-2026", "/free-tools", "/free-tools/title-checker", "/free-tools/tag-checker", "/free-tools/fee-calculator"];
// LH_PAGES=/a,/b checks those instead: an image built with a tool switched on has pages of its own to check.
if (process.env.LH_PAGES) PAGES.splice(0, PAGES.length, ...process.env.LH_PAGES.split(","));
const TARGETS = { performance: 95, seo: 100, accessibility: 95 };
const RUNS = 3;

const chrome = require("@playwright/test").chromium.executablePath();
const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "listyro-lh-"));

function once(page, run) {
  const out = path.join(outDir, `${page.replace(/\W+/g, "_") || "home"}-${run}.json`);
  // Chrome's temp files can refuse deletion on Windows after the report is written: the report decides.
  spawnSync("npx", [
    "--yes", `lighthouse@${VERSION}`, BASE + page, "--quiet", "--output=json", `--output-path=${out}`,
    "--only-categories=performance,seo,accessibility", `--chrome-path=${chrome}`,
    "--chrome-flags=--headless=new --no-sandbox",
  ], { stdio: "ignore", shell: process.platform === "win32" });
  if (!fs.existsSync(out)) throw new Error(`no report for ${page}`);
  return JSON.parse(fs.readFileSync(out, "utf8"));
}

const score = (report, name) => Math.round(report.categories[name].score * 100);
const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];

let failed = false;
console.log(`Lighthouse ${VERSION}, mobile, median of ${RUNS} runs, ${BASE}`);
console.log("page | performance | seo | accessibility | LCP | TBT | CLS");
for (const page of PAGES) {
  const reports = Array.from({ length: RUNS }, (_, run) => once(page, run));
  const scores = Object.fromEntries(Object.keys(TARGETS).map((name) => [name, median(reports.map((r) => score(r, name)))]));
  const audit = (id) => median(reports.map((r) => r.audits[id].numericValue));
  console.log(`${page} | ${scores.performance} | ${scores.seo} | ${scores.accessibility} | ${(audit("largest-contentful-paint") / 1000).toFixed(1)} s | ${Math.round(audit("total-blocking-time"))} ms | ${audit("cumulative-layout-shift").toFixed(3)}`);
  for (const [name, target] of Object.entries(TARGETS)) {
    if (scores[name] < target) {
      failed = true;
      const last = reports[reports.length - 1];
      const missed = Object.values(last.categories[name].auditRefs).map((ref) => last.audits[ref.id])
        .filter((a) => a.score !== null && a.score < 0.9 && a.scoreDisplayMode !== "informative").map((a) => `${a.id}${a.displayValue ? ` (${a.displayValue})` : ""}`);
      console.log(`  under target: ${name} ${scores[name]} < ${target}: ${missed.join(", ")}`);
    }
  }
}
fs.rmSync(outDir, { recursive: true, force: true });
process.exit(failed ? 1 : 0);
