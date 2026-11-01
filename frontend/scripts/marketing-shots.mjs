#!/usr/bin/env node
/**
 * The public site's product screenshots, from the synthetic demo account only (docs/marketing-screens.md):
 *
 *   docker compose exec -T api python -m app.cli seed-demo --admin --shops 15     (prints the password)
 *   LAYOUT_EMAIL=demo-shots@example.test LAYOUT_PASSWORD=… node scripts/marketing-shots.mjs
 *   docker compose exec -T api python -m app.cli seed-demo --remove               (afterwards)
 *
 * Each crop is 4:3, saved to public/screens at 1120 and 560 wide. Our own server only.
 */
import { chromium } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const BASE = process.env.LAYOUT_BASE_URL ?? "http://localhost:3000";
if (!["localhost", "127.0.0.1"].includes(new URL(BASE).hostname)) throw new Error("our own local server only");
if (process.env.LAYOUT_EMAIL !== "demo-shots@example.test") throw new Error("the synthetic demo account only");

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "public", "screens");
const WIDTH = 880;
const HEIGHT = 660;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1136, height: 1000 }, deviceScaleFactor: 2, timezoneId: "America/Chicago" });
const page = await context.newPage();

await page.goto(`${BASE}/login`);
await page.getByLabel(/email/i).fill(process.env.LAYOUT_EMAIL);
await page.getByLabel(/password/i).fill(process.env.LAYOUT_PASSWORD);
await page.getByRole("button", { name: /sign in/i }).click();
await page.waitForURL(/dashboard|accept-terms/);

async function settle() {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(700);
}

async function batchHref(name) {
  await page.goto(`${BASE}/batches`);
  await settle();
  return page.getByRole("link", { name }).first().getAttribute("href");
}

/** The 4:3 crop that starts at this element, within the page's main column. */
async function crop(name, anchor, { dy = -14, phone = false } = {}) {
  await anchor.first().scrollIntoViewIfNeeded();
  await anchor.first().evaluate((el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 90));
  // The app's footer prints the support address of the machine it runs on: never in a picture.
  await page.addStyleTag({ content: "footer { visibility: hidden !important; }" });
  await page.mouse.move(0, 0);
  await page.waitForTimeout(500);
  const box = await anchor.first().boundingBox();
  const main = await page.locator("main").first().boundingBox();
  const width = phone ? 560 : WIDTH;
  const shot = await page.screenshot({ clip: { x: phone ? 0 : main.x, y: Math.max(box.y + dy, 0), width, height: (width * 3) / 4 } });
  for (const [suffix, width, quality] of [["", 1120, 78], ["-560", 560, 72]]) {
    await sharp(shot).resize(width).webp({ quality }).toFile(path.join(OUT, `site-${name}${suffix}.webp`));
  }
  console.log(`site-${name}`);
}

const summer = await batchHref(/Summer drop/);
await page.goto(`${BASE}${summer}`);
await settle();
await crop("upload", page.getByText(/Found \d+ groups?/));

await page.goto(`${BASE}${summer}/review`);
await settle();
await crop("write", page.getByText("Title", { exact: false }).locator("visible=true"), { dy: -60 });
// The bottom of a card reads best in the one-column layout.
await page.setViewportSize({ width: 560, height: 1000 });
await page.waitForTimeout(500);
await crop("review", page.getByText("Search-readiness checklist"), { dy: -16, phone: true });
await page.setViewportSize({ width: 1136, height: 1000 });

await page.goto(`${BASE}/scheduled`);
await settle();
await crop("schedule", page.getByText("Every listing waiting to go live"), { dy: -16 });

await page.goto(`${BASE}/templates`);
await settle();
const ten = page.getByRole("button", { name: /Ten colours/ }).first();
if (await ten.count()) await ten.click();
await settle();
await crop("mockups", page.getByText("Select all").first(), { dy: -24 });

const brand = await batchHref(/Published in Northwind/);
await page.goto(`${BASE}${brand}`);
await settle();
await page.getByRole("combobox").first().selectOption({ label: "Juniper Print Co." }).catch(() => {});
await settle();
await crop("shops", page.getByText("Working on:"), { dy: -20 });

await page.goto(`${BASE}/profiles`);
await settle();
await crop("profiles", page.getByText("Listing style").first(), { dy: -130 });

await page.goto(`${BASE}/analytics`);
await settle();
// The demo account's sales and fees are in its first shop.
const data = await page.evaluate(() => fetch("/api/shops").then((r) => r.json()));
const north = (data.shops ?? data).find((s) => /Northwind/.test(`${s.display_name} ${s.shop_name}`));
await page.evaluate((id) => localStorage.setItem("listyro.shop", id), north.id ?? north.connection_id);
await page.reload();
await settle();
await crop("report", page.getByText("Etsy fees", { exact: false }).first(), { dy: -340 });

await browser.close();
