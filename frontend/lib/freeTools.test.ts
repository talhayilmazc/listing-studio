// The free tools: each rule and sum, where each fact comes from, the flags, and the cache policy.
// lib/freeToolRules.test.ts proves the rules answer what the app's rules answer.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { BRAND_RULE, BRAND_SOURCES, KIND_WORDS, brandLine, summary, type Line } from "./freeTools/check.ts";
import { calculate, parseAmount, showMoney, type FeeInput } from "./freeTools/feeCalc.ts";
import { DEFAULT_PLACE, FEES, FEES_CHECKED, LISTING_FEE_USD, NOT_INCLUDED_SOURCES, OFFSITE_ADS, PROCESSING, REGULATORY, TRANSACTION_PERCENT, feeCalculatorOpen, regulatoryPercent } from "./freeTools/fees.ts";
import { TAG_EXAMPLE, TAG_RULES, checkTags, parseTags, tagBrands } from "./freeTools/tagCheck.ts";
import { TITLE_EXAMPLE, TITLE_RULES, checkTitle, titleBrands } from "./freeTools/titleCheck.ts";
import { jsonLdNames } from "./marketing/brandRule.ts";
import { FREE_TOOLS } from "./marketing/help.ts";
import { toolApplication } from "./marketing/jsonld.ts";
import { PAGES, ideasOn, metadataFor, pageAt, sitemapPages } from "./marketing/pages.ts";
import { CACHE_PAGE, CACHE_PRIVATE, IDEAS_PATH, MARKETING_PAGES, TOOL_SLUGS, cacheRules } from "./marketing/routes.mjs";
import { READ } from "./marketing/sources.ts";
import { FEE_COMING_SOON, FEE_TOOL, IDEAS_TOOL, TAG_TOOL, TITLE_TOOL, TOOLS, TOOL_CTA } from "./marketing/tools.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8");
const flat = (text: string) => text.replace(/[’‘]/g, "'").replace(/[“”]/g, '"').replace(/\\"/g, '"').replace(/\s+/g, " ");
const LEDGER = flat(read("../docs/guide-sources.md"));
const READ_URLS = new Set(Object.values(READ).map((s) => s.url));

const line = (lines: Line[], id: string) => lines.find((l) => l.id === id)!;

// --- The title checker ---------------------------------------------------------------------------

test("title: empty means not checked, and every rule still shows", () => {
  const lines = checkTitle("  ");
  assert.equal(lines.length, TITLE_RULES.length);
  assert.ok(lines.every((l) => l.verdict === "idle" && l.detail === ""));
});

test("title: a clean one passes every rule", () => {
  const lines = checkTitle("Frog Reading Club Shirt, Book Lover Tee");
  assert.deepEqual(lines.filter((l) => l.verdict !== "pass").map((l) => l.id), ["shorter"]);
  assert.match(line(lines, "shorter").detail, /garment 2 times \(shirt, tee\)/);
  assert.deepEqual(checkTitle("Frog Reading Club Shirt, Book Lover Top").filter((l) => l.verdict !== "pass").map((l) => l.id), []);
  assert.equal(summary(checkTitle("Frog Reading Club Shirt, Book Lover Top")), "Nothing to change on these rules.");
});

test("title: each of Etsy's limits", () => {
  assert.equal(line(checkTitle("a".repeat(140)), "length").verdict, "pass");
  const long = line(checkTitle("a".repeat(141)), "length");
  assert.equal(long.verdict, "fail");
  assert.match(long.detail, /141 characters, 1 over/);
  const emoji = line(checkTitle("Frog 😀 Shirt"), "characters");
  assert.equal(emoji.verdict, "fail");
  assert.match(emoji.detail, /😀/);
  assert.match(line(checkTitle("Frog\tShirt"), "characters").detail, /a tab or line break.*remove/);
  assert.equal(line(checkTitle("Mom’s Shirt"), "characters").verdict, "pass"); // punctuation, in a title
  assert.equal(line(checkTitle("Café Crème Größe ™"), "characters").verdict, "pass");
  assert.equal(line(checkTitle("Mom & Dad + Kids: 50% Tee"), "once").verdict, "pass");
  const twice = line(checkTitle("Mom & Dad & Kids"), "once");
  assert.equal(twice.verdict, "fail");
  assert.match(twice.detail, /Write "and"/);
  // Etsy suggests the word count and does not require it: never "fix this".
  assert.equal(line(checkTitle(Array(14).fill(0).map((_, i) => `w${i}`).join(" ")), "words").verdict, "pass");
  const words = line(checkTitle(Array(15).fill(0).map((_, i) => `w${i}`).join(" ")), "words");
  assert.equal(words.verdict, "warn");
  assert.match(words.detail, /not a limit/);
});

test("title: each piece of our advice", () => {
  const lines = checkTitle(TITLE_EXAMPLE);
  assert.match(line(lines, "repeats").detail, /"nurse"/);
  assert.match(line(lines, "subjective").detail, /"cute", "perfect"/);
  assert.equal(line(lines, "commerce").detail, 'Yours uses "free shipping".'); // not "shipping" as well
  assert.match(line(lines, "gift").detail, /"gift", "for her"/);
  for (const id of ["repeats", "subjective", "commerce", "gift"]) assert.equal(line(lines, id).verdict, "warn", id);
  assert.equal(line(lines, "item_first").verdict, "pass");
  assert.equal(line(checkTitle("Sunset Lake, Nurse Shirt"), "item_first").verdict, "warn");
  assert.match(line(checkTitle("Sunset Lake, Nurse Shirt"), "item_first").detail, /"shirt" comes after/);
  // A mug is not a garment: the checker says it cannot tell rather than calling it wrong.
  assert.equal(line(checkTitle("Handmade Ceramic Mug"), "item_first").verdict, "note");
  assert.equal(line(checkTitle("One, Two, Three, Four, Five Shirt"), "shorter").verdict, "warn");
  assert.match(summary(lines), /worth a look/);
  assert.match(summary(checkTitle("a".repeat(141))), /1 thing to fix/);
});

test("title and tags: the brand warning finds what the app finds, and stops nothing", () => {
  assert.deepEqual(titleBrands("DisneyWorld Trip Shirt"), ["Disney"]);
  assert.deepEqual(titleBrands("Marvelous Shirt"), []);
  assert.deepEqual(tagBrands(["mick ey tee", "plain tag", "#disney"]).sort(), ["Disney", "Mickey"]);
  // A brand changes no rule's verdict.
  assert.deepEqual(checkTitle("Disney Shirt").map((l) => l.verdict), checkTitle("Frogs Shirt").map((l) => l.verdict));
  for (const source of BRAND_SOURCES) assert.ok(READ_URLS.has(source.url));
  for (const quote of [...BRAND_RULE.matchAll(/"([^"]+)"/g)].map((m) => m[1])) assert.ok(LEDGER.includes(flat(quote)), quote);
  assert.match(brandLine("Disney"), /^Disney is a trademark/);
  // The app's sentence names a claim no page we read states: it stays out of the public tools.
  for (const file of ["lib/freeTools/check.ts", "components/marketing/ToolParts.tsx"]) assert.doesNotMatch(read(file), /often removes/);
});

// --- The tag checker -----------------------------------------------------------------------------

test("tags: split on commas and lines, empty means not checked", () => {
  assert.deepEqual(parseTags(" a b ,\n\n c  d,,e\r\n"), ["a b", "c d", "e"]);
  assert.ok(checkTags([]).every((l) => l.verdict === "idle"));
  assert.equal(checkTags([]).length, TAG_RULES.length);
});

test("tags: each of Etsy's limits", () => {
  const thirteen = Array.from({ length: 13 }, (_, i) => `good tag ${i}`);
  assert.equal(line(checkTags(thirteen), "count").verdict, "pass");
  assert.match(line(checkTags([...thirteen, "x"]), "count").detail, /14, 1 over/);
  assert.equal(line(checkTags([...thirteen, "x"]), "count").verdict, "fail");
  assert.equal(line(checkTags(["a".repeat(20)]), "length").verdict, "pass");
  assert.equal(line(checkTags(["a".repeat(21)]), "length").verdict, "fail");
  assert.equal(line(checkTags(["tag & more"]), "characters").verdict, "fail");
  assert.match(line(checkTags(["tag & more"]), "characters").detail, /write "and"/);
  assert.match(line(checkTags(["curly’s tag"]), "characters").detail, /straight apostrophe/);
  assert.equal(line(checkTags(["mom's tag", "t-shirt ™"]), "characters").verdict, "pass");
  assert.equal(line(checkTags(["'quote first"]), "start").verdict, "fail");
  assert.equal(line(checkTags(["-dash first"]), "start").verdict, "fail");
  assert.equal(line(checkTags(["mom's t-shirt"]), "start").verdict, "pass");
});

test("tags: each piece of our advice", () => {
  const dup = line(checkTags(["nurse shirt", "Nurse Shirt", "shirts nurse"]), "duplicates");
  assert.equal(dup.verdict, "warn");
  assert.match(dup.detail, /Twice: "nurse shirt"/);
  assert.match(dup.detail, /Same words: "nurse shirt" and "shirts nurse"/);
  assert.equal(line(checkTags(["nurse shirt", "doctor shirt"]), "duplicates").verdict, "pass");
  assert.equal(line(checkTags(["nurse shirt"]), "title_words").verdict, "note");
  assert.equal(line(checkTags(["nurse shirt"], "Retro Nurse Shirt"), "title_words").verdict, "warn");
  assert.equal(line(checkTags(["hospital tee"], "Retro Nurse Shirt"), "title_words").verdict, "pass");
  const singles = Array.from({ length: 9 }, (_, i) => `w${i}`);
  assert.equal(line(checkTags(singles), "phrases").verdict, "warn");
  assert.equal(line(checkTags(singles.map((w) => `${w} tag`)), "phrases").verdict, "pass");
  assert.match(line(checkTags(["a b"]), "phrases").detail, /1 of your 1 tag is a phrase/);
  assert.match(line(checkTags(["one"]), "all").detail, /room for 12 more/);
  assert.equal(line(checkTags(Array.from({ length: 13 }, (_, i) => `t ${i}`)), "all").verdict, "pass");
  assert.ok(checkTags(parseTags(TAG_EXAMPLE)).some((l) => l.verdict === "fail"));
});

// --- Every rule is Etsy's, quoted with its page, or ours and labelled ------------------------------

test("every rule is either quoted from a page that was read, or labelled as our advice", () => {
  assert.deepEqual(KIND_WORDS, { etsy: "Etsy's rule", advice: "Our advice" });
  for (const rule of [...TITLE_RULES, ...TAG_RULES]) {
    if (rule.kind === "etsy") {
      assert.ok(READ_URLS.has(rule.source.url), `${rule.id}: ${rule.source.url}`);
      assert.ok(LEDGER.includes(flat(rule.quote)), `${rule.id}: not in docs/guide-sources.md: ${rule.quote}`);
    } else {
      assert.ok(rule.why.length > 20, rule.id);
      assert.doesNotMatch(rule.why, /\bEtsy\b/, `${rule.id}: advice must not say what Etsy says`);
    }
  }
  assert.deepEqual(TITLE_RULES.filter((r) => r.kind === "advice").map((r) => r.id), ["item_first", "repeats", "subjective", "commerce", "gift", "shorter"]);
  assert.deepEqual(TAG_RULES.filter((r) => r.kind === "advice").map((r) => r.id), ["duplicates", "title_words", "phrases", "all"]);
  assert.deepEqual(TAG_RULES.filter((r) => r.kind === "etsy").map((r) => r.id), ["count", "length", "characters", "start"]);
  const parts = read("components/marketing/ToolParts.tsx");
  assert.match(parts, /KIND_WORDS\[line\.kind\]/);
  assert.match(parts, /line\.source\.url/);
});

test("the checkers send nothing and use no AI", () => {
  for (const file of ["components/marketing/TitleChecker.tsx", "components/marketing/TagChecker.tsx", "components/marketing/FeeCalculator.tsx", "lib/freeTools/titleCheck.ts", "lib/freeTools/tagCheck.ts", "lib/freeTools/feeCalc.ts", "lib/listingRules.ts", "lib/brandSpot.ts"]) {
    assert.doesNotMatch(read(file), /\bfetch\(|XMLHttpRequest|localStorage|document\.cookie|lib\/api"/, file);
  }
  // They are the app's modules: the checker imports them and repeats no limit or word of its own.
  assert.match(read("lib/freeTools/titleCheck.ts"), /from "\.\.\/etsyText\.ts"/);
  assert.match(read("lib/freeTools/tagCheck.ts"), /from "\.\.\/etsyText\.ts"/);
  assert.doesNotMatch(read("lib/freeTools/titleCheck.ts"), /"cute",|"free shipping",/);
});

// --- The fee calculator --------------------------------------------------------------------------

const SALE: FeeInput = { place: "United States", price: 25, shippingCharged: 5, salesTax: null, baseCost: 12, shippingCost: 4.5, offsite: "none", usdRate: null };
const sum = (input: Partial<FeeInput>) => {
  const outcome = calculate({ ...SALE, ...input });
  assert.ok(outcome.ok, JSON.stringify(outcome));
  return outcome.result;
};
const fee = (result: ReturnType<typeof sum>, id: string) => result.fees.find((f) => f.id === id)?.amount;

test("a US sale, worked by hand", () => {
  const r = sum({});
  assert.equal(r.currency, "USD");
  assert.equal(r.revenue, 30);
  assert.equal(fee(r, "listing"), 0.2); // $0.20
  assert.equal(fee(r, "transaction"), 1.95); // 6.5% of 30
  assert.equal(fee(r, "processing"), 1.15); // 3% of 30 = 0.90, + 0.25
  assert.equal(fee(r, "regulatory"), undefined); // none in the US
  assert.equal(fee(r, "offsite"), undefined);
  assert.equal(r.totalFees, 3.3);
  assert.equal(r.costs, 16.5);
  assert.equal(r.profit, 10.2); // 30 - 3.30 - 16.50
  assert.equal(r.margin, 34); // 10.20 / 30
});

test("sales tax raises only payment processing, and is not income", () => {
  const r = sum({ salesTax: 2.4 });
  assert.equal(r.revenue, 30);
  assert.equal(fee(r, "processing"), 1.22); // 3% of 32.40 = 0.972 -> 0.97, + 0.25
  assert.equal(fee(r, "transaction"), 1.95);
});

test("Offsite Ads: both rates, and the cap", () => {
  assert.equal(fee(sum({ offsite: "under" }), "offsite"), 4.5); // 15% of 30
  assert.equal(fee(sum({ offsite: "over" }), "offsite"), 3.6); // 12% of 30
  assert.equal(fee(sum({ price: 1000, shippingCharged: 0, offsite: "under" }), "offsite"), 100); // capped, not 150
  assert.equal(fee(sum({ price: 1000, shippingCharged: 0, offsite: "over" }), "offsite"), 100); // capped, not 120
  assert.equal(fee(sum({ price: 800, shippingCharged: 0, offsite: "over" }), "offsite"), 96);
});

test("a country with a regulatory fee, its own processing rate, and dollars converted at the seller's rate", () => {
  const r = sum({ place: "United Kingdom", usdRate: 0.8 });
  assert.equal(r.currency, "GBP");
  assert.equal(fee(r, "listing"), 0.16); // $0.20 at 0.80
  assert.equal(fee(r, "processing"), 1.4); // 4% of 30 = 1.20, + 0.20
  assert.equal(fee(r, "regulatory"), 0.14); // 0.48% of 30 = 0.144
  assert.equal(fee(sum({ place: "United Kingdom", usdRate: 0.8, price: 1000, offsite: "under" }), "offsite"), 80); // $100 at 0.80
  // A place name with a note reads its country's regulatory fee.
  assert.equal(regulatoryPercent("Canada (international orders)"), 0.5);
  assert.equal(fee(sum({ place: "Canada (international orders)", usdRate: 1.4 }), "processing"), 1.45); // 4% + 0.25
  assert.equal(regulatoryPercent("Germany"), 0);
});

test("a loss is a loss", () => {
  const r = sum({ price: 10, shippingCharged: 0, baseCost: 15, shippingCost: 5 });
  assert.ok(r.profit < 0);
  assert.ok(r.margin! < 0);
});

test("what has no figure behind it is blank, never zero", () => {
  assert.deepEqual(calculate({ ...SALE, price: null }), { ok: false, needs: "Enter your item's price." });
  assert.deepEqual(calculate({ ...SALE, price: 0 }), { ok: false, needs: "Enter your item's price." });
  assert.equal(calculate({ ...SALE, place: "Atlantis" }).ok, false);
  assert.equal(calculate({ ...SALE, baseCost: -1 }).ok, false);
  const noRate = calculate({ ...SALE, place: "United Kingdom" });
  assert.equal(noRate.ok, false);
  assert.match((noRate as { needs: string }).needs, /1 USD is in GBP/);
  assert.equal(parseAmount(""), null);
  assert.equal(parseAmount("abc"), null);
  assert.equal(parseAmount("12,50"), 12.5);
  assert.equal(parseAmount(" 3.5 "), 3.5);
  assert.equal(showMoney(3.5, "USD"), "3.50 USD");
});

test("the lines add up to the total, for every country", () => {
  for (const { place, currency } of PROCESSING) {
    const r = sum({ place, usdRate: currency === "USD" ? null : 1.5, price: 19.99, shippingCharged: 4.99, offsite: "under" });
    const added = r.fees.reduce((s, f) => s + Math.round(f.amount * 100), 0);
    assert.equal(added, Math.round(r.totalFees * 100), place);
    assert.equal(Math.round(r.revenue * 100) - added - Math.round(r.costs * 100), Math.round(r.profit * 100), place);
  }
});

test("the fee file: dated, one page per fee, every fee still to confirm, the figures as read", () => {
  assert.match(FEES_CHECKED, /^\d{4}-\d{2}-\d{2}$/);
  assert.deepEqual(FEES.map((f) => f.id), ["listing", "transaction", "processing", "regulatory", "offsite"]);
  for (const f of FEES) {
    assert.equal(f.status, "to confirm", f.id); // until the owner has compared them with Etsy's fee page
    assert.ok(READ_URLS.has(f.source.url), f.id);
    assert.ok(LEDGER.includes(flat(f.quote)), `${f.id}: not in docs/guide-sources.md: ${f.quote}`);
  }
  for (const s of NOT_INCLUDED_SOURCES) assert.ok(READ_URLS.has(s.url));
  assert.equal(LISTING_FEE_USD, 0.2);
  assert.equal(TRANSACTION_PERCENT, 6.5);
  assert.deepEqual(OFFSITE_ADS, { under: 15, over: 12, capUsd: 100, thresholdUsd: 10000 });
  assert.equal(PROCESSING.length, 64);
  assert.equal(new Set(PROCESSING.map((p) => p.place)).size, 64);
  assert.ok(PROCESSING.some((p) => p.place === DEFAULT_PLACE));
  // Every row and every regulatory rate is in the record, as read.
  for (const p of PROCESSING) assert.ok(LEDGER.includes(`${p.place}: ${p.percent}% + `), `not on record: ${p.place} ${p.percent}%`);
  assert.equal(Object.keys(REGULATORY).length, 9);
  for (const [country, percent] of Object.entries(REGULATORY)) assert.ok(LEDGER.includes(`${country}: ${percent}`), country);
  // The list for the owner names every fee and its page.
  const list = read("../docs/free-tools.md");
  for (const f of FEES) assert.ok(list.includes(f.source.url), f.id);
  assert.ok(list.includes("https://www.etsy.com/legal/fees"));
});

test("the calculator's flag: off by default, coming soon, and no figure on the page", () => {
  assert.equal(feeCalculatorOpen(undefined), false);
  assert.equal(feeCalculatorOpen("false"), false);
  assert.equal(feeCalculatorOpen("1"), false);
  assert.equal(feeCalculatorOpen("true"), true);
  assert.equal(FEE_COMING_SOON.status, "Coming soon");
  // While it is not up, the page says "will", and shows no figure.
  assert.match(FEE_COMING_SOON.lead, /will show/);
  assert.match(FEE_COMING_SOON.explain[0], /will work out/);
  assert.doesNotMatch(FEE_COMING_SOON.lead, /\d/);
  assert.doesNotMatch(FEE_COMING_SOON.body, /\d/);
  const page = read("app/(marketing)/free-tools/fee-calculator/page.tsx");
  assert.match(page, /const open = feeCalculatorOpen\(\)/);
  assert.match(page, /\{open\s*\?\s*<FeeCalculator \/>/);
  assert.match(read("../.env.example"), /^FEE_CALCULATOR_CONFIRMED=false$/m);
  assert.match(read("../docker/frontend.prod.Dockerfile"), /ARG FEE_CALCULATOR_CONFIRMED=false/);
  assert.match(read("../docker-compose.prod.yml"), /FEE_CALCULATOR_CONFIRMED: \$\{FEE_CALCULATOR_CONFIRMED:-false\}/);
  const calculator = read("components/marketing/FeeCalculator.tsx");
  assert.match(calculator, /Fees checked on \$\{longDate\(FEES_CHECKED\)\}\. Etsy can change its fees/);
  assert.match(calculator, /"To confirm"|To confirm/);
});

// --- The AI tool's flag and the cache --------------------------------------------------------------

test("the ideas tool: off by default, a 404 while off, on the sitemap and the tools page only while on", () => {
  assert.equal(ideasOn(undefined), false);
  assert.equal(ideasOn("true"), true);
  assert.ok(!sitemapPages("false").some((p) => p.path === IDEAS_PATH));
  assert.ok(sitemapPages("true").some((p) => p.path === IDEAS_PATH));
  for (const path of TOOL_SLUGS.map((slug) => `/free-tools/${slug}`)) assert.ok(sitemapPages("false").some((p) => p.path === path), path);
  const page = read("app/(marketing)/free-tools/listing-ideas/page.tsx");
  assert.match(page, /if \(!ideasOn\(\)\) notFound\(\)/);
  assert.match(page, /export const dynamic = "force-dynamic"/);
  assert.match(read("app/(marketing)/free-tools/page.tsx"), /ideasOn\(\) \? \[\.\.\.TOOLS, IDEAS_TOOL\] : TOOLS/);
  assert.match(read("../.env.example"), /^FREE_TOOL_AI_ENABLED=false$/m);
});

test("the checkers are public like any page; the ideas page is never in the public cache", () => {
  for (const slug of TOOL_SLUGS) assert.ok(MARKETING_PAGES.includes(`/free-tools/${slug}`), slug);
  assert.deepEqual(TOOLS.map((t) => t.slug), TOOL_SLUGS);
  assert.ok(!MARKETING_PAGES.includes(IDEAS_PATH));
  const rules = cacheRules();
  for (const rule of rules.slice(1)) assert.ok(rule.source !== IDEAS_PATH && !rule.source.startsWith("/free-tools/:") && !rule.source.includes("free-tools/("), rule.source);
  assert.equal(rules[0].headers[0].value, CACHE_PRIVATE);
  assert.ok(rules.some((r) => r.source === "/free-tools/title-checker" && r.headers[0].value === CACHE_PAGE));
  // docs/seo.md's Cloudflare rule keeps it out by name too.
  const seo = read("../docs/seo.md");
  assert.match(seo, /starts_with\(http\.request\.uri\.path, "\/free-tools\/"\)/);
  assert.match(seo, /not starts_with\(http\.request\.uri\.path, "\/free-tools\/listing-ideas"\)/);
});

test("only the ideas page may load Turnstile", () => {
  const config = read("next.config.mjs");
  assert.match(config, /htmlLimitedBots: \/\.\*\//); // its metadata stays in the head, though it is rendered per request
  assert.match(config, /source: IDEAS_PATH, headers: \[\{ key: "Content-Security-Policy", value: ideasCsp \}\]/);
  assert.equal(config.match(/challenges\.cloudflare\.com/g)?.length, 1);
  for (const file of ["components/marketing/TitleChecker.tsx", "components/marketing/TagChecker.tsx", "components/marketing/FeeCalculator.tsx", "components/marketing/ToolPage.tsx", "app/(marketing)/layout.tsx"]) {
    assert.doesNotMatch(read(file), /turnstile/i, file);
  }
  assert.match(read("components/marketing/ListingIdeas.tsx"), /challenges\.cloudflare\.com\/turnstile/);
});

// --- Each page ------------------------------------------------------------------------------------

test("each tool's page: its own metadata, no Etsy in its title or h1, an explanation, a guide, a quiet invitation", () => {
  const all = [...TOOLS, IDEAS_TOOL];
  assert.deepEqual(all.map((t) => t.slug), ["title-checker", "tag-checker", "fee-calculator", "listing-ideas"]);
  for (const tool of all) {
    const at = `/free-tools/${tool.slug}`;
    const page = pageAt(at);
    assert.equal(page.parent, "/free-tools");
    assert.equal(page.title, tool.title);
    assert.ok(page.title.length <= 70 && page.title.endsWith("| Listyro"), page.title);
    for (const text of [tool.title, tool.h1, tool.name, page.card, page.crumb]) assert.doesNotMatch(text, /etsy/i, text);
    assert.ok(tool.description.length >= 50 && tool.description.length <= 200, `${tool.slug}: ${tool.description.length}`);
    assert.ok(tool.explain.length >= 2);
    const known = new Set(PAGES.map((p) => p.path));
    assert.ok(known.has(tool.guide.href), tool.guide.href);
    if (tool.also) assert.ok(known.has(tool.also.href), tool.also.href);
    const meta = metadataFor(at);
    assert.deepEqual(meta.alternates, { canonical: at });
    for (const name of jsonLdNames(toolApplication(tool))) assert.doesNotMatch(name, /etsy/i, name);
    assert.doesNotMatch(JSON.stringify(toolApplication(tool)), /aggregateRating|offers|ratingValue/);
    const file = read(`app/(marketing)/free-tools/${tool.slug}/page.tsx`);
    assert.match(file, /<ToolPage tool=\{/);
    assert.match(file, /metadataFor\(/);
  }
  assert.equal(TOOL_CTA.label, "Do this for 100 listings at once with Listyro");
  assert.equal(TOOL_CTA.href, "/request-invite");
  const frame = read("components/marketing/ToolPage.tsx");
  assert.match(frame, /data-testid="tool-cta"/);
  assert.match(frame, /toolApplication\(tool\)/);
  assert.match(frame, /<PageHead /);
  // The cache rules are the same: nothing here depends on who asks.
  for (const tool of TOOLS) assert.doesNotMatch(read(`app/(marketing)/free-tools/${tool.slug}/page.tsx`), /cookies\(|headers\(/);
});

test("the tools page lists them and no longer says they are not ready", () => {
  assert.doesNotMatch(`${FREE_TOOLS.lead} ${FREE_TOOLS.description}`, /not ready|not built/i);
  // True with the AI tool on too: that one does not stay in the browser, and the page must not say it does.
  assert.match(FREE_TOOLS.lead, /The checkers and the calculator run in your browser/);
  assert.equal(FREE_TOOLS.h1, "Free tools for sellers");
  const page = read("app/(marketing)/free-tools/page.tsx");
  assert.match(page, /tools\.map\(\(tool\)/);
  assert.match(page, /FEE_COMING_SOON\.status/);
  assert.deepEqual([TITLE_TOOL, TAG_TOOL, FEE_TOOL].map((t) => t.guide.href), ["/guides/etsy-title-rules-2026", "/guides/etsy-tags-13-tags-20-characters", "/features/profit-report"]);
});

test("the privacy policy says what the tools do", () => {
  const policy = read("app/(marketing)/privacy/page.tsx");
  assert.match(policy, /data-testid="free-tools-privacy"/);
  assert.match(policy, /run in your browser/);
  assert.match(policy, /Cloudflare Turnstile/);
  assert.match(policy, /R\.freeToolVisitorDays/);
});
