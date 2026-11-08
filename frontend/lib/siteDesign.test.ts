// Site v3 (docs/design/home-reference.dc.html, docs/design/logo-reference.dc.html): the tokens are one set and
// readable, the soft glows stay in the hero and the dark sections, the logo is the two hang tags everywhere,
// the public site has no tracked all-caps labels and no arrows, its copy keeps the plain-language rules, the
// hero's entrance plays once in about two and a half seconds and shows its final state to anyone who asks
// for reduced motion, and every picture the site shows has its source and licence written down.
// e2e/marketing.spec.ts checks the same entrance in a real browser.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { HOME, allCopy } from "./homeCopy.ts";
import { COMPARES, ALTERNATIVES, COMPARE_PAGE } from "./marketing/compare.ts";
import { allText } from "./marketing/content.ts";
import { FEATURES, FEATURES_PAGE } from "./marketing/features.ts";
import { GUIDES_PAGE } from "./marketing/guides.ts";
import { FREE_TOOLS, HELP } from "./marketing/help.ts";
import { SHOTS } from "./marketing/heroImages.ts";
import { PROMISE } from "./marketing/testimonials.ts";
import { INCLUDED, LISTING_LINE, PLANNED_NOTICE, PRICING_HEADING, PRICING_LINE, SPECIAL, TRIAL } from "./plans.ts";
import { PRIMARY_CTA } from "./marketing/cta.ts";
import { TAG_BACK, TAG_FRONT } from "./logoPaths.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1");

function files(dir: string): string[] {
  return fs.readdirSync(path.join(FRONTEND, dir), { withFileTypes: true }).flatMap((entry) => {
    const relative = `${dir}/${entry.name}`;
    if (entry.isDirectory()) return files(relative);
    return /\.tsx?$/.test(entry.name) && !entry.name.endsWith(".test.ts") ? [relative] : [];
  });
}

/** Everything that draws the public site. */
const SITE_FILES = [...files("components/marketing"), ...files("app/(marketing)"), "components/LegalShell.tsx", "components/Logo.tsx", "app/not-found.tsx"];

/** The words the site's own copy modules hold (the guides quote Etsy and are checked elsewhere). */
const COPY = [
  ...allCopy(), ...allText(FEATURES), ...allText(FEATURES_PAGE), ...allText(HELP), ...allText(FREE_TOOLS), ...allText(GUIDES_PAGE),
  ...allText(COMPARE_PAGE), ...allText(COMPARES.map((c) => ({ h1: c.h1, lead: c.lead }))), ALTERNATIVES.h1, ALTERNATIVES.lead,
  PLANNED_NOTICE, PRICING_HEADING, PRICING_LINE, LISTING_LINE, TRIAL.line, SPECIAL.body, SPECIAL.cta.label, ...INCLUDED, PROMISE.line, ...PROMISE.badges,
];

const luminance = (hex: string) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: string, b: string) => {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};

test("the tokens are one set, shared by the site and the app, and readable", () => {
  const tailwind = read("tailwind.config.ts");
  const css = read("app/globals.css");
  for (const [name, value] of [["ink", "#14162B"], ["tag", "#FFD24A"], ["paper", "#FFFFFF"], ["mist", "#F5F6FA"], ["line", "#E6E8EF"], ["body", "#4B4F6B"], ["muted", "#6A6F8A"]]) {
    assert.match(tailwind, new RegExp(`${name}: "${value}"`), name);
    assert.match(css, new RegExp(`--${name}: ${value.toLowerCase()};`), name);
  }
  assert.match(tailwind, /go: \{ DEFAULT: "#1F8A4C", ink: "#1A7340" \}/);
  // The app's names point at the same values: its primary action is ink, its text body and muted.
  assert.match(tailwind, /600: "#14162B", \/\/ --accent: ink/);
  assert.match(tailwind, /400: "#6A6F8A"/);
  assert.match(tailwind, /600: "#4B4F6B"/);
  // Text contrast (WCAG AA): ink, body, muted and go-ink on paper and mist; ink on the tag; light text on ink.
  for (const bg of ["#FFFFFF", "#F5F6FA"]) for (const fg of ["#14162B", "#4B4F6B", "#6A6F8A", "#1A7340"]) assert.ok(contrast(fg, bg) >= 4.5, `${fg} on ${bg}`);
  assert.ok(contrast("#14162B", "#FFD24A") >= 4.5, "ink on tag yellow");
  assert.ok(contrast("#3B3F5C", "#FFD24A") >= 4.5, "the yellow section's secondary text");
  for (const fg of ["#FFFFFF", "#C3C7DD", "#B7BBD3", "#A4A9C4"]) assert.ok(contrast(fg, "#14162B") >= 4.5, `${fg} on ink`);
  // Tag yellow is never text on paper: too faint.
  assert.ok(contrast("#FFD24A", "#FFFFFF") < 3);
  // The reference's faint step numbers (#9A9FB8, 2.6:1) and small green labels (#1F8A4C on its tint, 3.9:1)
  // are drawn in muted and go-ink instead.
  assert.ok(contrast("#9A9FB8", "#FFFFFF") < 4.5 && contrast("#1F8A4C", "#EAF6F0") < 4.5);
  const sections = code(read("components/marketing/Sections.tsx"));
  assert.doesNotMatch(sections, /#9A9FB8/);
  assert.match(sections, /color: "#1A7340"/);
  // No old v2 colours, no Etsy orange, no violet utility classes.
  for (const file of ["app/globals.css", "tailwind.config.ts", ...SITE_FILES]) {
    const text = code(read(file));
    assert.doesNotMatch(text, /#14213D|#FFCF33|#f1641e|#f56400|#d5641c/i, file);
    assert.doesNotMatch(text, /\b(violet|purple)-\d|bg-gradient|from-\w+-\d+ to-/, file);
  }
  // One typeface, self-hosted: Archivo with its width axis, never swapped in late.
  const layout = read("app/layout.tsx");
  assert.match(layout, /import \{ Archivo \} from "next\/font\/google";/);
  assert.match(layout, /axes: \["wdth"\]/);
  assert.match(layout, /display: "optional"/);
  assert.match(css, /\.mk-display,\n\.mk-h1 \{\n {2}font-size: clamp\(40px, 5\.2vw, 68px\);\n {2}line-height: 0\.98;\n {2}font-weight: 900;/);
});

test("the soft yellow and lilac glows are only in the hero and the dark sections", () => {
  const glow = /radial-gradient/g;
  // How many radial gradients each file draws: the hero three (yellow, lilac, blue), every ink block two.
  const allowed = new Map([
    ["app/(marketing)/page.tsx", 3], // the hero (HERO_GLOW)
    ["components/marketing/Sections.tsx", 4], // the shops section and the closing block, both ink
    ["components/marketing/Page.tsx", 2], // every long page's closing block, ink
    ["app/og/[slug]/route.tsx", 2], // the share card, ink
  ]);
  for (const file of [...SITE_FILES, "app/og/[slug]/route.tsx"]) {
    const found = (code(read(file)).match(glow) ?? []).length;
    assert.equal(found, allowed.get(file) ?? 0, file);
  }
  assert.match(read("app/(marketing)/page.tsx"), /const HERO_GLOW =/);
  // Each glow outside the hero sits on ink.
  for (const file of ["components/marketing/Sections.tsx", "components/marketing/Page.tsx", "app/og/[slug]/route.tsx"]) {
    for (const match of code(read(file)).matchAll(glow)) {
      const around = code(read(file)).slice(Math.max(0, match.index! - 400), match.index! + 50);
      assert.match(around, /background: "#14162B"/, file);
    }
  }
});

test("the logo is the two hang tags, the same drawing everywhere, with its files and icons", () => {
  // The reference's drawing: the back tag yellow, turned 12 degrees; the front tag ink, turned -8, punched.
  assert.equal(TAG_BACK, "M28 4 H42 L54 16 V40 Q54 46 48 46 H28 Q22 46 22 40 V10 Q22 4 28 4 Z");
  assert.equal(TAG_FRONT, "M18 16 H32 L44 28 V52 Q44 58 38 58 H18 Q12 58 12 52 V22 Q12 16 18 16 Z");
  const logo = read("components/Logo.tsx");
  assert.match(logo, /rotate\(12 40 26\)/);
  assert.match(logo, /rotate\(-8 30 36\)/);
  assert.match(logo, /<circle cx="33" cy="27\.5" r="3\.8"/);
  assert.match(read("lib/logoPaths.ts"), /export const WORDMARK = "M/);
  for (const file of ["public/brand/mark.svg", "public/brand/logo.svg", "public/brand/logo-dark.svg", "public/brand/icon.svg", "app/icon.svg"]) {
    const svg = read(file);
    assert.ok(svg.includes(TAG_BACK) && svg.includes(TAG_FRONT), file);
    assert.match(svg, /fill="#FFD24A"/, file);
  }
  assert.match(read("public/brand/logo-dark.svg"), /<path transform="translate[^"]*" d="M[^"]*" fill="#FFFFFF"\/>/);
  assert.equal(read("app/icon.svg"), read("public/brand/icon.svg"));
  // The favicon set: 16, 32 and 48 in the .ico, 180 for Apple, 192 and a maskable 512 in the manifest.
  const ico = fs.readFileSync(path.join(FRONTEND, "app/favicon.ico"));
  assert.equal(ico.readUInt16LE(4), 3);
  assert.deepEqual([0, 1, 2].map((i) => ico.readUInt8(6 + 16 * i)), [16, 32, 48]);
  const pngSize = (file: string) => { const b = fs.readFileSync(path.join(FRONTEND, file)); return [b.readUInt32BE(16), b.readUInt32BE(20)]; };
  assert.deepEqual(pngSize("app/apple-icon.png"), [180, 180]);
  assert.deepEqual(pngSize("public/brand/icon-192.png"), [192, 192]);
  assert.deepEqual(pngSize("public/brand/icon-512-maskable.png"), [512, 512]);
  assert.match(read("app/manifest.ts"), /purpose: "maskable"/);
  // The share card and every frame use the same logo.
  assert.match(read("app/og/[slug]/route.tsx"), /d=\{TAG_BACK\}/);
  for (const file of ["components/Rail.tsx", "components/AuthShell.tsx", "components/marketing/Site.tsx"]) assert.match(read(file), /<Logo /, file);
});

test("no tracked all-caps labels and no arrows on buttons or links, anywhere on the public site", () => {
  for (const file of SITE_FILES) {
    const source = code(read(file));
    for (const [, classes] of source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
      if (!classes) continue;
      assert.doesNotMatch(classes, /\btracking-(wide|wider|widest|\[0\.\d+em\])/, `${file}: tracked letters (${classes})`);
      if (/\buppercase\b/.test(classes)) assert.equal(file, "components/LegalShell.tsx", `${file}: ${classes}`);
    }
    assert.doesNotMatch(source, /[→⟶➜➔»›]|&rarr;|&raquo;|-&gt;|textTransform: "uppercase"/, `${file}: an arrow or capitals`);
    for (const [, label] of source.matchAll(/>([^<>{}]*)<\/(a|button)>/g)) assert.doesNotMatch(label, /(->|>>|\s>)\s*$/, `${file}: ${label}`);
  }
  assert.match(read("app/globals.css"), /\.site \.label \{\n {2}text-transform: none;\n {2}letter-spacing: 0;/);
  for (const text of COPY) assert.doesNotMatch(text, /[→⟶➜➔»›]|\s->\s/, text);
});

test("the copy keeps the plain-language rules: sentence case, no em dashes, no filler, no emoji", () => {
  for (const text of COPY) {
    assert.doesNotMatch(text, /—/, `em dash: ${text}`);
    assert.doesNotMatch(text, /\b(seamless(ly)?|effortless(ly)?|unlock(s|ed|ing)?|supercharg\w*|elevat(e|es|ed|ing))\b/i, text);
    assert.doesNotMatch(text, /\p{Extended_Pictographic}/u, `emoji: ${text}`);
  }
  // Headings are sentence case: after the first word, only names and acronyms are capitalised.
  const names = /^(Listyro|Etsy|Etsy's|AI|SKU|I|Photoshop|Every|Get|Shop|Manager|Help|API|Pro|Business|Growth|Starter|Special)$/;
  const heads = [
    HOME.hero.heading, HOME.grind.heading, HOME.how.heading, HOME.shops.heading, HOME.safe.heading, HOME.pricing.heading, HOME.faq.heading, HOME.closing.heading,
    ...HOME.how.steps.map((s) => s.title), ...HOME.safe.items.map((i) => i.title), ...HOME.faq.items.map((i) => i.q), PRICING_HEADING,
  ];
  for (const heading of heads) {
    for (const sentence of heading.split(/[.?!]\s+/)) {
      const words = sentence.split(/\s+/).slice(1).map((w) => w.replace(/[,.:;?!]+$/, ""));
      for (const word of words) if (/^[A-Z]/.test(word)) assert.match(word, names, `${heading}: "${word}"`);
    }
  }
  // Buttons say what happens.
  assert.deepEqual([PRIMARY_CTA.label, HOME.hero.primary.label, HOME.closing.primary.label, SPECIAL.cta.label], ["Start free", "Start free with 10 listings", "Start free", "Talk to us"]);
});

test("the hero's entrance: tiles left to right, tags one by one, shops last; about 2.5 s; reduced motion shows the end", () => {
  const css = read("app/globals.css");
  const demo = read("components/marketing/HeroDemo.tsx");
  // The animations exist only inside prefers-reduced-motion: no-preference. Outside it the classes do nothing,
  // so with reduced motion every piece is simply there: the final state.
  const motion = css.indexOf("@media (prefers-reduced-motion: no-preference) {");
  assert.ok(motion !== -1);
  const block = css.slice(motion, css.indexOf("@keyframes hd-in"));
  assert.match(block, /\.hd-in \{\n {4}animation: hd-in [^;]+ both;/);
  assert.match(block, /\.hd-pop \{\n {4}animation: hd-pop [^;]+ both;/);
  for (const name of ["hd-in", "hd-pop"]) {
    const outside = css.slice(0, motion) + css.slice(motion + block.length).replace(/@keyframes [\w-]+ \{[\s\S]*?\n\}/g, "");
    assert.doesNotMatch(outside, new RegExp(`\\.${name}\\b`), `.${name} styled outside the motion query`);
  }
  // Once, not on a loop; opacity and transform only, so nothing is laid out again.
  assert.doesNotMatch(block, /infinite|alternate/);
  for (const keyframes of css.matchAll(/@keyframes hd-[\w-]+ \{([\s\S]*?)\n\}/g)) {
    const properties = [...keyframes[1].matchAll(/^\s+([a-z-]+):/gm)].map((m) => m[1]);
    assert.deepEqual([...new Set(properties)].sort(), ["opacity", "transform"]);
  }
  // No other animation anywhere on the site, and no transition but the buttons' colours.
  for (const file of SITE_FILES) assert.doesNotMatch(code(read(file)), /\banimate-|animation:|transition-(all|transform)/, file);
  // The order and the length: the timing table in the component.
  const timing = demo.match(/export const TIMING = (\{[^\n]+\});/)![1];
  const T = new Function(`return ${timing.replace(/: number/g, "")};`)() as { tile: (i: number) => number; card: number; tag: (i: number) => number; shopCard: number; row: (i: number) => number };
  const tiles = HOME.hero.window.tiles.length, tags = HOME.hero.ai.tags.length, rows = HOME.hero.shops.rows.length;
  for (let i = 1; i < tiles; i++) assert.ok(T.tile(i) > T.tile(i - 1), "tiles left to right");
  for (let i = 1; i < tags; i++) assert.ok(T.tag(i) > T.tag(i - 1), "tags in sequence");
  assert.ok(T.tag(0) > T.card && T.row(0) > T.tag(tags - 1) - 0.5 && T.row(0) > T.tile(tiles - 1), "the shop rows come last");
  const end = Math.max(T.tile(tiles - 1) + 0.45, T.tag(tags - 1) + 0.35, T.row(rows - 1) + 0.45);
  assert.ok(end >= 2.2 && end <= 2.8, `ends at ${end}s`);
  // No piece starts hidden by its own markup, and every picture has its size, so nothing moves.
  assert.doesNotMatch(demo, /opacity-0|invisible|display: "none"/);
  assert.match(demo, /width=\{shot\.width\}\n\s+height=\{shot\.height\}/);
});

test("the hero's pictures weigh under 250 KB, at 2x, whichever format the browser takes", () => {
  let bytes = 0;
  for (const tile of HOME.hero.window.tiles) {
    const size = (file: string) => fs.statSync(path.join(FRONTEND, "public", file)).size;
    bytes += Math.max(size(tile.shot.avif2x), size(tile.shot.webp2x));
  }
  assert.ok(bytes < 250 * 1024, `${bytes} bytes`);
  // Every picture comes at 1x and 2x, AVIF and WebP, with its size.
  for (const shot of Object.values(SHOTS)) {
    assert.ok(shot.width > 0 && shot.height > 0);
    for (const file of [shot.avif, shot.avif2x, shot.webp, shot.webp2x]) assert.ok(fs.existsSync(path.join(FRONTEND, "public", file)), file);
  }
});

test("every picture the public site shows has its source and licence in docs/marketing-assets.md", () => {
  const doc = read("../docs/marketing-assets.md");
  const images: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(path.join(FRONTEND, dir), { withFileTypes: true })) {
      const relative = `${dir}/${entry.name}`;
      if (entry.isDirectory()) walk(relative);
      else if (/\.(png|webp|avif|jpe?g|svg|ico)$/.test(entry.name)) images.push(relative);
    }
  };
  walk("public");
  for (const file of ["app/icon.svg", "app/favicon.ico", "app/apple-icon.png"]) images.push(file);
  assert.ok(images.length > 50);
  for (const file of images) {
    const name = file.replace(/^(public|app)\//, "");
    const line = doc.split("\n").find((l) => l.includes("`" + name + "`"));
    assert.ok(line, `${name} is not listed`);
    assert.match(line!, /licen[cs]e|ours|our own/i, `${name}: no licence`);
  }
  // Each photo: its page on Pexels, the photographer and the licence; no generated image.
  const photos = [...doc.matchAll(/^\| `([a-z-]+)\.jpg` \| (https:\/\/www\.pexels\.com\/photo\/[^\s|]+) \| ([^|]+) \| ([^|]+) \|/gm)];
  assert.equal(photos.length, 7);
  for (const [, name, , who, licence] of photos) {
    assert.ok(who.trim().length > 2, name);
    assert.match(licence, /Pexels License/, name);
  }
});
