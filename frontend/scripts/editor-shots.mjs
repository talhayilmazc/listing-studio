#!/usr/bin/env node
/**
 * Screenshots of the mockup editor work (feature/mockups-editor, docs/mockups.md) at 1366 and 375 px:
 *
 *   docker compose exec -T api python -m app.cli seed-demo --admin       (prints the password)
 *   PASSWORD=… node scripts/editor-shots.mjs [base url]
 *   docker compose exec -T api python -m app.cli seed-demo --remove      (afterwards)
 *
 * Synthetic demo account only (demo-shots@example.test, its "Ten colours" template of drawn tees), our own local
 * server only; the designs are drawn here (shapes and text, no one's artwork). Saves docs/screens/mockups-editor/
 * <screen>-<width>.webp. The worker makes the mockups, as for a seller.
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const BASE = process.argv[2] ?? "http://localhost:3000";
if (!["localhost", "127.0.0.1"].includes(new URL(BASE).hostname)) throw new Error("our own local server only");
const PASSWORD = process.env.PASSWORD;
if (!PASSWORD) throw new Error("PASSWORD (the demo account's, from seed-demo) is required");
const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "screens", "mockups-editor");
mkdirSync(OUT, { recursive: true });
const WIDTHS = [1366, 375];

/** A drawn design: a coloured badge with a word, transparent around it. */
async function design(word, colour) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="2000">
    <circle cx="900" cy="760" r="620" fill="${colour}"/>
    <text x="900" y="860" font-family="Arial, sans-serif" font-size="300" font-weight="700" fill="#ffffff" text-anchor="middle">${word}</text>
    <text x="900" y="1700" font-family="Arial, sans-serif" font-size="220" font-weight="700" fill="${colour}" text-anchor="middle">SAMPLE</text>
  </svg>`;
  return { name: `${word}.png`, mimeType: "image/png", buffer: await sharp(Buffer.from(svg)).png().toBuffer() };
}

async function shot(page, name, width, fullPage = true) {
  await page.waitForTimeout(600);
  const file = path.join(OUT, `${name}-${width}.webp`);
  const png = await page.screenshot({ fullPage });
  const meta = await sharp(png).metadata();
  await sharp(png).extract({ left: 0, top: 0, width: meta.width, height: Math.min(meta.height, 2400) }).webp({ quality: 80 }).toFile(file);
  console.log("saved", path.relative(process.cwd(), file));
}

const browser = await chromium.launch();
let runUrl = null;
for (const width of WIDTHS) {
  const context = await browser.newContext({ viewport: { width, height: width > 500 ? 900 : 812 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  await page.goto(`${BASE}/login`);
  await page.locator("#email").fill("demo-shots@example.test");
  await page.locator("#password").fill(PASSWORD);
  await page.keyboard.press("Enter");
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 60_000 });

  await page.goto(`${BASE}/dashboard`);
  await page.getByTestId("home-shortcuts").waitFor();
  await page.getByTestId("home-shortcuts").scrollIntoViewIfNeeded();
  await shot(page, "home-shortcuts", width);

  if (!runUrl) {
    // The first pass makes the mockups (the worker); the second opens the same run.
    await page.goto(`${BASE}/mockups`);
    await page.getByTestId("mockup-templates").waitFor();
    const labels = await page.getByTestId("mockup-templates").locator("label").allInnerTexts();
    const index = labels.findIndex((l) => l.includes("Ten colours"));
    await page.getByTestId("mockup-template").nth(index >= 0 ? index : 3).check();
    await page.getByTestId("mockup-designs-files").setInputFiles([await design("SUN", "#c2542d"), await design("SEA", "#2f5d8a"),
      await design("FERN", "#3f6b3f")]);
    await page.getByTestId("mockup-make").waitFor();
    await page.waitForFunction(() => !document.querySelector('[data-testid="mockup-make"]')?.hasAttribute("disabled"), null, { timeout: 120_000 });
    await page.getByTestId("mockup-sharpen").selectOption("low");
    await shot(page, "run-form", width);
    const started = page.waitForResponse((r) => /\/api\/batches\/[^/]+\/mockups$/.test(new URL(r.url()).pathname) && r.request().method() === "POST");
    await page.getByTestId("mockup-make").click();
    const run = await (await started).json();
    await page.getByTestId("mockup-results").waitFor({ timeout: 300_000 });
    await page.getByTestId("mockup-zip").waitFor({ timeout: 300_000 }); // finished
    runUrl = `${BASE}/mockups?run=${run.id}`;
  } else {
    // The run form at this width: the template ticked, sharpening chosen (nothing made).
    await page.goto(`${BASE}/mockups`);
    await page.getByTestId("mockup-templates").waitFor();
    const names = await page.getByTestId("mockup-templates").locator("label").allInnerTexts();
    const at = names.findIndex((l) => l.includes("Ten colours"));
    await page.getByTestId("mockup-template").nth(at >= 0 ? at : 3).check();
    await page.getByTestId("mockup-sharpen").selectOption("low");
    await shot(page, "run-form", width);
    await page.goto(runUrl);
    await page.getByTestId("mockup-zip").waitFor({ timeout: 120_000 });
  }
  // Results: tick two, open the options, ask for a ZIP of the selection.
  const picks = page.getByTestId("mockup-pick");
  await picks.nth(0).check();
  await picks.nth(1).check();
  await page.locator('[data-testid="download-options"] summary').click();
  await page.getByTestId("mockup-results").scrollIntoViewIfNeeded();
  await shot(page, "results-downloads", width);
  await page.getByTestId("mockup-zip-selected").click();
  await page.getByTestId("mockup-zip-status").waitFor();

  // The editor: presets, Centre, apply to the selected, download.
  await page.getByTestId("mockup-thumbs").locator("button").first().click();
  await page.getByTestId("design-editor").waitFor({ timeout: 60_000 });
  const usual = page.getByTestId("design-editor-presets-usual");
  // The presets load with the editor: wait for either the list or the offer to add the usual three.
  await page.locator('[data-testid="design-editor-presets-usual"], [data-testid="design-editor-preset"]').first().waitFor({ timeout: 30_000 });
  if (await usual.count()) await usual.click();
  await page.getByTestId("design-editor-preset").first().waitFor();
  await page.getByTestId("design-editor-preset").nth(1).click(); // Chest print
  await shot(page, "editor", width, false); // the dialog as the seller sees it, not the page under it
  await page.getByTestId("design-editor-presets").scrollIntoViewIfNeeded();
  await shot(page, "editor-presets", width, false);
  await page.getByTestId("design-editor-close").click();

  await page.goto(`${BASE}/mockups/downloads`);
  await page.getByTestId("downloads-list").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(4000); // the ZIP being made shows its progress, then its link
  await shot(page, "downloads", width);
  await context.close();
}
await browser.close();
