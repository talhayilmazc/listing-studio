#!/usr/bin/env node
/**
 * "Etsy" is in no h1 or page title typed into the source, in the site or the app (the approved
 * Commercial Access application; CLAUDE.md). Runs as "prebuild", so `npm run build` and the production
 * image fail on one, and in `npm test`. Headings that come from the marketing registry are checked
 * while the pages are built (lib/marketing/brandRule.ts).
 */
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const SOURCES = ["app", "components"];

const TYPED = [
  /<h1\b[^>]*>([\s\S]*?)<\/h1>/g,
  /<(?:AuthShell|LegalShell)\b[^>]*\btitle="([^"]*)"/g,
  /\btitle: \{ (?:absolute|default): ["`]([^"`]*)["`]/g,
  /metadata[^=\n]*=\s*\{\s*title:\s*["`]([^"`]*)["`]/g,
];

function sourceFiles(dir) {
  return fs.readdirSync(path.join(ROOT, dir), { withFileTypes: true }).flatMap((entry) => {
    const relative = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(relative);
    return entry.name.endsWith(".tsx") ? [relative] : [];
  });
}

/** Each heading or title in the given source that names Etsy. */
function etsyHeadingsIn(source) {
  return TYPED.flatMap((pattern) => [...source.matchAll(pattern)].map((m) => m[1]).filter((text) => /etsy/i.test(text)));
}

function etsyHeadings() {
  return SOURCES.flatMap(sourceFiles).flatMap((file) =>
    etsyHeadingsIn(fs.readFileSync(path.join(ROOT, file), "utf8")).map((text) => `${file}: ${text.trim()}`));
}

if (require.main === module) {
  const found = etsyHeadings();
  for (const line of found) console.error(`"Etsy" may not appear in a title or h1: ${line}`);
  if (found.length) process.exit(1);
}

module.exports = { etsyHeadings, etsyHeadingsIn };
