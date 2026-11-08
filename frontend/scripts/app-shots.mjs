#!/usr/bin/env node
/**
 * Every app screen at 1366 and 375 px, with data and empty, for the app audit (docs/app-audit.md):
 *
 *   docker compose exec -T api python -m app.cli seed-demo --admin --empty     (prints both passwords)
 *   FULL_PASSWORD=… EMPTY_PASSWORD=… node scripts/app-shots.mjs <folder under docs/screens> [base url]
 *   docker compose exec -T api python -m app.cli seed-demo --remove            (afterwards)
 *
 * Synthetic demo accounts only (demo-shots@ and demo-empty@example.test), our own local server only.
 * Each screen is saved as <account>-<screen>-<width>.webp; a page longer than 2,400 px is cut there.
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const folder = process.argv[2];
if (!folder) throw new Error("usage: app-shots.mjs <folder under docs/screens> [base url]");
const BASE = process.argv[3] ?? "http://localhost:3000";
if (!["localhost", "127.0.0.1"].includes(new URL(BASE).hostname)) throw new Error("our own local server only");
const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "screens", folder);
mkdirSync(OUT, { recursive: true });
const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;
const MAX_HEIGHT = 2400;

const ACCOUNTS = [
  { key: "full", email: "demo-shots@example.test", password: process.env.FULL_PASSWORD },
  { key: "empty", email: "demo-empty@example.test", password: process.env.EMPTY_PASSWORD },
];

/** The screens: a path, or a function finding it from the account's own data. */
const SCREENS = {
  full: [
    ["home", "/dashboard"],
    ["upload", "/upload"],
    ["batches", "/batches"],
    ["batch", async (page) => firstBatch(page, "")],
    ["review", async (page) => firstBatch(page, "/review")],
    ["schedule", "/scheduled"],
    ["season-planner", "/season-planner"],
    ["reports", "/analytics"],
    ["listing-health", "/listing-health"],
    ["profiles", "/profiles"],
    ["templates", "/templates"],
    ["template-designs", "/templates/designs"],
    ["shops", "/connect"],
    ["settings", "/settings"],
    ["admin", "/admin"],
  ],
  empty: [
    ["home", "/dashboard"],
    ["upload", "/upload"],
    ["batches", "/batches"],
    ["schedule", "/scheduled"],
    ["reports", "/analytics"],
    ["listing-health", "/listing-health"],
    ["profiles", "/profiles"],
    ["templates", "/templates"],
    ["shops", "/connect"],
    ["settings", "/settings"],
  ],
};

async function firstBatch(page, suffix) {
  await page.goto(`${BASE}/batches`);
  await settle(page);
  const href = await page.locator('a[href^="/batches/"]').first().getAttribute("href");
  return href.replace(/\/review$/, "") + suffix;
}

async function settle(page) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1200);
}

const browser = await chromium.launch();
for (const account of ACCOUNTS) {
  if (!account.password) {
    console.log(`skipping ${account.key}: no password given`);
    continue;
  }
  for (const width of [1366, 375]) {
    const context = await browser.newContext({
      viewport: { width, height: width > 500 ? 900 : 800 },
      deviceScaleFactor: 1,
      isMobile: width < 500,
      hasTouch: width < 500,
      timezoneId: "America/Chicago",
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    await page.goto(`${BASE}/login`);
    await settle(page); // hydrated: a click before that submits the form as a plain page load
    await page.locator("#email").fill(account.email);
    await page.locator("#password").fill(account.password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60_000 });
    for (const [name, where] of SCREENS[account.key]) {
      if (ONLY && !ONLY.has(name)) continue;
      const target = typeof where === "function" ? await where(page) : where;
      await page.goto(`${BASE}${target}`);
      await settle(page);
      // The development server's own overlay is not the app.
      await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
      const height = await page.evaluate(() => document.documentElement.scrollHeight);
      const shot = await page.screenshot({ fullPage: true, clip: { x: 0, y: 0, width, height: Math.min(height, MAX_HEIGHT) } });
      const file = path.join(OUT, `${account.key}-${name}-${width}.webp`);
      await sharp(shot).webp({ quality: 72 }).toFile(file);
      console.log(`${account.key}-${name}-${width}.webp (${Math.min(height, MAX_HEIGHT)} of ${height} px)`);
    }
    await context.close();
  }
}
await browser.close();
