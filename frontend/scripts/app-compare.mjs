#!/usr/bin/env node
/**
 * Before and after, side by side (app/onboarding-v1): the same screen from docs/screens/app-before and
 * docs/screens/app-after, each cut to ``HEIGHT`` px and labelled, written to docs/screens/app-after/compare-<name>.jpg.
 *
 *   node scripts/app-compare.mjs empty-home-1366 full-review-1366 …
 *   BEFORE=app-after AFTER=onboarding-followups node scripts/app-compare.mjs …   (other folders; written to AFTER)
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "screens");
const BEFORE = process.env.BEFORE ?? "app-before";
const AFTER = process.env.AFTER ?? "app-after";
const names = process.argv.slice(2);
if (!names.length) throw new Error("usage: app-compare.mjs <screen> …");

async function panel(file, label, width, height) {
  const img = sharp(file);
  const meta = await img.metadata();
  const scale = width / meta.width;
  const cut = Math.min(meta.height, Math.round(height / scale));
  const body = await sharp(file).extract({ left: 0, top: 0, width: meta.width, height: cut }).resize({ width }).toBuffer();
  const bodyHeight = (await sharp(body).metadata()).height;
  const head = Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="44"><rect width="100%" height="100%" fill="#14162B"/>` +
    `<text x="16" y="29" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#FFD24A">${label}</text></svg>`);
  return sharp({ create: { width, height: height + 44, channels: 3, background: "#F5F6FA" } })
    .composite([{ input: head, top: 0, left: 0 }, { input: body, top: 44, left: 0 }])
    .png().toBuffer().then((b) => ({ b, h: bodyHeight }));
}

for (const name of names) {
  const phone = name.endsWith("-375");
  const width = phone ? 375 : 760;
  const height = phone ? 1300 : 900;
  const before = await panel(path.join(ROOT, BEFORE, `${name}.webp`), "Before", width, height);
  const after = await panel(path.join(ROOT, AFTER, `${name}.webp`), "After", width, height);
  const gap = 16;
  await sharp({ create: { width: width * 2 + gap, height: height + 44, channels: 3, background: "#FFFFFF" } })
    .composite([{ input: before.b, top: 0, left: 0 }, { input: after.b, top: 0, left: width + gap }])
    .jpeg({ quality: 78 })
    .toFile(path.join(ROOT, AFTER, `compare-${name}.jpg`));
  console.log(`compare-${name}.jpg`);
}
