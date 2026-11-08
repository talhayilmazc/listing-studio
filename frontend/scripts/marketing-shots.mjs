#!/usr/bin/env node
/**
 * The public site's product pictures, from the synthetic demo account only (docs/marketing-screens.md):
 *
 *   docker compose exec -T api python -m app.cli seed-demo --admin --shops 15     (prints the password)
 *   LAYOUT_EMAIL=demo-shots@example.test LAYOUT_PASSWORD=… node scripts/marketing-shots.mjs [http://localhost:3000]
 *   docker compose exec -T api python -m app.cli seed-demo --remove               (afterwards)
 *
 * Each picture is a crop of the part that matters (docs/design-v2.md), taken at twice its size and saved to
 * public/screens; lib/marketing/screens.ts gives its natural size, which the page never exceeds, so its text
 * reads as it does in the app. Each is taken twice: from the app's wide layout, and from its phone layout
 * ("-phone", 359 px wide) for phones, where the wide crop would shrink below a readable size. Our own server only.
 */
import { chromium } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const BASE = process.argv[2] ?? process.env.LAYOUT_BASE_URL ?? "http://localhost:3000";
if (!["localhost", "127.0.0.1"].includes(new URL(BASE).hostname)) throw new Error("our own local server only");
if (process.env.LAYOUT_EMAIL !== "demo-shots@example.test") throw new Error("the synthetic demo account only");

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "public", "screens");
const ONLY = process.env.ONLY ? new Set(process.env.ONLY.split(",")) : null;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 2, timezoneId: "America/Chicago", reducedMotion: "reduce" });
const page = await context.newPage();

await page.goto(`${BASE}/login`);
await page.getByLabel(/email/i).fill(process.env.LAYOUT_EMAIL);
await page.getByLabel(/password/i).fill(process.env.LAYOUT_PASSWORD);
await page.getByRole("button", { name: /sign in/i }).click();
await page.waitForURL(/dashboard|accept-terms/, { timeout: 180000 });

async function settle() {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(900);
}

async function batchHref(name) {
  await page.goto(`${BASE}/batches`);
  await settle();
  return page.getByRole("link", { name }).first().getAttribute("href");
}

/**
 * ``width`` x ``height`` CSS pixels whose top-left is the anchor's, less ``pad`` (and ``dx``/``dy``): the
 * part of the screen that matters, at its natural size.
 */
let PHONE = false;
async function crop(name, anchor, options) {
  if (ONLY && !ONLY.has(name)) return;
  const { width, height, pad = 12, dx = 0, dy = 0 } = PHONE
    ? { ...options, width: 359, height: Math.round(options.height * (options.phoneHeight ?? 1.3)), pad: options.pad ?? 12, dx: 0, dy: options.phoneDy ?? options.dy ?? 0 }
    : options;
  name = PHONE ? `${name}-phone` : name;
  const el = anchor.first();
  await el.scrollIntoViewIfNeeded();
  // Room above the anchor for the crop's own offset.
  await el.evaluate((node, above) => window.scrollTo(0, node.getBoundingClientRect().top + window.scrollY - above), 120 + Math.max(0, pad - dy));
  // The app's footer prints the support address of the machine it runs on: never in a picture.
  await page.addStyleTag({ content: "footer { visibility: hidden !important; } nextjs-portal { display: none !important; }" });
  await page.mouse.move(0, 0);
  await page.waitForTimeout(600);
  const box = await el.boundingBox();
  const viewport = page.viewportSize();
  const x = PHONE ? 8 : Math.max(0, Math.min(box.x - pad + dx, viewport.width - width));
  const shot = await page.screenshot({ clip: { x, y: Math.max(box.y - pad + dy, 0), width, height } });
  const file = path.join(OUT, `${name}.webp`);
  await sharp(shot).webp({ quality: 80 }).toFile(file);
  console.log(`${name}.webp ${width}x${height}`);
}

async function shoot() {
  await page.setViewportSize({ width: PHONE ? 375 : 1280, height: 1000 });
  const summer = await batchHref(/Summer drop/);

  await page.goto(`${BASE}${summer}`);
  await settle();
  await crop("step-upload", page.getByText("Tap an image to move it or make it the cover.").first(), { width: 520, height: 300, pad: 20, dx: 4, dy: -180, phoneDy: -250 });

  await page.goto(`${BASE}${summer}/review`);
  await settle();
  await crop("step-write", page.getByText("Title", { exact: true }).locator("visible=true"), { width: 520, height: 330, pad: 16 });
  await crop("why-writer", page.getByText("Description", { exact: true }).locator("visible=true"), { width: 520, height: 150, pad: 16 });
  await crop("step-send", page.locator("section, div").filter({ has: page.getByText("Where and when", { exact: true }) }).filter({ hasText: "Send to" }).last(), { width: 520, height: 300, pad: 2 });
  await crop("step-review", page.getByText("Search-readiness checklist").locator("visible=true"), { width: 520, height: 300, pad: 16, dx: -36 });

  // The rest read best in the app's one-column layout: everything that matters fits the crop's width.
  await page.setViewportSize({ width: PHONE ? 375 : 560, height: 1000 });

  const brand = await batchHref(/Published in Northwind/);
  await page.goto(`${BASE}${brand}`);
  await settle();
  await page.getByRole("combobox").first().selectOption({ label: "Juniper Print Co." }).catch(() => {});
  await settle();
  await crop("why-shops", page.getByText("Working on:"), { width: 520, height: 420, pad: 8 });

  await page.goto(`${BASE}/templates`);
  await settle();
  const ten = page.getByRole("button", { name: /Ten colours/ }).first();
  // The card remembers being open (this browser): click it only when it is closed.
  if ((await ten.count()) && (await ten.getAttribute("aria-expanded")) !== "true") await ten.click();
  await settle();
  await crop("feature-mockups", page.getByText("Select all").locator("visible=true"), { width: 520, height: 420, pad: 8, dy: 60 });

  await page.goto(`${BASE}/scheduled`);
  await settle();
  await crop("feature-schedule", page.getByText("Group A", { exact: true }).first(), { width: 520, height: 420, pad: 14 });

  await page.goto(`${BASE}/profiles`);
  await settle();
  await crop("feature-profiles", page.getByText("Template", { exact: true }).locator("visible=true"), { width: 520, height: 420, pad: 12, dy: -70 });

  await page.goto(`${BASE}/analytics`);
  await settle();
  // The demo account's sales and fees are in its first shop.
  const data = await page.evaluate(() => fetch("/api/shops").then((r) => r.json()));
  const north = (data.shops ?? data).find((s) => /Northwind/.test(`${s.display_name} ${s.shop_name}`));
  await page.evaluate((id) => localStorage.setItem("listyro.shop", id), north.id ?? north.connection_id);
  await page.reload();
  await settle();
  await crop("feature-report", page.getByText("What needs attention").first(), { width: 520, height: 420, pad: 14 });

}

await shoot();
PHONE = true;
await shoot();

await browser.close();
