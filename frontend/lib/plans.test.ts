// The launch plans: written once, shown as prices for display and nothing more.
//
// The pricing page shows what we intend to charge at launch. While billing is off it must never be
// mistaken for a shop window: no payment is taken, so the page has no form and no payment link, it
// says so at the top, and every number on it, on the home page and in the documents to Etsy, comes
// from lib/plans.ts.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import {
  EXTRA_PACK, FREE_REWRITES, INCLUDED, LISTING_LINE, PLANNED_NOTICE, PLANS, PRICING_HEADING, PRICING_LINE, SPECIAL, TRIAL, YEARLY_MONTHS, YEARLY_NOTE,
  extraPackFact, listingsFact, packLine, planLine, priceFact, shopsFact, yearlyPerMonthFact, yearlyPrice, yearlyPriceFact,
} from "./plans.ts";
import { HOME, allCopy, headings } from "./homeCopy.ts";
import { BILLING_ON, PRIMARY_CTA } from "./marketing/cta.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1");
const PRICING = "app/(marketing)/pricing/page.tsx";
const CARDS = "components/marketing/PlanCards.tsx";
const TEASER = "components/marketing/Sections.tsx";

test("the plan config: the launch plans as agreed (7 November 2026)", () => {
  assert.deepEqual(PLANS.map(planLine), [
    "Starter: 150 AI listings a month, 1 shop, $19/month, $190/year",
    "Growth: 300 AI listings a month, 3 shops, $29/month, $290/year",
    "Pro: 600 AI listings a month, 5 shops, $49/month, $490/year",
    "Business: 1,000 AI listings a month, 10 shops, $69/month, $690/year",
  ]);
  // Growth is the one highlighted.
  assert.deepEqual(PLANS.filter((plan) => plan.highlight).map((plan) => plan.name), ["Growth"]);
  assert.deepEqual(EXTRA_PACK, { name: "Extra pack", listings: 100, price: 9, line: EXTRA_PACK.line });
  assert.equal(extraPackFact(), "Extra pack: +100 AI listings for $9");
  assert.equal(packLine(), "Extra pack: +100 AI listings for $9, on any plan");
  assert.equal(TRIAL.listings, 25);
  assert.match(TRIAL.line, /25 AI listings when you connect a shop/);
  assert.equal(SPECIAL.name, "Special");
  assert.match(SPECIAL.body, /More than 1,000 AI listings a month/);
  assert.deepEqual(SPECIAL.cta, { label: "Contact us", href: "/request-invite?plan=custom" });
  // Each plan is larger than the one before it in everything that is counted.
  for (let i = 1; i < PLANS.length; i++) {
    assert.ok(PLANS[i].listings > PLANS[i - 1].listings && PLANS[i].shops > PLANS[i - 1].shops && PLANS[i].price > PLANS[i - 1].price);
  }
  for (const word of ["Mockups", "write-it-yourself", "Profiles", "SKU", "Scheduling", "Trademark", "Profit"]) {
    assert.ok(INCLUDED.some((item) => item.includes(word)), word);
  }
});

test("a year costs ten months: yearly = 10 x monthly, and the monthly equivalent is that over twelve", () => {
  assert.equal(YEARLY_MONTHS, 10);
  assert.equal(YEARLY_NOTE, "Pay for 10 months, get 12");
  for (const plan of PLANS) {
    assert.equal(yearlyPrice(plan), plan.price * 10, plan.name);
    const perMonth = Math.round(((plan.price * 10) / 12) * 100) / 100;
    assert.equal(yearlyPerMonthFact(plan), `$${Number.isInteger(perMonth) ? perMonth : perMonth.toFixed(2)} a month`, plan.name);
    assert.ok(perMonth < plan.price);
  }
  assert.deepEqual(PLANS.map(priceFact), ["$19", "$29", "$49", "$69"]);
  assert.deepEqual(PLANS.map(yearlyPriceFact), ["$190", "$290", "$490", "$690"]);
  assert.deepEqual(PLANS.map(yearlyPerMonthFact), ["$15.83 a month", "$24.17 a month", "$40.83 a month", "$57.50 a month"]);
  assert.deepEqual(PLANS.map(listingsFact), ["150 AI listings a month", "300 AI listings a month", "600 AI listings a month", "1,000 AI listings a month"]);
  assert.deepEqual(PLANS.map(shopsFact), ["1 shop", "3 shops", "5 shops", "10 shops"]);
});

test("only AI-written listings count, as the pricing page says once", () => {
  assert.equal(
    LISTING_LINE,
    "Only AI-written listings count. A listing is one design written by AI, with all its photos, and rewriting the same design is free up to 3 times. Listings you write yourself, photos, and sending a listing to more shops do not count.",
  );
  // The number in that sentence is the one the server uses (core/allowance.py).
  assert.equal(FREE_REWRITES, 3);
  const server = fs.readFileSync(path.join(FRONTEND, "..", "backend", "app", "core", "allowance.py"), "utf8");
  assert.match(server, /^FREE_REWRITES = 3$/m);
  assert.ok(code(read(PRICING)).includes("{LISTING_LINE}"));
  assert.ok(code(read(PRICING)).includes("{TRIAL.line}"));
});

test("while billing is off, the page says these are launch prices and nothing is charged yet", () => {
  assert.equal(BILLING_ON, false);
  assert.equal(PLANNED_NOTICE, "Launch prices. Nothing is charged yet: billing starts after launch, and we will tell you before it does.");
  assert.equal(PRICING_HEADING, "Pay for the listings Listyro writes");
  assert.equal(PRICING_LINE, "Invite-only early access. No payment is taken today; these plans start after launch.");
  const page = code(read(PRICING));
  const order = ["<h1", "{PRICING_HEADING}", "{PRICING_LINE}", "{PRIMARY_CTA.label}", "{PLANNED_NOTICE}", "<PlanCards />", "{LISTING_LINE}"].map((piece) => page.indexOf(piece));
  assert.ok(order.every((at) => at !== -1), String(order));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  // The home page's teaser carries the same notice beside the same figures.
  const teaser = code(read(TEASER));
  assert.match(teaser, /\{PLANNED_NOTICE\}/);
  for (const piece of ["PLANS.map(", "priceFact(plan)", "listingsFact(plan)", "shopsFact(plan)", "{TRIAL.line}"]) assert.ok(teaser.includes(piece), piece);
});

test("the pricing page and the cards lay the plans out from the constants and type none of them", () => {
  const page = code(read(PRICING));
  const cards = code(read(CARDS));
  assert.match(page, /<PlanCards \/>/);
  assert.match(cards, /import \{[^}]*\bPLANS\b[^}]*\} from "@\/lib\/plans";/);
  for (const piece of ["{plan.name}", "yearly ? yearlyPriceFact(plan) : priceFact(plan)", "listingsFact(plan)", "shopsFact(plan)", "{SPECIAL.body}", "YEARLY_NOTE", "yearlyPerMonthFact(plan)", "extraPackFact()"]) {
    assert.ok(cards.includes(piece), piece);
  }
  for (const piece of ["{PRICING_HEADING}", "{PRICING_LINE}", "{PLANNED_NOTICE}", "{LISTING_LINE}", "{INCLUDED.map("]) assert.ok(page.includes(piece), piece);
  // No plan name, price or count of their own, here or in the home page's teaser.
  for (const source of [page, cards, code(read(TEASER))]) {
    for (const plan of PLANS) {
      assert.ok(!source.includes(`"${plan.name}"`) && !source.includes(`>${plan.name}<`), plan.name);
      for (const fact of [priceFact(plan), yearlyPriceFact(plan), listingsFact(plan)]) assert.ok(!source.includes(fact), fact);
    }
    assert.doesNotMatch(source, /\$\s?\d/);
  }
  assert.doesNotMatch(page + cards, /\b(Studio|Catalog)\b|Scheduling add-on|\$69\/month|100 listings a month/, "the old plans are gone");
});

test('"free" is not a headline or a badge on the pricing page or the home page', () => {
  for (const source of [read(PRICING), read(CARDS)]) {
    for (const [, heading] of source.matchAll(/<h[1-3][^>]*>([\s\S]*?)<\/h[1-3]>/g)) assert.doesNotMatch(heading, /\bfree\b/i, heading);
    assert.doesNotMatch(code(source), />\s*Free\b/, "no line that opens with Free");
  }
  assert.doesNotMatch(PRICING_HEADING + PLANNED_NOTICE + PRICING_LINE, /\bfree\b/i);
  for (const heading of headings()) assert.doesNotMatch(heading, /\bfree\b/i, heading);
  // The badge on the highlighted plan is a suggestion, not a claim about other sellers.
  assert.match(read(CARDS), />A good place to start</);
  assert.doesNotMatch(read(CARDS), /most (sellers|popular)/i);
  assert.equal(HOME.hero.note, "Invite-only early access. No payment is taken today.");
  for (const text of [HOME.hero.heading, HOME.hero.sub, HOME.closing.body]) assert.doesNotMatch(text, /\bfree\b/i, text);
  // That no payment is taken is still said plainly, in each place a visitor looks for it.
  assert.match(PRICING_LINE, /No payment is taken today/);
  assert.match(PLANNED_NOTICE, /Nothing is charged yet/);
  assert.match(HOME.faq.items.find((item) => item.q === "Can I cancel?")!.a, /Nothing is charged during early access/);
});

test("the pricing page has no checkout and no payment form", () => {
  const page = code(read(PRICING));
  const cards = code(read(CARDS));
  for (const source of [page, cards]) {
    assert.doesNotMatch(source, /<form\b|<input\b|<select\b|<textarea\b|type="submit"|onSubmit|action=|method=/);
    assert.doesNotMatch(source, /stripe|paddle|paypal|lemon ?squeezy|braintree|iyzico|checkout\.|\/checkout|\/pay\b|\/billing|\/subscribe|buy now|add to cart|subscribe now|fetch\(|api\./i);
  }
  // The only buttons are the two halves of the Monthly / Yearly switch: plain state, nothing sent anywhere.
  assert.doesNotMatch(page, /<button\b|"use client"|useState/);
  assert.equal((cards.match(/<button /g) ?? []).length, 2);
  assert.match(cards, /const \[yearly, setYearly\] = useState\(false\);/, "Monthly is what is shown first");
  // Every link leads to the configured call to action or the quote request.
  const links = [...(page + cards).matchAll(/<a href=(?:"([^"]+)"|\{([^}]+)\})/g)].map((m) => m[1] ?? m[2]);
  assert.deepEqual(links, ["PRIMARY_CTA.href", "SPECIAL.cta.href"]);
  assert.equal(PRIMARY_CTA.href, "/request-invite");
  // Nor is there a payment library anywhere in the frontend.
  const pkg = JSON.parse(read("package.json"));
  const packages = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies }).join(" ");
  assert.doesNotMatch(packages, /stripe|paddle|paypal|braintree|lemonsqueezy|iyzi|chargebee|recurly/i);
});

test("a quote request asks for the listings a month and the number of shops", () => {
  const form = code(read("components/marketing/InviteForm.tsx"));
  assert.match(form, /get\("plan"\) === "custom"/);
  assert.match(form, /htmlFor="ir-listings" className="label">New listings a month</);
  assert.match(form, /htmlFor="ir-count" className="label">Number of shops</);
  assert.match(form, /const sent = custom \? quoteNote\(listings, note\) : note;/);
});

test("the documents to Etsy list the same plans; the approved list stays as it was", () => {
  const application = read("../docs/commercial-application.md");
  // The approved plan list is a record and is not rewritten.
  for (const line of [
    "- Starter: 100 listings a month, 1 shop, scheduling not included (available as an add-on), $69/month, $690/year\n",
    "- Catalog: 500 listings a month, 8 shops, scheduling included, $329/month, $3,290/year\n",
  ]) assert.ok(application.includes(line), line);
  // The dated note below it lists today's plans, from this file.
  const note = application.slice(application.indexOf("## Launch prices (7 November 2026, after approval)"));
  assert.ok(note.length > 100, "the dated note");
  for (const plan of PLANS) assert.ok(note.includes(`- ${planLine(plan)}\n`), planLine(plan));
  assert.ok(note.includes(`- ${packLine()}\n`), packLine());
  assert.ok(note.includes("- Special: more than 1,000 AI listings a month, quoted on request\n"));
  // The capacity the largest plan needs, worked out from the plan itself.
  const largest = PLANS[PLANS.length - 1];
  assert.equal(largest.listings, 1000);
  assert.ok(Math.abs((largest.listings * 35) / 30 - 1170) < 10, "about 1,170 calls a day at about 35 per listing");
  assert.ok(note.includes("the largest plan (1,000 listings a month) needs about 1,170 calls a day per seller"));
  const compliance = read("../docs/etsy-compliance.md");
  for (const plan of PLANS) assert.ok(compliance.includes(planLine(plan)), planLine(plan));
  assert.ok(compliance.includes(packLine()), packLine());
  assert.ok(compliance.includes(PLANNED_NOTICE), "the notice, word for word");
  const prices = new Set([...PLANS.flatMap((plan) => [priceFact(plan), yearlyPriceFact(plan)])]);
  for (const [, price] of compliance.matchAll(/(\$[\d,]+)\/(?:month|year)/g)) assert.ok(prices.has(price), price);
  // The home page's words type no price, and the Terms point to the pricing page.
  for (const text of allCopy()) assert.doesNotMatch(text, /\$\s?\d/, text);
  assert.deepEqual(HOME.pricing.link, { label: "See every plan", href: "/pricing" });
  const terms = read("app/(marketing)/terms/page.tsx").replace(/\s+/g, " ");
  assert.doesNotMatch(terms, /\$\s?\d(?!\d*\))/, "the Terms name no plan price");
});
