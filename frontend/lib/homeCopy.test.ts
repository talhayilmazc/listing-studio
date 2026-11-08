// The public home page (site v3, docs/design/home-reference.dc.html) keeps its rules: what it may never say,
// that Etsy's name only describes, that every call to action is the configured one, that the trial is worded
// the one way, and that the reference's words are used as decided. Also: no user-facing text says "beta".
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { APPROVED_PHRASES, HERO_VIDEO, HOME, allCopy, headings, withoutApproved } from "./homeCopy.ts";
import { BILLING_ON, PRIMARY_CTA, START_HREF } from "./marketing/cta.ts";
import { TESTIMONIALS } from "./marketing/testimonials.ts";
import { TRIAL } from "./plans.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8");

// The files that make the home page. Its header and footer belong to the site's frame
// (components/marketing/Site.tsx): the footer's trademark notice is checked by backend/tests/test_legal_texts.py.
const HOME_SOURCES = ["lib/homeCopy.ts", "app/(marketing)/page.tsx", "components/marketing/Sections.tsx", "components/marketing/HeroDemo.tsx", "components/marketing/PlanCards.tsx"];

// Never said or implied on the home page (after the owner-approved phrases are taken out).
// "production partner" is Etsy's own name for a listing field, not a claim about us.
const FORBIDDEN: [string, RegExp][] = [
  ["approved", /\bapproved\b/i],
  ["certified", /\bcertified\b/i],
  ["official", /\bofficial(ly)?\b/i],
  ["partner", /\bpartner(s|ed|ship|ships)?\b/i],
  ["100% compliant", /100\s*%\s*compliant|fully compliant|guaranteed compliant/i],
  ["endorsed by", /\bendorsed by\b|\bauthori[sz]ed by etsy\b/i],
  ["the same listing in many shops", /same (listing|design)s? (in|to|across) (many|several|multiple|all|every)/i],
  ["mass listing", /\bmass[- ]?list|\bbulk\b/i],
  ["volume promises", /thousands of|hundreds of|\bin one click\b|\bone-click\b|\bunlimited\b/i],
  ["invented proof", /trusted by|\bas seen (in|on)\b|[★⭐]|\b\d(\.\d)?\s*(\/|out of)\s*5\b|\b\d[\d,.]*\+?\s+(sellers|shops|users|customers)\b/i],
  ["ranking promises", /rank (higher|#?1|first)\b|\bguarantee(d|s)?\b|top of (the )?search/i],
  ["other products", /\b(vela|marmalead|erank|everbee|alura|printify|printful|shopify|canva)\b/i],
];
const ALLOWED = /production partners?/gi;
const violations = (text: string) => FORBIDDEN.filter(([, pattern]) => pattern.test(withoutApproved(text).replace(ALLOWED, ""))).map(([name]) => name);

test("Listyro is the product's name and leads the title; Etsy is in no heading the page opens with", () => {
  assert.equal(HOME.title, "Listyro: AI listing writer and mockups for print-on-demand sellers");
  for (const heading of headings()) assert.doesNotMatch(heading, /etsy/i, heading);
  assert.match(HOME.description, /Etsy/);
  const page = read("app/(marketing)/page.tsx");
  assert.match(page, /metadataFor\("\/"\)/);
  assert.match(page, /<h1 id="hero-title"[^>]*>\{hero\.heading\}<\/h1>/);
  assert.equal((page.match(/<h1/g) ?? []).length, 1);
  assert.equal(HOME.hero.heading, "Upload 50 designs. Get 50 listings, ready for every shop.");
});

test("the trademark notice is in the footer of the home page, as of every page, and not in the hero", () => {
  const page = read("app/(marketing)/page.tsx");
  assert.doesNotMatch(page, /home-trademark-notice|\{TRADEMARK_NOTICE\}/);
  assert.match(read("components/marketing/Site.tsx"), /data-testid="trademark-notice">\{TRADEMARK_NOTICE\}<\/p>/);
  assert.match(read("app/(marketing)/layout.tsx"), /<SiteFooter \/>/);
});

test("no forbidden word or promise is anywhere on the home page; the owner-approved phrases are exact", () => {
  for (const text of allCopy()) assert.deepEqual(violations(text), [], text);
  // The files' own words: comments and the line that defines the approved phrases are not copy.
  const words = (file: string) => read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/[^\n]*/g, "$1").replace(/^export const APPROVED_PHRASES.*$/m, "");
  for (const file of HOME_SOURCES) assert.deepEqual(violations(words(file)), [], file);
  // The check itself works.
  assert.deepEqual(violations("An official Etsy partner, approved and certified."), ["approved", "certified", "official", "partner"]);
  assert.deepEqual(violations("The same listing in many shops: thousands of listings in one click."), ["the same listing in many shops", "volume promises"]);
  // "official" is allowed only in the two facts the owner approved, and nowhere else.
  assert.equal(HOME.hero.badge, "Built on Etsy's official API");
  assert.equal(HOME.strip[0], "Uses Etsy’s official sign-in");
  assert.equal(APPROVED_PHRASES.length, 2);
  assert.deepEqual(violations("An official partner of Etsy"), ["official", "partner"]);
  // The claims checked against our own documents (site v3 report).
  assert.ok(HOME.strip.includes("Your designs never train AI"));
  const privacy = read("app/(marketing)/privacy/page.tsx").replace(/\s+/g, " ");
  assert.match(privacy, /We ourselves never use your designs or any Etsy data to train any model/);
  assert.match(privacy, /not used to train its models/);
  assert.doesNotMatch(allCopy().join(" "), /Etsy often removes|always free/);
  assert.equal(HOME.safe.contact.href, "/contact", "a person answers: the contact page is how");
});

test("the home page's words are all in the copy module, not typed into the components", () => {
  for (const file of HOME_SOURCES.slice(1)) {
    const source = read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/[^\n]*/g, "$1");
    const typed = [...source.matchAll(/>\s*([A-Za-z][A-Za-z'’,.]*(?:\s+[A-Za-z'’,.]+)+)\s*</g)].map((m) => m[1]);
    assert.deepEqual(typed, [], file);
  }
});

test("every call to action is the configured one: Start free, to the invite form while billing is off", () => {
  assert.equal(BILLING_ON, false);
  assert.deepEqual(PRIMARY_CTA, { label: "Start free", href: "/request-invite" });
  assert.equal(START_HREF, "/request-invite");
  assert.deepEqual(HOME.hero.primary, { label: "Start free with 10 listings", href: "/request-invite" });
  assert.deepEqual(HOME.closing.primary, PRIMARY_CTA);
  const page = read("app/(marketing)/page.tsx");
  assert.match(page, /<a href=\{hero\.primary\.href\}[^>]*data-testid="hero-cta">\{hero\.primary\.label\}<\/a>/);
  assert.match(read("components/marketing/Sections.tsx"), /<a href=\{c\.primary\.href\}[^>]*data-testid="closing-cta">\{c\.primary\.label\}<\/a>/);
  assert.match(read("components/marketing/Site.tsx"), /<a href=\{PRIMARY_CTA\.href\}[^>]*>\{PRIMARY_CTA\.label\}<\/a>/);
  assert.match(read("components/marketing/PlanCards.tsx"), /<a href=\{PRIMARY_CTA\.href\}[^>]*>\{PRIMARY_CTA\.label\}<\/a>/);
  assert.match(read("lib/marketing/cta.ts"), /BILLING_ON \? "\/register" : "\/request-invite"/);
  for (const route of ["request-invite", "pricing", "terms", "privacy", "contact"]) {
    assert.ok(fs.existsSync(path.join(FRONTEND, "app/(marketing)", route, "page.tsx")), route);
  }
  // There is no video yet: the reference's "Watch it in 60 seconds" shows only when one is configured.
  assert.equal(HERO_VIDEO, "");
  assert.match(page, /\{hero\.video\.href \? \(/);
});

test("the trial is worded one way everywhere: 10 free AI listings, only after connecting a shop that never had one", () => {
  assert.equal(TRIAL.listings, 10);
  assert.match(TRIAL.line, /^10 free AI listings once you connect an Etsy shop that has never had a trial\./);
  assert.match(TRIAL.line, /One trial per Etsy shop and per Etsy account, for life\./);
  assert.match(TRIAL.line, /No free listings before a shop is connected\./);
  assert.equal(HOME.closing.body, "Connect your shop and get 10 listings free. If it doesn't save you time, walk away. No card, nothing to cancel.");
  assert.equal(HOME.hero.primary.label, `Start free with ${TRIAL.listings} listings`);
  // The places that state it read it from lib/plans.ts: pricing, Help and both sign-up screens.
  for (const file of ["app/(marketing)/pricing/page.tsx", "app/(marketing)/request-invite/page.tsx", "app/(app)/register/page.tsx"]) {
    assert.match(read(file), /\{TRIAL\.line\}/, file);
  }
  assert.match(read("lib/marketing/help.ts"), /\{ q: "Is there a free trial\?", a: TRIAL_ANSWER \}/);
  // No other trial size is left anywhere.
  for (const dir of ["app", "components", "lib"]) {
    for (const file of fs.readdirSync(path.join(FRONTEND, dir), { recursive: true }).map(String).filter((f) => /\.tsx?$/.test(f) && !f.endsWith(".test.ts"))) {
      assert.doesNotMatch(read(`${dir}/${file}`), /\b25 (free )?AI listings\b/, file);
    }
  }
});

test("the page has the reference's sections, in order", () => {
  const page = read("app/(marketing)/page.tsx");
  const order = ["<HeroDemo />", "<Strip />", "<Grind />", "<How />", "<ShopVersions />", "<Safe />", "<Voices />", "<Pricing />", "<Faq />", "<Closing />"].map((piece) => page.indexOf(piece));
  assert.ok(order.every((at) => at !== -1), String(order));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.deepEqual(HOME.faq.items.map((i) => i.q), ["Is it safe for my shop?", "Will the titles sound like a robot?", "Do I need Photoshop for mockups?", "I run more than one shop.", "What counts as a listing?", "Can I cancel?"]);
  assert.match(HOME.faq.items[4].a, /^In the launch plans/);
  assert.equal(HOME.hero.window.tiles.length, 8);
  assert.equal(HOME.how.colours.length, 6);
  assert.equal(HOME.shops.cards.length, 3);
  assert.equal(HOME.hero.ai.tags.length, 13);
  // The AI card counts its own title, not a typed number.
  assert.equal(HOME.hero.ai.characters, `${HOME.hero.ai.title.length} of 140 characters`);
});

test("testimonials: three, from their file; with none, the promise takes the yellow section", () => {
  assert.equal(TESTIMONIALS.length, 3);
  assert.equal(HOME.voices.testimonials, TESTIMONIALS);
  assert.equal(HOME.voices.promise.line, "Try it on 10 designs. If it doesn't save you time, walk away.");
  assert.deepEqual(HOME.voices.promise.badges, ["No card", "Nothing to cancel", "Keep every draft it made"]);
  // The first is large, the others are cards, with initials.
  const sections = read("components/marketing/Sections.tsx");
  assert.match(sections, /<Initials name=\{first\.name\} size=\{58\} \/>/);
  assert.match(sections, /rest\.map\(\(t\) =>/);
  assert.match(sections, /<Initials name=\{t\.name\} size=\{44\} \/>/);
});

test("the shirts are real photos through our mockup engine, with their licences recorded", () => {
  assert.match(read("lib/marketing/heroImages.ts"), /Written by scripts\/hero-images\.mjs/);
  assert.match(read("components/marketing/HeroDemo.tsx"), /backend\/scripts\/hero_assets\.py/);
  for (const shot of [...HOME.hero.window.tiles, ...HOME.how.colours, ...HOME.shops.cards].map((t) => t.shot)) {
    for (const file of [shot.avif, shot.avif2x, shot.webp, shot.webp2x]) assert.ok(fs.statSync(path.join(FRONTEND, "public", file)).size > 0, file);
  }
});

test("no public page states a maximum or a large number of shops", () => {
  // The number of shops is set per account. The customers' own words are their own (one runs 17 shops) and
  // the plans' shop counts come from lib/plans.ts; neither is typed into the page.
  const counted = /\b(up to|as many as|maximum of|max\.?|at most)\s+(\d+|two|three|four|five|six|seven|eight|nine|ten|twelve|sixteen|twenty)\s+(etsy\s+)?shops\b|\b(\d{1,3}|eight|ten|twelve|sixteen|twenty)\+?\s+(etsy\s+)?shops\b/i;
  for (const text of allCopy()) assert.doesNotMatch(withoutApproved(text), counted, text);
  for (const file of ["app/(marketing)/pricing/page.tsx", "app/(marketing)/terms/page.tsx", "app/(marketing)/privacy/page.tsx", "app/(marketing)/request-invite/page.tsx", "app/(marketing)/contact/page.tsx", "components/marketing/Sections.tsx", "components/marketing/InviteForm.tsx"]) {
    const text = read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1").replace(/\s+/g, " ");
    assert.doesNotMatch(text, counted, file);
  }
  assert.doesNotMatch(read("app/(marketing)/pricing/page.tsx"), /\d+\s+shops?/);
  assert.match(read("components/marketing/PlanCards.tsx"), /shopsFact\(plan\)/);
  assert.match("Up to eight shops", counted);
  assert.doesNotMatch("Several shops; the number is set per account", counted);
});

test('no user-facing text says "beta"', () => {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(path.join(FRONTEND, dir), { withFileTypes: true })) {
      const relative = `${dir}/${entry.name}`;
      if (entry.isDirectory()) walk(relative);
      else if (/\.(tsx?|css)$/.test(entry.name) && !entry.name.endsWith(".test.ts")) files.push(relative);
    }
  };
  for (const dir of ["app", "components", "lib"]) walk(dir);
  assert.ok(files.length > 100);
  const found: string[] = [];
  for (const file of files) {
    const text = read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1");
    for (const line of text.split("\n")) if (/beta/i.test(line)) found.push(`${file}: ${line.trim().slice(0, 90)}`);
  }
  assert.deepEqual(found, []);
  assert.match(read("components/marketing/InviteForm.tsx").replace(/\s+/g, " "), /Listyro is invite-only while we onboard sellers in small groups\. We&apos;ll email you when a spot opens\./);
  assert.ok(read("app/(marketing)/pricing/page.tsx").includes("{PRICING_LINE}"));
  assert.match(read("app/(marketing)/terms/page.tsx"), /title="Fees: none during early access"/);
  assert.doesNotMatch(read("app/layout.tsx"), /beta/i);
});
