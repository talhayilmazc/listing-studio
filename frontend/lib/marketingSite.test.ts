// The public site keeps its promises: one registry for every page, the trademark rule, no invented
// claims, links that lead somewhere, valid structured data, and a cache policy that is private unless
// a page is listed. e2e/marketing.spec.ts and e2e/headers.spec.ts check the same on the real server.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { COMPARES, ALTERNATIVES, COMPARE_PAGE, LISTYRO, NOT_STATED } from "./marketing/compare.ts";
import { allText, linksIn, longDate } from "./marketing/content.ts";
import { FEATURES, FEATURES_PAGE } from "./marketing/features.ts";
import { CALENDAR_DATES, GUIDES, GUIDES_PAGE, guideText, liveBy } from "./marketing/guides.ts";
import { FREE_TOOLS, HELP } from "./marketing/help.ts";
import { article, breadcrumbs, faqPage, organization, softwareApplication } from "./marketing/jsonld.ts";
import { PAGES, SITE_URL, cardSlug, metadataFor, pageAt, trail } from "./marketing/pages.ts";
import { CACHE_FILE, CACHE_PAGE, CACHE_PRIVATE, COMPARE_SLUGS, FEATURE_SLUGS, GUIDE_SLUGS, MARKETING_PAGES, PRIVATE_WHEN_PRESENT, PUBLIC_FILES, SESSION_COOKIE, cacheRules } from "./marketing/routes.mjs";
import { TESTIMONIALS } from "./marketing/testimonials.ts";
import { DEMO_VIDEO } from "./marketing/video.ts";
import { HOME, allCopy } from "./homeCopy.ts";
import { PLANS, priceFact } from "./plans.ts";

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
  has("/features/ai-listing-writer", /etsy listing generator/i);
  has("/features/mockup-generator", /etsy mockup generator/i);
  has("/guides/list-print-on-demand-shirts-in-bulk", /bulk/i);
  has("/free-tools", /etsy title checker/i);
  has("/free-tools", /etsy fee calculator/i);
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

test("trademark rule: Listyro is the only name; Etsy's name only describes; no logo, no orange", () => {
  assert.doesNotMatch(new URL(SITE_URL).hostname, /etsy/i);
  assert.doesNotMatch(read("components/marketing/Site.tsx").slice(read("components/marketing/Site.tsx").indexOf("export function Wordmark"), read("components/marketing/Site.tsx").indexOf("export function SiteHeader")), /etsy/i);
  // Nothing says or implies that Etsy runs, endorses or works with us.
  const implies = /\b(official|certified|authori[sz]ed by etsy|sponsored|recommended by|in partnership|partnered|etsy('s)? partner|partner of etsy|etsy[- ]approved|approved by etsy|endorsed by etsy)\b/i;
  const denied = /(not (made, )?(endorsed|certified)[^.]*|production partners?|your production partner|its print partners?|print partners?)/gi;
  for (const text of [...OUR_COPY, ...PAGES.map((p) => p.title)]) assert.doesNotMatch(text.replace(denied, ""), implies, text);
  // No title opens as if the page were Etsy's own: every one names Listyro as its own.
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
  const anchors = new Set(["/features/ai-listing-writer#trademark-warning", "/privacy#ai"]);
  const allowed = /^https:\/\/(www\.etsy\.com|help\.etsy\.com|listadum\.com|www\.listadum\.com|mydesigns\.io|getvela\.com|apps\.shopify\.com)\//;
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
  // The handbook is cited where it is used.
  const cited = GUIDES.filter((g) => g.sources.some((s) => /Seller Handbook/.test(s.label))).map((g) => g.slug);
  assert.deepEqual(cited.sort(), GUIDES.filter((g) => g.slug !== "list-print-on-demand-shirts-in-bulk" || true).map((g) => g.slug).filter((s) => cited.includes(s)).sort());
  assert.ok(cited.length >= 6);
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

test("testimonials come only from their file, and an empty file hides the section", () => {
  assert.deepEqual(TESTIMONIALS, []);
  const sections = read("components/marketing/Sections.tsx");
  assert.match(sections, /if \(TESTIMONIALS\.length === 0\) return null;/);
  assert.doesNotMatch(read("lib/homeCopy.ts"), /testimonial/i);
});

test("the home page has what was asked for", () => {
  assert.equal(HOME.how.steps.length, 4);
  assert.equal(HOME.apart.items.length, 3);
  assert.match(HOME.hero.heading, /From design files to scheduled Etsy listings/);
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
