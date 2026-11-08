// The icon set, from the logo files (scripts/logo-assets.py writes public/brand/*.svg).
//
//   node frontend/scripts/logo-icons.mjs
//
// app/icon.svg        the browser tab icon (ink square, yellow back tag, white front tag)
// app/favicon.ico     16, 32 and 48 px of the same
// app/apple-icon.png  180 px, full bleed (iOS rounds the corners itself)
// public/brand/icon-192.png, icon-512-maskable.png   for the web manifest (app/manifest.ts);
//                     the maskable one keeps the mark inside the 80% safe circle
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const brand = join(root, "public", "brand");
const INK = "#14162B";

copyFileSync(join(brand, "icon.svg"), join(root, "app", "icon.svg"));

const icon = readFileSync(join(brand, "icon.svg"));
const render = (svg, size) => sharp(svg, { density: Math.ceil((72 * size) / 64) * 2 }).resize(size, size).png({ compressionLevel: 9 }).toBuffer();

/** The mark alone on a full ink square, at ``share`` of the side. */
function fullBleed(share) {
  const inner = readFileSync(join(brand, "icon.svg"), "utf8").match(/<g transform="translate[^"]*">([\s\S]*)<\/g><\/svg>/)[1];
  const offset = (64 - 64 * share) / 2;
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="${INK}"/>` +
      `<g transform="translate(${offset} ${offset}) scale(${share})">${inner}</g></svg>`,
  );
}

// favicon.ico: PNG entries (every browser since IE Vista reads them).
const sizes = [16, 32, 48];
const pngs = await Promise.all(sizes.map((size) => render(icon, size)));
const header = Buffer.alloc(6 + 16 * sizes.length);
header.writeUInt16LE(0, 0);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(sizes.length, 4);
let offset = header.length;
sizes.forEach((size, i) => {
  const at = 6 + 16 * i;
  header.writeUInt8(size, at);
  header.writeUInt8(size, at + 1);
  header.writeUInt16LE(1, at + 4);
  header.writeUInt16LE(32, at + 6);
  header.writeUInt32LE(pngs[i].length, at + 8);
  header.writeUInt32LE(offset, at + 12);
  offset += pngs[i].length;
});
writeFileSync(join(root, "app", "favicon.ico"), Buffer.concat([header, ...pngs]));

writeFileSync(join(root, "app", "apple-icon.png"), await render(fullBleed(0.78), 180));
writeFileSync(join(brand, "icon-192.png"), await render(icon, 192));
writeFileSync(join(brand, "icon-512-maskable.png"), await render(fullBleed(0.62), 512));
console.log("icons written");
