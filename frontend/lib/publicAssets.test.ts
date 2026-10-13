// The images the site points at exist, and nothing checks for a file by a name of its own.
//
// A production build once failed because its Dockerfile still tested for a picture
// (og-v2.png) that the pages had stopped using and that had been deleted. The list is
// now read from the source (scripts/public-assets.cjs); these tests keep it that way.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { HOME } from "./homeCopy.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const REPO = path.join(FRONTEND, "..");
const SCRIPT = path.join(FRONTEND, "scripts", "public-assets.cjs");

type Report = { referenced: string[]; missing: { asset: string; by: string }[]; unused: string[] };
const report = (): Report => JSON.parse(execFileSync(process.execPath, [SCRIPT, "--json"], { encoding: "utf8" }));

test("every image a page points at is a file in public/, and public/ holds nothing else", () => {
  const found = report();
  assert.deepEqual(found.missing, []);
  // A picture no page uses is dead weight in every image and every deploy: remove it with the page.
  assert.deepEqual(found.unused, []);
  // The scan sees what the home page uses: its screenshots and the share image.
  const screens = HOME.apart.cards.flatMap((card) => (card.visual.kind === "screen" ? [card.visual.src] : []));
  assert.ok(screens.length >= 4);
  for (const src of screens) assert.ok(found.referenced.includes(src), src);
  const share = found.referenced.filter((asset) => /^\/og-[\w.-]+\.png$/.test(asset));
  assert.equal(share.length, 1, "one share image, named in one spelling everywhere");
  for (const file of ["app/layout.tsx", "app/(marketing)/page.tsx"]) {
    assert.ok(fs.readFileSync(path.join(FRONTEND, file), "utf8").includes(`"${share[0]}"`), file);
  }
});

test("the build and the production image check the same list, and name no picture themselves", () => {
  const scripts = JSON.parse(fs.readFileSync(path.join(FRONTEND, "package.json"), "utf8")).scripts;
  assert.equal(scripts.prebuild, "node scripts/public-assets.cjs");
  const dockerfile = fs.readFileSync(path.join(REPO, "docker", "frontend.prod.Dockerfile"), "utf8");
  const code = dockerfile.split("\n").filter((line) => !line.trimStart().startsWith("#")).join("\n");
  // The list is written from the source at build time and read back in the final image.
  assert.match(code, /node scripts\/public-assets\.cjs --write \/app\/public-assets\.txt/);
  assert.match(code, /COPY --from=build [^\n]*\/app\/public-assets\.txt/);
  assert.match(code, /while read -r asset; do test -s "public\$asset"/);
  // No file name of its own, in the Dockerfile or in any deploy script.
  const named = /\b[\w.-]+\.(png|webp|jpe?g|avif)\b/i;
  assert.doesNotMatch(code, named);
  for (const file of fs.readdirSync(path.join(REPO, "deploy")).filter((name) => name.endsWith(".sh"))) {
    const text = fs.readFileSync(path.join(REPO, "deploy", file), "utf8");
    assert.doesNotMatch(text, /public\/[\w./-]+\.(png|webp|jpe?g|avif)|og-v\d/i, file);
  }
});

test("pictures the site no longer uses are named nowhere", () => {
  const gone = /og-v2|analytics-content|batch-content|phone-2\.webp|review-2\.webp|review-card-2/;
  const look = (dir: string, depth = 0): string[] =>
    fs.readdirSync(path.join(REPO, dir), { withFileTypes: true }).flatMap((item) => {
      const relative = `${dir}/${item.name}`;
      if (item.isDirectory()) return depth > 5 || /^(node_modules|\.next|\.git|__pycache__|samples)$/.test(item.name) ? [] : look(relative, depth + 1);
      if (!/\.(tsx?|cjs|mjs|js|json|md|sh|yml|yaml|py|css|Dockerfile)$|Dockerfile$/.test(item.name) || item.name === "publicAssets.test.ts" || item.name === "package-lock.json") return [];
      return gone.test(fs.readFileSync(path.join(REPO, relative), "utf8")) ? [relative] : [];
    });
  assert.deepEqual(["docker", "deploy", "docs", "frontend", "backend/app", "backend/tests"].flatMap((dir) => look(dir)), []);
  for (const file of ["CLAUDE.md", "docker-compose.yml", "docker-compose.prod.yml"]) {
    assert.doesNotMatch(fs.readFileSync(path.join(REPO, file), "utf8"), gone, file);
  }
});
