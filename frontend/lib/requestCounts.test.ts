// Etsy request counts are for admins only (A3): no seller page or component shows a number of
// requests, an estimate per draft, or a daily budget. Admin pages are left out, and so is the
// Overview card "Etsy requests today" (the remaining-quota indicator Etsy's terms require).
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const ALLOWED_FILES = new Set([
  "app/(app)/dashboard/page.tsx", // the Overview card required by Etsy's terms
]);
const COUNT = [
  /\$\{[^}]*\}\s*(Etsy\s+)?requests?\b/, // `${n} requests`
  /≈[^"`]*requests?/, // "≈ 69 requests"
  /requests?\s+(left|so far|to go|today|each|a day)\b/i,
  /\bEtsy requests\b/,
];

function files(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(join(root, dir))) {
    const rel = `${dir}/${name}`;
    if (statSync(join(root, rel)).isDirectory()) out.push(...files(rel));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(rel);
  }
  return out;
}

test("no seller page or component shows an Etsy request count", () => {
  const seller = [...files("app"), ...files("components"), ...files("lib")].filter(
    (f) => !f.startsWith("app/(app)/admin/") && !f.startsWith("components/admin/") && !ALLOWED_FILES.has(f),
  );
  assert.ok(seller.length > 100, "the sources were walked");
  const found: string[] = [];
  for (const file of seller) {
    const lines = readFileSync(join(root, file), "utf8").split("\n");
    lines.forEach((line, i) => {
      if (/^\s*(\/\/|\*|\/\*)/.test(line)) return; // comments
      if (/\badmin\b/.test(line)) return; // a branch shown to admins only
      if (COUNT.some((re) => re.test(line))) found.push(`${file}:${i + 1}: ${line.trim().slice(0, 120)}`);
    });
  }
  assert.deepEqual(found, []);
});
