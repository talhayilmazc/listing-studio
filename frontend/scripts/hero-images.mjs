// The home page's shirt pictures, from the mockup engine's lossless output to what the page loads.
//
//   node frontend/scripts/hero-images.mjs <folder from backend/scripts/hero_assets.py>
//
// Each <name>.png, <name>-2x.png (and -4x) becomes <name>.avif / <name>.webp in public/hero, and
// lib/marketing/heroImages.ts lists every file with its 1x size in CSS px: the page names each picture it
// uses from there, and scripts/public-assets.cjs checks that each listed file exists.
import { readdirSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const from = process.argv[2];
if (!from) throw new Error("usage: hero-images.mjs <folder of PNGs>");
const to = join(root, "public", "hero");
mkdirSync(to, { recursive: true });

const pictures = new Map();
let total = 0;
for (const file of readdirSync(from).filter((f) => f.endsWith(".png")).sort()) {
  const name = file.slice(0, -4);
  const input = sharp(join(from, file));
  await input.clone().avif({ quality: 62, effort: 7 }).toFile(join(to, name + ".avif"));
  await input.clone().webp({ quality: 80, effort: 6 }).toFile(join(to, name + ".webp"));
  total += statSync(join(to, name + ".avif")).size;
  const [, base, scale] = name.match(/^(.*?)(?:-(\d)x)?$/);
  const entry = pictures.get(base) ?? { scales: [] };
  if (!scale) {
    const meta = await input.metadata();
    entry.width = meta.width;
    entry.height = meta.height;
  }
  entry.scales.push(Number(scale ?? 1));
  pictures.set(base, entry);
}

const lines = [
  "// Written by scripts/hero-images.mjs; do not edit by hand.",
  "// The home page's shirt pictures: the mockup engine's output on real blank-shirt photos",
  "// (backend/scripts/hero_assets.py; every photo's source, photographer and licence is in docs/marketing-assets.md).",
  "// Each picture's files at 1x and 2x (shop covers also 4x), AVIF and WebP, and its size in CSS px.",
  "export type Shot = { avif: string; avif2x: string; webp: string; webp2x: string; width: number; height: number };",
  "export type WideShot = Shot & { avif4x: string; webp4x: string };",
  "",
  "export const SHOTS = {",
];
for (const [base, { width, height, scales }] of pictures) {
  const files = scales
    .sort()
    .flatMap((s) => {
      const suffix = s === 1 ? "" : `-${s}x`;
      const key = s === 1 ? "" : `${s}x`;
      return [`avif${key}: "/hero/${base}${suffix}.avif"`, `webp${key}: "/hero/${base}${suffix}.webp"`];
    })
    .join(", ");
  lines.push(`  ${base.replace(/-/g, "_")}: { ${files}, width: ${width}, height: ${height} },`);
}
lines.push("};", "");
writeFileSync(join(root, "lib", "marketing", "heroImages.ts"), lines.join("\n"));
console.log(`${pictures.size} pictures, avif total ${total} bytes`);
