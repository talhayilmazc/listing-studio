// Site v2, the print shop (docs/design-v2.md): the tokens are one set and readable, the public site has no
// tracked all-caps labels and no arrows on its buttons, its copy keeps the plain-language rules, and the
// hero's demonstration shows its final state to anyone who asks for reduced motion and never moves the page.
// e2e/marketing.spec.ts checks the same demonstration in a real browser.
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
import { INCLUDED, LISTING_LINE, PLANNED_NOTICE, PRICING_HEADING, PRICING_LINE, SPECIAL, TRIAL } from "./plans.ts";

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
  PLANNED_NOTICE, PRICING_HEADING, PRICING_LINE, LISTING_LINE, TRIAL.line, SPECIAL.body, SPECIAL.cta.label, ...INCLUDED,
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
  for (const [name, value] of [["ink", "#14213D"], ["paper", "#FFFFFF"], ["heather", "#D9DCE1"], ["fabric", "#F3F4F6"], ["tag", "#FFCF33"]]) {
    assert.match(tailwind, new RegExp(`${name}: "${value}"`), name);
    assert.match(css, new RegExp(`--${name}: ${value.toLowerCase()};`), name);
  }
  assert.match(tailwind, /go: \{ DEFAULT: "#1F8A4C", ink: "#1A7340" \}/);
  // The app's names point at the same values: its primary action is ink.
  assert.match(tailwind, /600: "#14213D", \/\/ --accent: ink/);
  // Text contrast (WCAG AA): ink and the muted greys on paper and fabric; ink on the tag; go-ink as small text.
  for (const bg of ["#FFFFFF", "#F3F4F6"]) for (const fg of ["#14213D", "#5F6673", "#545B67", "#474D58", "#1A7340"]) assert.ok(contrast(fg, bg) >= 4.5, `${fg} on ${bg}`);
  assert.ok(contrast("#14213D", "#FFCF33") >= 4.5, "ink on tag yellow");
  // Tag yellow is never text on paper: too faint.
  assert.ok(contrast("#FFCF33", "#FFFFFF") < 3);
  // No purple, cream, gradient or Etsy orange left in the shared styles or the site.
  for (const file of ["app/globals.css", "tailwind.config.ts", ...SITE_FILES]) {
    const text = read(file);
    assert.doesNotMatch(text, /#4c1d95|#6d28d9|#5b21b6|#f5f3ff|#fafaf9|#f1641e|#f56400|#d5641c/i, file);
    // The one linear-gradient left draws the transparency checkerboard behind a design: a pattern, not a gradient.
    const withoutChecker = code(text).replace(/\.checkerboard \{[\s\S]*?\n {2}\}/, "");
    assert.doesNotMatch(withoutChecker, /linear-gradient|radial-gradient|bg-gradient|from-\w+-\d+ to-/, file);
    assert.doesNotMatch(code(text), /\b(violet|purple)-\d/, file);
  }
  // One typeface, self-hosted: Archivo with its width axis; the serif is gone.
  const layout = read("app/layout.tsx");
  assert.match(layout, /import \{ Archivo \} from "next\/font\/google";/);
  assert.match(layout, /axes: \["wdth"\]/);
  assert.doesNotMatch(layout + read("app/(marketing)/layout.tsx"), /Instrument_Serif/);
  assert.match(css, /\.mk-display,\n\.mk-h1,\n\.mk-h2 \{\n {2}font-stretch: 125%;\n {2}font-weight: 800;/);
});

test("the mark is a hang tag, the same drawing everywhere, and the app shows it", () => {
  const logo = read("components/Logo.tsx");
  const path = logo.match(/MARK_PATH =\s*"([^"]+)" \+\s*"([^"]+)"/)!;
  const drawing = path[1] + path[2];
  assert.ok(read("app/icon.svg").includes(drawing), "app/icon.svg is the same drawing");
  assert.match(read("app/icon.svg"), /fill="#FFCF33" fill-rule="evenodd" stroke="#14213D"/);
  assert.match(read("app/og/[slug]/route.tsx"), /d=\{MARK_PATH\}/);
  for (const file of ["components/Rail.tsx", "components/AuthShell.tsx", "components/marketing/Site.tsx"]) assert.match(read(file), /<Logo /, file);
  assert.doesNotMatch(read("components/Rail.tsx") + read("components/AuthShell.tsx"), />\s*L\s*</, "the old letter mark is gone");
});

test("no tracked all-caps labels and no arrows on buttons or links, anywhere on the public site", () => {
  for (const file of SITE_FILES) {
    const source = code(read(file));
    for (const [, classes] of source.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
      if (!classes) continue;
      assert.doesNotMatch(classes, /\btracking-(wide|wider|widest|\[0\.\d+em\])/, `${file}: tracked letters (${classes})`);
      // Capitals are kept only for legal text the law asks to be conspicuous (LegalShell's Conspicuous).
      if (/\buppercase\b/.test(classes)) assert.equal(file, "components/LegalShell.tsx", `${file}: ${classes}`);
    }
    // The app's `label` class is tracked capitals; on the site it is re-set in sentence case (globals.css .site .label).
    assert.doesNotMatch(source, /[→⟶➜➔»›]|&rarr;|&raquo;|-&gt;/, `${file}: an arrow`);
    // Button and link labels carry no appended arrow made of characters either.
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
  const names = /^(Listyro|Etsy|Etsy's|AI|SKU|SKUs|I|Shop|Manager|Help|Center|Terms|Privacy|Policy|Seller|Handbook|Listadum|MyDesigns|Vela|POD|Etsy,|Ads|Stats|API|Publish|Approve|Spread|Now|Pro|Business|Growth|Starter|Special|Christmas|Halloween|Valentine's|Mother's|Father's|Thanksgiving|Easter)$/;
  const heads = [HOME.hero.heading, HOME.how.heading, HOME.why.heading, HOME.pricing.heading, HOME.faq.heading, HOME.closing.heading, ...HOME.how.steps.map((s) => s.title), ...HOME.why.items.map((i) => i.title), PRICING_HEADING];
  for (const heading of heads) {
    for (const sentence of heading.split(/[.?!]\s+/)) {
      const words = sentence.split(/\s+/).slice(1).map((w) => w.replace(/[,.:;?!]+$/, ""));
      for (const word of words) if (/^[A-Z]/.test(word)) assert.match(word, names, `${heading}: "${word}"`);
    }
  }
  // Buttons say what happens.
  assert.deepEqual([HOME.hero.secondary.label, HOME.closing.secondary.label, SPECIAL.cta.label], ["See how it works", "See pricing", "Contact us"]);
});

test("the hero's demonstration: reduced motion shows the final state, motion plays once, nothing shifts", () => {
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
  // No piece starts hidden by its own markup, every picture has its size, and it all ends by about four seconds.
  assert.doesNotMatch(demo, /opacity-0|invisible|hidden md:|display: "none"/);
  for (const [, img] of demo.matchAll(/<img ([^>]*)\/>/g)) assert.match(img, /width=\{\d+\} height=\{\d+\}/);
  // The delays, in the order the pieces appear, and how many pieces each staggered row has.
  const delays = [...demo.matchAll(/delay\(([\d.]+)(?: \+ i \* ([\d.]+))?\)/g)].map((m) => [Number(m[1]), Number(m[2] ?? 0)]);
  const rows = [1, 1, 5, 1, HOME.hero.demo.tags.length, HOME.hero.demo.shops.length];
  assert.equal(delays.length, rows.length);
  const ends = delays.map(([start, step], i) => start + step * (rows[i] - 1) + 0.5);
  assert.ok(Math.max(...ends) <= 4.5, `ends at ${Math.max(...ends)}s`);
  // Under 120 KB of pictures.
  const bytes = ["design", "mockup-white", "mockup-sand", "mockup-sage", "mockup-pink", "mockup-mustard"].reduce((sum, name) => sum + fs.statSync(path.join(FRONTEND, `public/hero/${name}.webp`)).size, 0);
  assert.ok(bytes < 120 * 1024, `${bytes} bytes`);
});
