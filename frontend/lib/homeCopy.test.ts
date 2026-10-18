// The public home page keeps its rules: what it may never say, where "Etsy" may
// not appear, that its figures are the ones the code uses, and that every call
// to action leads to the invite form. Also: no user-facing text says "beta".
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { HOME, allCopy, headings } from "./homeCopy.ts";
import { RETENTION } from "./legal.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8");

// The files that make the home page. Its header and footer belong to the site's
// frame (components/marketing/Site.tsx): the footer's trademark notice is
// required text and is checked by backend/tests/test_legal_texts.py.
const HOME_SOURCES = ["lib/homeCopy.ts", "app/(marketing)/page.tsx", "components/marketing/Sections.tsx", "components/marketing/HeroDemo.tsx"];

// Never said or implied on the home page. "production partner" is the one
// exception: it is Etsy's own name for a field on a listing, not a claim about us.
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
  ["invented proof", /trusted by|testimonial|\bas seen (in|on)\b|[★⭐]|\b\d(\.\d)?\s*(\/|out of)\s*5\b|\b\d[\d,.]*\+?\s+(sellers|shops|users|customers)\b/i],
  ["ranking promises", /rank (higher|#?1|first)\b(?! \?)|\bguarantee(d|s)?\b|top of (the )?search/i],
  ["other products", /\b(vela|marmalead|erank|everbee|alura|printify|printful|shopify|canva)\b/i],
];
const ALLOWED = /production partners?/gi;

// "Will my listings rank higher?" is a question the page answers with "nobody can promise that".
const QUESTION = "Will my listings rank higher?";

function violations(text: string): string[] {
  const cleaned = text.replace(ALLOWED, "").replace(QUESTION, "");
  return FORBIDDEN.filter(([, pattern]) => pattern.test(cleaned)).map(([name]) => name);
}

test("the page title, the hero heading and every section heading leave Etsy's name out", () => {
  for (const heading of headings()) assert.doesNotMatch(heading, /etsy/i, heading);
  assert.doesNotMatch(HOME.description, /etsy/i);
  assert.doesNotMatch(HOME.ogAlt, /etsy/i);
  // The product's own name leads: it opens the title, and no heading names anyone else.
  assert.ok(HOME.title.startsWith("Listyro"));
  // The page takes its title and its h1 from here, and types neither itself.
  const page = read("app/(marketing)/page.tsx");
  assert.match(page, /title: \{ absolute: HOME\.title \}/);
  assert.match(page, /<h1[^>]*>\s*<span>\{`\$\{hero\.heading\} `\}<\/span>\s*<em[^>]*>\{hero\.headingAccent\}<\/em>\s*<\/h1>/);
  assert.equal((page.match(/<h1/g) ?? []).length, 1);
});

test("the trademark notice is in the hero, in full, not only in the footer", () => {
  const page = read("app/(marketing)/page.tsx");
  assert.match(page, /import \{ TRADEMARK_NOTICE \} from "@\/lib\/legal";/);
  const hero = page.slice(page.indexOf('aria-labelledby="hero-title"'), page.indexOf("<Apart />"));
  assert.match(hero, /data-testid="home-trademark-notice">\{TRADEMARK_NOTICE\}<\/p>/);
  // Readable at a glance: not the footer's muted grey and not smaller than body text.
  assert.match(hero, /text-sm[^"]*text-slate-700/);
});

test("no forbidden word or promise is anywhere on the home page", () => {
  for (const text of allCopy()) assert.deepEqual(violations(text), [], text);
  // Nor in the files that lay the copy out (their own words, comments included).
  for (const file of HOME_SOURCES) assert.deepEqual(violations(read(file)), [], file);
  // The check itself works.
  assert.deepEqual(violations("An official Etsy partner, approved and certified."), ["approved", "certified", "official", "partner"]);
  assert.deepEqual(violations("The same listing in many shops: thousands of listings in one click."), ["the same listing in many shops", "volume promises"]);
  assert.deepEqual(violations("Copies your production partners."), []);
});

test("the home page's words are all in the copy module, not typed into the components", () => {
  for (const file of HOME_SOURCES.slice(1)) {
    const source = read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|\s)\/\/[^\n]*/g, "$1");
    // Text between tags: a run of two or more words that is not an expression.
    const typed = [...source.matchAll(/>\s*([A-Za-z][A-Za-z'’,.]*(?:\s+[A-Za-z'’,.]+)+)\s*</g)].map((m) => m[1]);
    assert.deepEqual(typed, [], file);
  }
});

test("every call to action leads to the invite form", () => {
  assert.deepEqual(HOME.hero.primary, { label: "Request an invite", href: "/request-invite" });
  assert.deepEqual(HOME.hero.secondary, { label: "See how it works", href: "#how-it-works" });
  assert.equal(HOME.how.id, "how-it-works");
  assert.deepEqual(HOME.closing.primary, { label: "Request an invite", href: "/request-invite" });
  const page = read("app/(marketing)/page.tsx");
  assert.match(page, /<Link href=\{hero\.primary\.href\}[^>]*data-testid="hero-cta">\{hero\.primary\.label\}<\/Link>/);
  assert.match(read("components/marketing/Sections.tsx"), /<Link href=\{c\.primary\.href\}[^>]*data-testid="closing-cta">\{c\.primary\.label\}<\/Link>/);
  // The form the buttons lead to exists, as do the pages the home page links to.
  for (const route of ["request-invite", "pricing", "terms", "privacy", "contact"]) {
    assert.ok(fs.existsSync(path.join(FRONTEND, "app/(marketing)", route, "page.tsx")), route);
  }
  assert.match(read("components/marketing/Site.tsx"), /<Link href="\/request-invite" className="btn-primary[^"]*">Request an invite<\/Link>/);
  assert.equal(HOME.hero.note, "Invite-only early access. No payment is taken today.");
});

test("the page has the sections it was asked for, in size", () => {
  assert.ok(HOME.apart.cards.length >= 6 && HOME.apart.cards.length <= 8);
  assert.equal(HOME.how.steps.length, 4);
  assert.equal(HOME.built.items.length, 4);
  for (const item of HOME.built.items) assert.match(item.when, /^(January|February|March|April|May|June|July|August|September|October|November|December) 20\d\d$/);
  assert.ok(HOME.faq.items.length >= 6 && HOME.faq.items.length <= 8);
  assert.deepEqual(HOME.safe.links.map((l) => l.href), ["/privacy", "/terms"]);
  // Every screenshot is a file that exists, and says what it shows.
  for (const card of HOME.apart.cards) {
    if (card.visual.kind !== "screen") continue;
    assert.ok(fs.existsSync(path.join(FRONTEND, "public", card.visual.src)), card.visual.src);
    assert.ok(card.visual.src.endsWith(".webp") && card.visual.alt.length > 20);
  }
});

test("figures quoted on the home page are the ones the Privacy Policy prints", () => {
  const designs = HOME.faq.items.find((i) => i.q === "What happens to my designs?")!.a;
  assert.ok(designs.includes(`deleted ${RETENTION.uploadsAfterDraftsDays} days after`));
  assert.ok(designs.includes(`after ${RETENTION.uploadsOtherwiseDays} days otherwise`));
  const leave = HOME.faq.items.find((i) => i.q === "How do I stop using it?")!.a;
  assert.ok(leave.includes(`within ${RETENTION.accountDeletionDays} days at the latest`));
});

test("no public page states a maximum or a large number of shops", () => {
  // The number of shops is set per account (a default that an admin changes for one account).
  const counted = /\b(up to|as many as|maximum of|max\.?|at most)\s+(\d+|two|three|four|five|six|seven|eight|nine|ten|twelve|sixteen|twenty)\s+(etsy\s+)?shops\b|\b(\d{1,3}|eight|ten|twelve|sixteen|twenty)\+?\s+(etsy\s+)?shops\b/i;
  for (const text of allCopy()) assert.doesNotMatch(text, counted, text);
  for (const file of ["app/(marketing)/pricing/page.tsx", "app/(marketing)/terms/page.tsx", "app/(marketing)/privacy/page.tsx", "app/(marketing)/request-invite/page.tsx", "app/(marketing)/contact/page.tsx", "components/marketing/Sections.tsx", "components/marketing/InviteForm.tsx"]) {
    const text = read(file).replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1").replace(/\s+/g, " ");
    assert.doesNotMatch(text, counted, file);
  }
  // The one exception is the planned plans on the pricing page (1, 2, 4 and 8 shops). Those
  // numbers come from lib/plans.ts: the page itself, checked above, types none.
  const pricing = read("app/(marketing)/pricing/page.tsx");
  assert.doesNotMatch(pricing, /\d+\s+shops?/);
  assert.match(read("components/marketing/PlanCards.tsx"), /shopsFact\(plan\)/);
  // The check itself works.
  assert.match("Up to eight shops", counted);
  assert.match("Connect 16 shops", counted);
  assert.doesNotMatch("Several shops; the number is set per account", counted);
});

test('no user-facing text says "beta"', () => {
  // Every page, component and text module of the site and the app. Comments are
  // not copy and may keep the word; identifiers no longer use it either, so the
  // rest of each file is checked whole.
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
  // What replaced it, where the wording was given.
  assert.match(read("components/marketing/InviteForm.tsx").replace(/\s+/g, " "), /Listyro is invite-only while we onboard sellers in small groups\. We&apos;ll email you when a spot opens\./);
  assert.ok(read("app/(marketing)/pricing/page.tsx").includes("{PRICING_LINE}"));
  assert.match(read("app/(marketing)/terms/page.tsx"), /title="Fees: none during early access"/);
  // Meta tags: the document's own description and every page's.
  assert.doesNotMatch(read("app/layout.tsx"), /beta/i);
});
