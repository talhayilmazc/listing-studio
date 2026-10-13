#!/usr/bin/env node
/**
 * Every image the site's source refers to must be a file in public/.
 *
 * The list is read from the source itself: any quoted absolute path to an image
 * ("/og-v3.png", "/screens/feature-shops.webp") in app/, components/ or lib/.
 * Nothing names the files a second time, so the check cannot fall behind when a
 * picture is replaced: a page that points at a file that is not there fails
 * `npm run build` (this runs as "prebuild") and `npm test`, and the production
 * image is checked against the same list after public/ is copied into it
 * (docker/frontend.prod.Dockerfile).
 *
 *   node scripts/public-assets.cjs                 check, and print the list
 *   node scripts/public-assets.cjs --write FILE    also write the list to FILE, one path a line
 *   node scripts/public-assets.cjs --json          print {referenced, missing, unused} as JSON
 */
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.join(__dirname, "..");
const SOURCES = ["app", "components", "lib", "middleware.ts"];
const IMAGE = "png|webp|jpe?g|svg|gif|ico|avif";
const REFERENCE = new RegExp("[\"'`](/[A-Za-z0-9_./-]+\\.(?:" + IMAGE + "))[\"'`]", "g");

function sourceFiles(entry) {
  const full = path.join(ROOT, entry);
  if (!fs.existsSync(full)) return [];
  if (fs.statSync(full).isFile()) return [full];
  return fs.readdirSync(full, { withFileTypes: true }).flatMap((item) => {
    const relative = path.join(entry, item.name);
    if (item.isDirectory()) return sourceFiles(relative);
    return /\.tsx?$/.test(item.name) && !item.name.endsWith(".test.ts") ? [path.join(ROOT, relative)] : [];
  });
}

/** Paths the source points at, and where each is named. */
function referenced() {
  const found = new Map();
  for (const file of SOURCES.flatMap(sourceFiles)) {
    for (const match of fs.readFileSync(file, "utf8").matchAll(REFERENCE)) {
      // A file Next serves from app/ by convention (favicon.ico, icon.svg) is not in public/.
      if (fs.existsSync(path.join(ROOT, "app", match[1]))) continue;
      if (!found.has(match[1])) found.set(match[1], path.relative(ROOT, file).replace(/\\/g, "/"));
    }
  }
  return new Map([...found].sort());
}

function publicImages(dir = path.join(ROOT, "public"), prefix = "") {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((item) => {
    const relative = `${prefix}/${item.name}`;
    if (item.isDirectory()) return publicImages(path.join(dir, item.name), relative);
    return new RegExp("\\.(?:" + IMAGE + ")$").test(item.name) ? [relative] : [];
  });
}

function report() {
  const refs = referenced();
  const present = (asset) => {
    const file = path.join(ROOT, "public", asset);
    return fs.existsSync(file) && fs.statSync(file).size > 0;
  };
  const missing = [...refs].filter(([asset]) => !present(asset)).map(([asset, by]) => ({ asset, by }));
  const unused = publicImages().filter((asset) => !refs.has(asset)).sort();
  return { referenced: [...refs.keys()], missing, unused };
}

function main(argv) {
  const result = report();
  if (argv.includes("--json")) {
    process.stdout.write(JSON.stringify(result) + "\n");
    return result.missing.length || !result.referenced.length ? 1 : 0;
  }
  if (!result.referenced.length) {
    console.error("public-assets: found no image references in the source; the scan is broken");
    return 1;
  }
  for (const { asset, by } of result.missing) console.error(`public-assets: public${asset} is missing or empty (named in ${by})`);
  if (result.missing.length) return 1;
  const write = argv.indexOf("--write");
  if (write !== -1) {
    if (!argv[write + 1]) {
      console.error("public-assets: --write needs a file");
      return 1;
    }
    fs.writeFileSync(argv[write + 1], result.referenced.join("\n") + "\n");
  }
  console.log(`public-assets: ${result.referenced.length} referenced images, all in public/`);
  for (const asset of result.unused) console.log(`public-assets: note: public${asset} is not referenced by any page`);
  return 0;
}

if (require.main === module) process.exit(main(process.argv.slice(2)));
module.exports = { report, referenced };
