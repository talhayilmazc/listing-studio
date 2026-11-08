#!/usr/bin/env node
/**
 * Lighthouse on the main app pages, signed in (app/onboarding-v1): accessibility and best practices,
 * aiming at 100 each. Against a production build that is already up, as the synthetic demo account only:
 *
 *   docker compose exec -T api python -m app.cli seed-demo --admin      (prints the password)
 *   docker run -d --rm --name listyro-lh --network etsy-assistant_default -p 3000:3000 listyro/frontend:latest
 *   LAYOUT_EMAIL=demo-shots@example.test LAYOUT_PASSWORD=… node scripts/lighthouse-app.cjs [http://localhost:3000]
 *
 * Signs in through our own API and hands Lighthouse the session cookie. Median of RUNS runs per page,
 * desktop preset (the app's main use; the phone layout has its own browser tests). Our own hosts only.
 */
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const VERSION = "12.8.2";
const BASE = process.argv[2] || "http://localhost:3000";
if (!["localhost", "127.0.0.1"].includes(new URL(BASE).hostname)) throw new Error("our own local server only");
if (process.env.LAYOUT_EMAIL !== "demo-shots@example.test") throw new Error("the synthetic demo account only");
const RUNS = 3;
const TARGETS = { accessibility: 100, "best-practices": 100 };
const chrome = require("@playwright/test").chromium.executablePath();
const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "listyro-lh-app-"));

async function session() {
  const res = await fetch(`${BASE}/api/account/login`, {
    method: "POST",
    headers: { "content-type": "application/json", origin: BASE },
    body: JSON.stringify({ email: process.env.LAYOUT_EMAIL, password: process.env.LAYOUT_PASSWORD }),
  });
  const cookie = (res.headers.get("set-cookie") ?? "").match(/session=[^;]+/);
  if (!res.ok || !cookie) throw new Error(`sign-in failed (${res.status})`);
  return cookie[0];
}

async function pages(cookie) {
  const batches = await (await fetch(`${BASE}/api/batches`, { headers: { cookie } })).json();
  const review = batches.find((b) => (b.statuses ?? []).includes("in_review")) ?? batches[0];
  return ["/dashboard", "/batches", "/review", `/batches/${review.id}/review`, "/scheduled", "/connect", "/settings", "/upload"];
}

function once(page, run, cookie) {
  const out = path.join(outDir, `${page.replace(/\W+/g, "_")}-${run}.json`);
  spawnSync("npx", [
    "--yes", `lighthouse@${VERSION}`, BASE + page, "--quiet", "--output=json", `--output-path=${out}`, "--preset=desktop",
    "--only-categories=accessibility,best-practices", `--chrome-path=${chrome}`, "--chrome-flags=--headless=new --no-sandbox",
    `--extra-headers=${cookie}`,
  ], { stdio: "ignore", shell: process.platform === "win32" });
  if (!fs.existsSync(out)) throw new Error(`no report for ${page}`);
  return JSON.parse(fs.readFileSync(out, "utf8"));
}

const score = (r, name) => Math.round(r.categories[name].score * 100);
const median = (v) => [...v].sort((a, b) => a - b)[Math.floor(v.length / 2)];

(async () => {
  const cookie = await session();
  // Lighthouse reads the extra header from a JSON file: no quoting through a shell.
  const headers = path.join(outDir, "headers.json");
  fs.writeFileSync(headers, JSON.stringify({ Cookie: cookie }));
  const quoted = headers;
  let failed = false;
  console.log(`Lighthouse ${VERSION}, desktop, median of ${RUNS} runs, signed in, ${BASE}`);
  console.log("page | accessibility | best practices | failing audits");
  for (const page of await pages(cookie)) {
    const reports = Array.from({ length: RUNS }, (_, run) => once(page, run, quoted));
    const scores = Object.fromEntries(Object.keys(TARGETS).map((n) => [n, median(reports.map((r) => score(r, n)))]));
    const last = reports[reports.length - 1];
    const bad = Object.values(last.audits).filter((a) => a.score !== null && a.score < 1 && a.scoreDisplayMode === "binary").map((a) => a.id);
    const shown = page.replace(/\/batches\/[^/]+\/review/, "/batches/<id>/review");
    console.log(`${shown} | ${scores.accessibility} | ${scores["best-practices"]} | ${bad.join(", ") || "-"}`);
    if (last.finalDisplayedUrl && new URL(last.finalDisplayedUrl).pathname.startsWith("/login")) throw new Error("Lighthouse was not signed in");
    for (const [n, t] of Object.entries(TARGETS)) if (scores[n] < t) failed = true;
  }
  if (failed) {
    console.log(`Below target (${JSON.stringify(TARGETS)}).`);
    process.exitCode = 1;
  }
})();
