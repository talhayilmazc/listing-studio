// The public site keeps its promises: one registry for every page, the trademark rule, no invented
// claims, links that lead somewhere, valid structured data, and a cache policy that is private unless
// a page is listed. e2e/marketing.spec.ts and e2e/headers.spec.ts check the same on the real server.
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";
import { COMPARES, ALTERNATIVES, COMPARE_PAGE, LISTYRO, NOT_STATED } from "./marketing/compare.ts";
import { jsonLdNames, refuseEtsy } from "./marketing/brandRule.ts";
import { allText, linksIn, longDate, type Block } from "./marketing/content.ts";
import { FEATURES, FEATURES_PAGE } from "./marketing/features.ts";
import { CALENDAR_AS_OF, CALENDAR_DATES, FOR_THE_SELLER_TO_READ, GUIDES, GUIDES_PAGE, READ, guideText, liveBy } from "./marketing/guides.ts";
import { FREE_TOOLS, HELP } from "./marketing/help.ts";
import { article, breadcrumbs, faqPage, organization, softwareApplication } from "./marketing/jsonld.ts";
import { PAGES, SITE_URL, cardSlug, metadataFor, pageAt, trail } from "./marketing/pages.ts";
import { CACHE_FILE, CACHE_PAGE, CACHE_PRIVATE, COMPARE_SLUGS, FEATURE_SLUGS, GUIDE_SLUGS, MARKETING_PAGES, PRIVATE_WHEN_PRESENT, PUBLIC_FILES, SESSION_COOKIE, cacheRules } from "./marketing/routes.mjs";
import { TESTIMONIALS } from "./marketing/testimonials.ts";
import { DEMO_VIDEO } from "./marketing/video.ts";
import { HOME, allCopy } from "./homeCopy.ts";
import { TRADEMARK_NOTICE } from "./legal.ts";
import { PLANS, PRICING_HEADING, priceFact } from "./plans.ts";

const { etsyHeadings, etsyHeadingsIn } = createRequire(import.meta.url)("../scripts/heading-rule.cjs");

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8");
const MARKETING_DIR = "app/(marketing)";

function pageFiles(dir: string): string[] {
  return fs.readdirSync(path.join(FRONTEND, dir), { withFileTypes: true }).flatMap((entry) => {
    const relative = `${dir}/${entry.name}`;
    if (entry.isDirectory()) return pageFiles(relative);
    return entry.name === "page.tsx" ? [relative] : [];
  });
}

const OUR_COPY = [
  ...allCopy(), ...allText(FEATURES), ...allText(FEATURES_PAGE), ...allText(HELP), ...allText(FREE_TOOLS), ...allText(GUIDES_PAGE),
  ...GUIDES.flatMap(guideText), ...allText(COMPARE_PAGE), ...allText(LISTYRO),
];
const ALL_LINK_TEXT = [...OUR_COPY, ...allText(COMPARES), ...allText(ALTERNATIVES)];

test("one list of pages: routes, registry and files agree", () => {
  assert.deepEqual(PAGES.map((p) => p.path).sort(), [...MARKETING_PAGES].sort());
  assert.deepEqual(FEATURES.map((f) => f.slug), FEATURE_SLUGS);
  assert.deepEqual(GUIDES.map((g) => g.slug), GUIDE_SLUGS);
  assert.deepEqual([...COMPARES.map((c) => c.slug), ALTERNATIVES.slug], COMPARE_SLUGS);
  const onDisk = pageFiles(MARKETING_DIR).map((file) => file.slice(MARKETING_DIR.length).replace(/\/page\.tsx$/, "") || "/");
  const expected = new Set(MARKETING_PAGES.map((p) => p.replace(/^\/features\/.+/, "/features/[slug]").replace(/^\/guides\/.+/, "/guides/[slug]").replace(/^\/compare\/.+/, "/compare/[slug]")));
  assert.deepEqual(onDisk.sort(), [...expected].sort());
});

test("the structure that was asked for", () => {
  assert.deepEqual(FEATURE_SLUGS, ["ai-listing-writer", "mockup-generator", "multi-shop-publishing", "scheduling", "profiles", "profit-report"]);
  assert.equal(GUIDES.length, 7);
  assert.equal(COMPARES.length, 3);
  const nav = read("components/marketing/Site.tsx");
  const top = nav.slice(nav.indexOf("export const TOP_NAV"), nav.indexOf("export function Wordmark"));
  assert.ok((top.match(/label:/g) ?? []).length <= 5, "at most five top navigation items");
});

test("every page has its own title, description, canonical address and share card", () => {
  assert.equal(new Set(PAGES.map((p) => p.title)).size, PAGES.length);
  assert.equal(new Set(PAGES.map((p) => p.description)).size, PAGES.length);
  for (const page of PAGES) {
    assert.ok(page.title.includes("Listyro"), page.title);
    assert.ok(page.title.length <= 70, `${page.title} (${page.title.length})`);
    assert.ok(page.description.length >= 50 && page.description.length <= 200, `${page.path}: ${page.description.length}`);
    const meta = metadataFor(page.path);
    assert.deepEqual(meta.alternates, { canonical: page.path });
    assert.deepEqual(meta.title, { absolute: page.title });
    const image = (meta.openGraph!.images as { url: string }[])[0];
    assert.equal(image.url, `/og/${cardSlug(page.path)}.png`);
  }
  assert.equal(new Set(PAGES.map((p) => cardSlug(p.path))).size, PAGES.length);
  // Each page takes its metadata from the registry and types none.
  for (const file of pageFiles(MARKETING_DIR)) assert.match(read(file), /metadataFor\(/, file);
});

test("the keywords the site is written for are on the pages meant to carry them", () => {
  const has = (path: string, words: RegExp) => assert.match(`${pageAt(path).title} ${pageAt(path).description}`, words, path);
  has("/", /listing assistant/i);
  has("/", /print-on-demand/i);
  has("/features/ai-listing-writer", /listing generator for Etsy sellers/i);
  has("/features/mockup-generator", /mockup generator for Etsy/i);
  has("/guides/list-print-on-demand-shirts-in-bulk", /bulk/i);
  has("/free-tools", /title checker/i);
  has("/free-tools", /fee calculator/i);
});

test("sitemap and robots: the site's pages only; the app and the API are shut out", () => {
  const sitemap = read("app/sitemap.ts");
  assert.match(sitemap, /PAGES\.map/);
  assert.deepEqual(PAGES.filter((p) => /dashboard|batches|login|api/.test(p.path)), []);
  const robots = read("app/robots.ts");
  assert.match(robots, /"\/api\/"/);
  const folders = fs.readdirSync(path.join(FRONTEND, "app/(app)"), { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => `/${e.name}`);
  const listed = [...robots.slice(robots.indexOf("APP_PATHS = ["), robots.indexOf("];")).matchAll(/"(\/[a-z-]+)"/g)].map((m) => m[1]);
  assert.deepEqual(listed.sort(), folders.sort());
  for (const page of PAGES) assert.ok(!listed.some((p) => page.path === p || page.path.startsWith(`${p}/`)), page.path);
  assert.ok(SITE_URL.startsWith("https://listyro.com") || process.env.NEXT_PUBLIC_SITE_URL);
});

test("trademark rule: Listyro is the only name; no logo, no orange", () => {
  assert.doesNotMatch(new URL(SITE_URL).hostname, /etsy/i);
  assert.doesNotMatch(read("components/marketing/Site.tsx").slice(read("components/marketing/Site.tsx").indexOf("export function Wordmark"), read("components/marketing/Site.tsx").indexOf("export function SiteHeader")), /etsy/i);
  // Nothing says or implies that Etsy runs, endorses or works with us.
  const implies = /\b(official|certified|authori[sz]ed by etsy|sponsored|recommended by|in partnership|partnered|etsy('s)? partner|partner of etsy|etsy[- ]approved|approved by etsy|endorsed by etsy)\b/i;
  const denied = /(not (made, )?(endorsed|certified)[^.]*|production partners?|your production partner|its print partners?|print partners?)/gi;
  for (const text of [...OUR_COPY, ...PAGES.map((p) => p.title)]) assert.doesNotMatch(text.replace(denied, ""), implies, text);
  // Every title names Listyro as its own.
  for (const page of PAGES) assert.match(page.title, /Listyro/);
  // No Etsy orange in anything the public site draws, and no Etsy logo file.
  const sources = [...pageFiles(MARKETING_DIR), "components/marketing/Site.tsx", "components/marketing/Page.tsx", "components/marketing/Sections.tsx", "app/og/[slug]/route.tsx", "app/globals.css"];
  for (const file of sources) assert.doesNotMatch(read(file), /#f1641e|#f56400|#d5641c/i, file);
  assert.deepEqual(fs.readdirSync(path.join(FRONTEND, "public"), { recursive: true }).map(String).filter((f) => /etsy/i.test(f)), []);
});

test("no invented numbers, ratings or promises anywhere on the site", () => {
  const forbidden: [string, RegExp][] = [
    ["beta", /\bbeta\b/i],
    ["invented proof", /trusted by|\bas seen (in|on)\b|[★⭐]|\b\d(\.\d)?\s*(\/|out of)\s*5\b|\b\d[\d,.]*\+?\s+(sellers|users|customers)\b|\bjoin (over )?\d/i],
    ["ranking or sales promises", /rank (higher|#?1|first)\b(?! \?)|\bguarantee(d|s)?\b|top of (the )?search|more (views|sales|traffic)|boost your|increase your sales|sell more\b/i],
    ["volume promises", /thousands of|\bin one click\b|\bone-click\b|\bunlimited\b/i],
    ["compliance promises", /100\s*%\s*compliant|fully compliant|guaranteed compliant/i],
  ];
  const question = "Will my listings rank higher?";
  for (const text of OUR_COPY) {
    const cleaned = text.replace(question, "");
    for (const [name, pattern] of forbidden) assert.doesNotMatch(cleaned, pattern, `${name}: ${text}`);
  }
});

test('"bulk" and "the same listing in many shops": the wording rule', () => {
  // The site says "several shops, each with its own". One guide's title uses the seller's own search words.
  const bulkGuide = GUIDES.find((g) => g.slug === "list-print-on-demand-shirts-in-bulk")!;
  for (const text of [...allCopy(), ...allText(FEATURES), ...allText(HELP)]) {
    // The one guide may be named where it is linked to.
    const cleaned = text.replace(bulkGuide.h1, "").replace(`/guides/${bulkGuide.slug}`, "");
    assert.doesNotMatch(cleaned, /\bmass[- ]?list|\bbulk\b/i, text);
  }
  for (const text of OUR_COPY) assert.doesNotMatch(text, /same (listing|design)s? (in|to|across) (many|several|multiple|all|every)/i, text);
  const uses = GUIDES.filter((g) => guideText(g).some((t) => /\bbulk\b/i.test(t))).map((g) => g.slug);
  assert.deepEqual(uses, ["list-print-on-demand-shirts-in-bulk"]);
});

test("no page states a maximum or a large number of shops", () => {
  const counted = /\b(up to|as many as|maximum of|max\.?|at most)\s+(\d+|two|three|four|five|six|seven|eight|nine|ten|twelve|sixteen|twenty)\s+(etsy\s+)?shops\b|\b(\d{1,3}|eight|ten|twelve|sixteen|twenty)\+?\s+(etsy\s+)?shops\b/i;
  for (const text of OUR_COPY) assert.doesNotMatch(text, counted, text);
  assert.equal(LISTYRO.shops, "Several shops; the number is set per account");
});

test("every link goes somewhere real", () => {
  const known = new Set(MARKETING_PAGES);
  const anchors = new Set(["/features/ai-listing-writer#trademark-warning", "/features/multi-shop-publishing#per-shop-text", "/privacy#ai"]);
  const allowed = /^https:\/\/(www\.etsy\.com|help\.etsy\.com|developers\.etsy\.com|listadum\.com|www\.listadum\.com|mydesigns\.io|getvela\.com|apps\.shopify\.com)\//;
  const hrefs = ALL_LINK_TEXT.flatMap((text) => linksIn(text).map((l) => l.href));
  assert.ok(hrefs.length > 30);
  for (const href of hrefs) {
    if (/^https?:/.test(href)) assert.match(href, allowed, href);
    else if (href.includes("#")) assert.ok(anchors.has(href), href);
    else assert.ok(known.has(href), href);
  }
  assert.ok(FEATURES.find((f) => f.slug === "ai-listing-writer")!.body.some((b) => "id" in b && b.id === "trademark-warning"));
  assert.match(read("app/(marketing)/privacy/page.tsx"), /id: "ai"/);
  for (const f of FEATURES) assert.ok(known.has(f.guide.href), f.guide.href);
  for (const tool of FREE_TOOLS.planned) assert.ok(known.has(tool.guide.href), tool.guide.href);
  for (const option of ALTERNATIVES.options) assert.ok(known.has(option.href) || allowed.test(option.href), option.href);
});

test("internal links are plain: no router and no image optimizer on the public site", () => {
  for (const file of [...pageFiles(MARKETING_DIR), "components/marketing/Site.tsx", "components/marketing/Page.tsx", "components/marketing/Sections.tsx", "components/marketing/PlanCards.tsx", "components/marketing/InviteForm.tsx", "app/not-found.tsx"]) {
    assert.doesNotMatch(read(file), /next\/link|next\/image/, file);
  }
});

test("each guide: dated, sourced, linked to its feature, and promises nothing", () => {
  for (const guide of GUIDES) {
    assert.match(guide.published, /^\d{4}-\d{2}-\d{2}$/);
    assert.match(guide.checked, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(guide.sources.length >= 2, guide.slug);
    assert.ok(guide.sources.some((s) => /etsy\.com/.test(s.url)), guide.slug);
    assert.ok(MARKETING_PAGES.includes(guide.feature.href.split("#")[0]), guide.feature.href);
    assert.ok(guideText(guide).join(" ").split(/\s+/).length >= 200, `${guide.slug} is too thin`);
  }
  // The trademark guide leads to the warning.
  const ip = GUIDES.find((g) => g.slug === "trademarks-and-ip-on-etsy")!;
  assert.equal(ip.feature.href, "/features/ai-listing-writer#trademark-warning");
  assert.match(guideText(ip).join(" "), /not legal advice/);
  assert.equal(longDate("2026-11-01"), "1 November 2026");
});

test("the calendar's dates are the real ones", () => {
  const day = (iso: string) => new Date(`${iso}T00:00:00Z`).getUTCDay();
  const dates = new Map(CALENDAR_DATES.map(([name, iso]) => [`${name} ${iso.slice(0, 4)}`, iso]));
  const nthWeekday = (year: number, month: number, weekday: number, n: number) => {
    const first = new Date(Date.UTC(year, month, 1)).getUTCDay();
    return new Date(Date.UTC(year, month, 1 + ((weekday - first + 7) % 7) + 7 * (n - 1))).toISOString().slice(0, 10);
  };
  assert.equal(dates.get("Mother's Day (US) 2027"), nthWeekday(2027, 4, 0, 2));
  assert.equal(dates.get("Father's Day (US) 2027"), nthWeekday(2027, 5, 0, 3));
  assert.equal(dates.get("Thanksgiving (US) 2027"), nthWeekday(2027, 10, 4, 4));
  const friday = new Date(Date.parse(nthWeekday(2026, 10, 4, 4) + "T00:00:00Z") + 86_400_000).toISOString().slice(0, 10);
  assert.equal(dates.get("Black Friday 2026"), friday);
  assert.equal(day(dates.get("Easter 2027")!), 0);
  assert.equal(liveBy("2026-12-25"), "30 Oct 2026");
});

test("compare pages: dated, sourced, fair, and plain about what the others do that we do not", () => {
  const unfair = /\b(worse|worst|terrible|awful|bad|poor|scam|avoid|inferior|clunky|overpriced|outdated|buggy|confusing|cheap|rip[- ]?off|lacks|fails|only us|unlike)\b/i;
  for (const c of COMPARES) {
    assert.match(c.checked, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(c.sources.length >= 2, c.slug);
    assert.ok(c.theirs.length >= 3, `${c.slug}: says what they do that we do not`);
    assert.ok(c.rows.length >= 8, c.slug);
    for (const text of allText([c.lead, c.description, c.rows, c.theirs, c.ours, c.chooseThem, c.chooseUs])) assert.doesNotMatch(text, unfair, text);
    assert.ok(c.h1.startsWith("Listyro vs "), c.h1);
    // Anything their pages did not state says so, in the same words, rather than guessing.
    for (const row of c.rows) assert.ok(row.them.length > 0 && row.listyro.length > 0, row.label);
  }
  assert.ok(COMPARES.some((c) => c.rows.some((r) => r.them === NOT_STATED)));
  for (const text of allText(ALTERNATIVES)) assert.doesNotMatch(text, unfair, text);
  // Our own planned prices are the constants', not typed.
  assert.ok(LISTYRO.price.includes(priceFact(PLANS[0])) && LISTYRO.price.includes(priceFact(PLANS[PLANS.length - 1])));
  assert.doesNotMatch(read("lib/marketing/compare.ts").slice(read("lib/marketing/compare.ts").indexOf("export const LISTYRO"), read("lib/marketing/compare.ts").indexOf("export type Compare")), /\$\d/);
  // The page shows the last-checked date beside the sources.
  assert.match(read("components/marketing/Page.tsx"), /Last checked \$\{longDate\(checked\)\}/);
  assert.match(read("app/(marketing)/compare/[slug]/page.tsx"), /<Sources sources=\{c\.sources\} checked=\{c\.checked\} \/>/);
  assert.match(read("app/(marketing)/compare/[slug]/page.tsx"), /What \$\{c\.them\} does that Listyro does not/);
});

test("structured data: valid, and only what the pages say", () => {
  const blocks = [organization(), softwareApplication(), faqPage([...HOME.faq.items]), breadcrumbs("/features/profiles"), article(GUIDES[0])];
  for (const block of blocks) {
    const round = JSON.parse(JSON.stringify(block));
    assert.equal(round["@context"], "https://schema.org");
    assert.ok(typeof round["@type"] === "string");
    assert.doesNotMatch(JSON.stringify(block), /aggregateRating|ratingValue|reviewCount|"review"/);
  }
  const app = softwareApplication();
  assert.deepEqual(app.offers.map((o) => [o.name, o.price, o.priceCurrency]), PLANS.map((p) => [p.name, String(p.price), "USD"]));
  for (const offer of app.offers) assert.match(offer.description, /No payment is taken today/);
  const faq = faqPage(HELP.groups.flatMap((g) => g.items));
  for (const q of faq.mainEntity) assert.doesNotMatch(q.acceptedAnswer.text, /\]\(/, "no link markup in an answer");
  const crumbs = breadcrumbs("/features/profiles");
  assert.deepEqual(crumbs.itemListElement.map((c) => c.position), [1, 2, 3]);
  assert.deepEqual(trail("/features/profiles").map((p) => p.path), ["/", "/features", "/features/profiles"]);
  const a = article(GUIDES[0]);
  assert.equal(a.datePublished, GUIDES[0].published);
  assert.equal(a.dateModified, GUIDES[0].checked);
  // Where each is used.
  assert.match(read("app/(marketing)/page.tsx"), /organization\(\)[\s\S]*softwareApplication\(\)[\s\S]*faqPage/);
  assert.match(read("app/(marketing)/guides/[slug]/page.tsx"), /article\(guide\)/);
  assert.match(read("app/(marketing)/help/page.tsx"), /faqPage/);
  // A text cannot end the script element.
  assert.match(read("components/marketing/Page.tsx"), /replace\(\/<\/g, "\\\\u003c"\)/);
});

test("Etsy is in no title, share title, JSON-LD name or headline, or h1", () => {
  const h1s = [
    `${HOME.hero.heading} ${HOME.hero.headingAccent}`, PRICING_HEADING, FEATURES_PAGE.h1, GUIDES_PAGE.h1, COMPARE_PAGE.h1, HELP.h1, FREE_TOOLS.h1, ALTERNATIVES.h1,
    ...FEATURES.map((f) => f.h1), ...GUIDES.map((g) => g.h1), ...COMPARES.map((c) => c.h1),
  ];
  for (const h1 of h1s) assert.doesNotMatch(h1, /etsy/i, h1);
  for (const page of PAGES) {
    const meta = metadataFor(page.path);
    for (const text of [page.title, page.card, page.crumb, (meta.openGraph as { title: string }).title, (meta.twitter as { title: string }).title]) {
      assert.doesNotMatch(text, /etsy/i, `${page.path}: ${text}`);
    }
  }
  // Every block of structured data the site can print, at any depth.
  const blocks = [
    organization(), softwareApplication(), faqPage([...HOME.faq.items]), faqPage(HELP.groups.flatMap((g) => g.items)),
    ...PAGES.map((p) => breadcrumbs(p.path)), ...GUIDES.map(article),
  ];
  const names = blocks.flatMap(jsonLdNames);
  assert.ok(names.length > 60);
  for (const name of names) assert.doesNotMatch(name, /etsy/i, name);
  // Headings typed into a page, in the site and the app alike.
  assert.deepEqual(etsyHeadings(), []);
});

test("a title, share heading, JSON-LD name or h1 naming Etsy stops the production build", () => {
  assert.throws(() => refuseEtsy("Etsy listing tools | Listyro", "a title"), /may not appear in a title/);
  assert.throws(() => refuseEtsy("etsy tools", "a title"), /may not appear/);
  assert.equal(refuseEtsy("Listing tools | Listyro", "a title"), "Listing tools | Listyro");
  assert.deepEqual(jsonLdNames({ name: "a", mainEntity: [{ name: "b", acceptedAnswer: { text: "Etsy may be here" } }], headline: "c" }), ["a", "b", "c"]);
  // Headings typed into a source file stop the build before it starts.
  assert.match(JSON.parse(read("package.json")).scripts.prebuild, /node scripts\/heading-rule\.cjs/);
  assert.match(read("../docker/frontend.prod.Dockerfile"), /RUN npm run build/);
  assert.deepEqual(etsyHeadingsIn(`<h1 className="x">Your Etsy shops</h1>`), ["Your Etsy shops"]);
  assert.deepEqual(etsyHeadingsIn(`<h1>\n  Etsy shops\n</h1>`), ["\n  Etsy shops\n"]);
  assert.deepEqual(etsyHeadingsIn(`<AuthShell title="Connect Etsy">`), ["Connect Etsy"]);
  assert.deepEqual(etsyHeadingsIn(`export const metadata: Metadata = { title: "Etsy page" }`), ["Etsy page"]);
  assert.deepEqual(etsyHeadingsIn(`title: { absolute: "Etsy page | Listyro" }`), ["Etsy page | Listyro"]);
  assert.deepEqual(etsyHeadingsIn(`<h1>Shops</h1><Section title="The Etsy data">`), []);
  // These run while the pages are rendered: `next build` renders every one of them.
  assert.match(read("lib/marketing/pages.ts"), /refuseEtsy\(page\.title,/);
  assert.match(read("lib/marketing/pages.ts"), /refuseEtsy\(page\.card,/);
  assert.match(read("components/marketing/Page.tsx"), /for \(const name of jsonLdNames\(data\)\) refuseEtsy\(/);
  assert.match(read("components/marketing/Page.tsx"), /refuseEtsy\(h1,/);
  // Every long public page has its heading checked by going through PageHead, or is in the list above.
  for (const file of ["guides/[slug]", "features/[slug]", "compare/[slug]", "help", "free-tools", "features", "guides", "compare"]) {
    assert.match(read(`${MARKETING_DIR}/${file}/page.tsx`), /<PageHead\b/, file);
  }
});

test("Etsy's name stays where it describes: in the descriptions, the copy and the notice", () => {
  assert.match(HOME.description, /Etsy listings/);
  assert.match(HOME.hero.eyebrow, /for Etsy sellers/);
  assert.match(pageAt("/features/ai-listing-writer").description, /for Etsy sellers/);
  assert.match(TRADEMARK_NOTICE, /not endorsed or certified by Etsy/);
});

const READ_URLS = new Set(Object.values(READ).map((s) => s.url));
const UNREAD_URLS = new Set(Object.values(FOR_THE_SELLER_TO_READ).map((s) => s.url));
const SAYS_ABOUT_ETSY = /\bEtsy('s)?\b[^.:]*\b(says?|requires?|asks?|allows?|refuses?|removes?|prohibits?|recommends?|suggests?|welcomes?|gives?|keeps|must|bans?)\b/i;
const OURS = /\b(our own|our advice|our view|our reading|our suggestion|not etsy's|not an etsy rule|nobody outside etsy)\b/i;

/** The text of each block, with the paragraph that leads into it when the block is a list. */
function blockTexts(blocks: Block[]): { text: string; intro: string }[] {
  const out: { text: string; intro: string }[] = [];
  let lastParagraph = "";
  for (const block of blocks) {
    if ("h2" in block) lastParagraph = "";
    else if ("p" in block) { out.push({ text: block.p, intro: "" }); lastParagraph = block.p; }
    else if ("note" in block) out.push({ text: block.note, intro: "" });
    else if ("ul" in block) out.push(...block.ul.map((text) => ({ text, intro: lastParagraph })));
    else if ("ol" in block) out.push(...block.ol.map((text) => ({ text, intro: lastParagraph })));
  }
  return out;
}

test("every claim about Etsy's rules links to a page that states it, or says it is our own", () => {
  const places: [string, { text: string; intro: string }[]][] = [
    ...GUIDES.map((g) => [g.slug, [{ text: g.lead, intro: "" }, ...blockTexts(g.body)]] as [string, { text: string; intro: string }[]]),
    ["help", HELP.groups.flatMap((g) => g.items.map((i) => ({ text: i.a, intro: "" })))],
  ];
  const leads = new Set(GUIDES.map((g) => g.lead));
  for (const [place, texts] of places) {
    for (const { text, intro } of texts) {
      if (!SAYS_ABOUT_ETSY.test(text) || OURS.test(text) || OURS.test(intro) || leads.has(text)) continue;
      const linked = [...linksIn(text), ...linksIn(intro)].some((l) => READ_URLS.has(l.href));
      assert.ok(linked, `${place}: "${text}" says what Etsy says, with no link to a page that states it`);
    }
  }
  // The check itself works.
  assert.ok(SAYS_ABOUT_ETSY.test("Etsy requires a thing"));
  assert.ok(SAYS_ABOUT_ETSY.test("Etsy's policy says a thing"));
});

test("sources are pages that were read, every one is used, and an unread page supports nothing", () => {
  assert.equal([...READ_URLS].filter((url) => UNREAD_URLS.has(url)).length, 0);
  for (const guide of GUIDES) {
    const body = allText(guide.body).concat(guide.lead, guide.description);
    const links = body.flatMap(linksIn).map((l) => l.href).filter((href) => /^https?:/.test(href));
    for (const href of links) assert.ok(READ_URLS.has(href), `${guide.slug}: ${href} is not a page we read`);
    for (const source of guide.sources) {
      assert.ok(READ_URLS.has(source.url), `${guide.slug}: source ${source.url}`);
      assert.ok(links.includes(source.url), `${guide.slug}: ${source.label} is listed but nothing cites it`);
    }
    for (const href of links) assert.ok(guide.sources.some((s) => s.url === href), `${guide.slug}: ${href} is cited but not listed`);
    for (const page of guide.alsoRead) {
      assert.ok(!guide.sources.some((s) => s.url === page.url), `${guide.slug}: ${page.label} is on both lists`);
    }
    assert.ok(guide.alsoRead.length >= 1, guide.slug);
  }
  // Features and Help cite only read pages too.
  for (const text of [...allText(FEATURES), ...allText(HELP)]) {
    for (const { href } of linksIn(text)) if (/etsy\.com/.test(href)) assert.ok(READ_URLS.has(href), href);
  }
  const page = read("app/(marketing)/guides/[slug]/page.tsx");
  assert.match(page, /<AlsoRead pages=\{guide\.alsoRead\} \/>/);
  assert.match(read("components/marketing/Page.tsx"), /Nothing above relies on them/);
});

test("a long quotation in a guide is on record, word for word, with the page it came from", () => {
  const flat = (text: string) => text.replace(/[’‘]/g, "'").replace(/[“”]/g, '"').replace(/\\"/g, '"').replace(/\s+/g, " ");
  const ledger = flat(fs.readFileSync(path.join(FRONTEND, "..", "docs", "guide-sources.md"), "utf8"));
  const quotations = (text: string) => {
    // A quotation may hold one nested quotation of its own.
    const found = [...text.matchAll(/"((?:[^"]|"[^"]*")*?)"(?=[\s.,;:)]|$)/g)].map((m) => m[1]);
    return found.filter((q) => q.split(/\s+/).length >= 7);
  };
  let checked = 0;
  for (const guide of GUIDES) {
    for (const block of guide.body) {
      if ("table" in block) continue;
      for (const text of allText(block)) {
        for (const quote of quotations(text)) {
          checked += 1;
          assert.ok(ledger.includes(flat(quote)), `${guide.slug}: not in docs/guide-sources.md: ${quote}`);
        }
      }
    }
  }
  assert.ok(checked >= 25, `only ${checked} quotations found`);
  for (const source of Object.values(READ)) assert.ok(ledger.includes(source.url) || source.url.includes("developers.etsy.com") && ledger.includes("developers.etsy.com"), source.url);
  for (const source of Object.values(FOR_THE_SELLER_TO_READ)) assert.ok(ledger.includes(source.url), source.url);
});

test("what rested only on a search summary or a blog is gone from the site", () => {
  const removed: [string, RegExp][] = [
    ["the date of the title guidance", /\bApril 2026\b/],
    ["\"holistic\" search", /holistic/i],
    ["the search matching list", /first photo and reviews/i],
    ["root words", /root words?/i],
    ["media coverage lead time", /media (coverage|feature)|4 to 6 months/i],
    ["the old community announcement", /community announcement|distinctly different/i],
    ["closing a seller's other shops", /all other shops|closes? the seller's other shops/i],
    ["Designed by and Sourced by examples", /"Designed by"|"Sourced by"/],
    ["liability wording not on a page we read", /liable for damages/i],
    ["counter notices limited to the US", /US copyright report/i],
    ["the handbook's stock photo wording", /not a rendering or stock photo|stock photos or artistic renderings/i],
    ["the unattributed removal claim", /Etsy often removes/i],
    ["a rule said to be Etsy's", /Etsy asks you to|Etsy's title guidance|Etsy says to do/i],
  ];
  const everywhere = [...GUIDES.flatMap(guideText), ...allText(HELP), ...allText(FEATURES), ...allText(FREE_TOOLS), ...allCopy()];
  for (const [name, pattern] of removed) {
    for (const text of everywhere) assert.doesNotMatch(text, pattern, `${name}: ${text}`);
  }
  // The photo rule stayed because Etsy's own page states it, and the sentence is Etsy's.
  const mockups = GUIDES.find((g) => g.slug === "mockups-buyers-trust")!;
  assert.ok(guideText(mockups).some((t) => t.includes("\"Etsy requires at least one unedited photo for most items.\"")));
  assert.ok(mockups.sources.includes(READ.imageHelp));
});

test("the multi-shop guide quotes the Seller Policy exactly and claims no more", () => {
  const guide = GUIDES.find((g) => g.slug === "running-more-than-one-etsy-shop")!;
  const body = allText(guide.body);
  const clause = body.find((t) => t.includes("section 1c item 7"))!;
  assert.ok(clause.includes(`"for the purpose of shilling, manipulating search or circumventing Etsy's policies"`), clause);
  assert.ok(clause.includes("prohibits creating duplicate shops or other actions"), clause);
  assert.ok(clause.includes("5 October 2026"), clause);
  assert.ok(clause.includes("(https://www.etsy.com/legal/sellers)"), clause);
  assert.ok(guide.sources.some((s) => s.url === "https://www.etsy.com/legal/sellers" && /5 October 2026/.test(s.label)));
  // It says what the policy does not say.
  const not = guide.body.findIndex((b) => "h2" in b && b.h2 === "What that wording does not say");
  assert.ok(not > 0);
  const list = guide.body[not + 2] as { ul: string[] };
  assert.equal(list.ul.length, 4);
  assert.match(list.ul[0], /does not define a duplicate shop/);
  // The advice: each shop's own text, no pushing one item in search, and the feature that does it.
  const text = body.join(" ");
  assert.match(text, /Give each shop its own text/);
  assert.match(text, /Do not use several shops to push the same item in search/);
  assert.ok(text.includes("(/features/multi-shop-publishing#per-shop-text)"));
  assert.ok(FEATURES.find((f) => f.slug === "multi-shop-publishing")!.body.some((b) => "id" in b && b.id === "per-shop-text"));
  // No wider claim than the policy's own: nothing says the same designs in two shops are banned or fine.
  assert.doesNotMatch(text, /same (types|type) of items|is a duplicate if|is allowed if/i);
  // The feature page quotes the same words.
  assert.ok(allText(FEATURES).some((t) => t.includes(`"for the purpose of shilling, manipulating search or circumventing Etsy's policies"`)));
});

test("the holiday guide leads with the dates ahead, and passed dates are one short note", () => {
  const guide = GUIDES.find((g) => g.slug === "holiday-listing-calendar-pod")!;
  assert.equal(CALENDAR_AS_OF, guide.checked);
  assert.equal(CALENDAR_AS_OF, "2026-11-01");
  assert.deepEqual(guide.body[0], { h2: "The dates ahead" });
  const { rows, head } = (guide.body[1] as { table: { rows: string[][]; head: string[] } }).table;
  assert.deepEqual(head, ["Event", "Date", "Live by"]);
  const when = (day: string) => Date.parse(`${day} UTC`);
  const asOf = Date.parse(`${CALENDAR_AS_OF}T00:00:00Z`);
  for (const [name, date] of rows) assert.ok(when(date) >= asOf, `${name} ${date} has passed`);
  for (let i = 1; i < rows.length; i++) assert.ok(when(rows[i][1]) >= when(rows[i - 1][1]), "in date order");
  assert.deepEqual(rows[0], ["Thanksgiving (US)", "26 Nov 2026", "Passed: list now"]);
  assert.deepEqual(rows[1], ["Black Friday", "27 Nov 2026", "Passed: list now"]);
  assert.deepEqual(rows[2], ["Christmas", "25 Dec 2026", "Passed: list now"]);
  assert.deepEqual(rows[3], ["Valentine's Day", "14 Feb 2027", "20 Dec 2026"]);
  assert.equal(rows.at(-1)![1], "25 Dec 2027");
  // A "live by" shown as a date is still ahead.
  for (const [, , live] of rows) if (live !== "Passed: list now") assert.ok(when(live) >= asOf, live);
  assert.ok(!rows.some(([name, date]) => name === "Halloween" && date.endsWith("2026")));
  const note = (guide.body[3] as { note: string }).note;
  assert.ok(note.startsWith("This season, already passed: Halloween (31 Oct 2026)"), note);
  for (const mark of ["1 Oct 2026", "2 Oct 2026", "30 Oct 2026"]) assert.ok(note.includes(mark), mark);
  assert.ok(note.length < 180, `${note.length}`);
  assert.match(guide.lead, /November 2026 through 2027/);
});

test("the worked example says the right number of words", () => {
  const guide = GUIDES.find((g) => g.slug === "etsy-title-rules-2026")!;
  const table = guide.body.find((b) => "table" in b) as { table: { rows: string[][] } };
  for (const [, title, why] of table.table.rows) assert.equal(title.split(/\s+/).length, Number(why.match(/^(\d+) words/)![1]), title);
});

test("testimonials come only from their file, and an empty file hides the section", () => {
  assert.deepEqual(TESTIMONIALS, []);
  const sections = read("components/marketing/Sections.tsx");
  assert.match(sections, /if \(TESTIMONIALS\.length === 0\) return null;/);
  assert.doesNotMatch(read("lib/homeCopy.ts"), /testimonial/i);
});

test("the home page has what was asked for", () => {
  assert.equal(HOME.how.steps.length, 4);
  assert.equal(HOME.apart.items.length, 3);
  assert.match(HOME.hero.heading, /From design files to scheduled listings/);
  assert.equal(DEMO_VIDEO, null);
  const sections = read("components/marketing/Sections.tsx");
  assert.match(sections, /data-testid="demo-video-slot"/);
  assert.match(sections, /STEP_SCREENS\[i\]/);
  assert.match(sections, /from "@\/lib\/plans"/);
  // The teaser's figures are read, not typed, and the notice stands beside them.
  assert.doesNotMatch(sections, /\$\d/);
  assert.match(sections, /\{PLANNED_NOTICE\}/);
  // The tools page says they are not built yet.
  assert.match(FREE_TOOLS.lead, /not ready yet/);
  assert.equal(FREE_TOOLS.status, "Not built yet");
});

test("cache policy: private unless listed, and never public for a session or a router request", () => {
  const rules = cacheRules();
  const control = (rule: { headers: { key: string; value: string }[] }) => rule.headers.find((h) => h.key === "Cache-Control")?.value;
  // The first rule is the catch-all; everything after it is an exception written on purpose.
  assert.deepEqual(rules[0], { source: "/((?!_next/static|api/).*)", headers: [{ key: "Cache-Control", value: "private, no-store" }] });
  assert.equal(CACHE_PRIVATE, "private, no-store");
  assert.match(CACHE_PAGE, /^public, s-maxage=\d+, stale-while-revalidate=\d+$/);
  assert.match(CACHE_FILE, /^public, .*s-maxage=\d+, stale-while-revalidate=\d+$/);
  const publicRules = rules.slice(1).filter((rule) => control(rule) !== undefined);
  assert.equal(publicRules.length, MARKETING_PAGES.length + PUBLIC_FILES.length);
  for (const rule of publicRules) assert.notEqual(control(rule), CACHE_PRIVATE);
  // A page is public only when none of these is on the request.
  for (const rule of rules.filter((r) => control(r) === CACHE_PAGE)) {
    assert.deepEqual((rule as { missing?: unknown }).missing, PRIVATE_WHEN_PRESENT, rule.source);
  }
  assert.ok(PRIVATE_WHEN_PRESENT.some((m) => m.type === "cookie" && m.key === SESSION_COOKIE));
  assert.ok(PRIVATE_WHEN_PRESENT.some((m) => m.type === "header" && m.key === "rsc"));
  assert.ok(PRIVATE_WHEN_PRESENT.some((m) => m.type === "query" && m.key === "_rsc"));
  // No app page can be public: nothing in app/(app) is on either list.
  const appFolders = fs.readdirSync(path.join(FRONTEND, "app/(app)"), { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => `/${e.name}`);
  assert.ok(appFolders.includes("/dashboard") && appFolders.includes("/login"));
  for (const source of [...MARKETING_PAGES, ...PUBLIC_FILES]) {
    assert.ok(!appFolders.some((folder) => source === folder || source.startsWith(`${folder}/`)), source);
    assert.ok(!source.startsWith("/api"), source);
  }
  // No redirect was added: the site has no old addresses to move, and no app address may change.
  const config = read("next.config.mjs");
  assert.doesNotMatch(config, /redirects\s*\(/);
  assert.match(config, /\.\.\.cacheRules\(\)/);
  // The session cookie's name is the one the middleware reads.
  assert.match(read("lib/session-check.ts"), new RegExp(`SESSION_COOKIE = "${SESSION_COOKIE}"`));
});

test("the visit counter: only on public pages, only if configured, and the app's policy stays strict", () => {
  const layout = read("app/(marketing)/layout.tsx");
  assert.match(layout, /NEXT_PUBLIC_CF_ANALYTICS_TOKEN/);
  assert.match(layout, /static\.cloudflareinsights\.com\/beacon\.min\.js/);
  assert.doesNotMatch(read("app/(app)/layout.tsx"), /cloudflareinsights/);
  assert.doesNotMatch(read("app/layout.tsx"), /cloudflareinsights/);
  const config = read("next.config.mjs");
  assert.match(config, /analytics \? MARKETING_PAGES\.map/);
  assert.match(read("app/(marketing)/privacy/page.tsx"), /Cloudflare Web Analytics/);
  // No token in the source: it is set at build time.
  assert.doesNotMatch(layout + config, /[0-9a-f]{32}/);
});
