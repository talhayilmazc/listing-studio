// The planned plans: written once, shown as plans and nothing more.
//
// The pricing page shows what we intend to charge after launch. It must never be
// mistaken for a shop window: no payment is taken before Etsy's Commercial Access,
// so the page has no form and no payment link, it says so at the top, and every
// number on it, and in the application to Etsy, comes from lib/plans.ts.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import {
  CUSTOM, FREE_REWRITES, INCLUDED, LISTING_LINE, PLANNED_NOTICE, PLANS, PRICING_HEADING, PRICING_LINE, YEARLY_MONTHS, YEARLY_NOTE,
  listingsFact, planLine, priceFact, schedulingFact, shopsFact, yearlyPerMonthFact, yearlyPrice, yearlyPriceFact,
} from "./plans.ts";
import { HOME, allCopy, headings } from "./homeCopy.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1");
const PRICING = "app/(marketing)/pricing/page.tsx";
const CARDS = "components/marketing/PlanCards.tsx";

test("the planned plans are the ones agreed", () => {
  assert.deepEqual(PLANS.map(planLine), [
    "Starter: 100 listings a month, 1 shop, publish now (no scheduling), $69/month, $690/year",
    "Growth: 200 listings a month, 2 shops, publish now (no scheduling), $149/month, $1,490/year",
    "Studio: 350 listings a month, 4 shops, scheduling, $299/month, $2,990/year",
    "Catalog: 500 listings a month, 8 shops, scheduling, $399/month, $3,990/year",
  ]);
  assert.equal(PLANNED_NOTICE, "Planned prices after launch. No payment is taken today. Prices may change before launch.");
  assert.equal(CUSTOM.body, "More listings or shops? Tell us what you need and we'll send you a quote.");
  // Each plan is larger than the one before it in everything that is counted.
  for (let i = 1; i < PLANS.length; i++) {
    assert.ok(PLANS[i].listings > PLANS[i - 1].listings && PLANS[i].shops > PLANS[i - 1].shops && PLANS[i].price > PLANS[i - 1].price);
  }
  for (const word of ["Profiles", "write-it-yourself", "Trademark", "Review and approve", "Size charts", "Profit"]) {
    assert.ok(INCLUDED.some((item) => item.includes(word)), word);
  }
});

test("a year costs ten months: yearly = 10 x monthly, and the monthly equivalent is that over twelve", () => {
  assert.equal(YEARLY_MONTHS, 10);
  assert.equal(YEARLY_NOTE, "Pay for 10 months, get 12");
  for (const plan of PLANS) {
    assert.equal(yearlyPrice(plan), plan.price * 10, plan.name);
    assert.equal(yearlyPriceFact(plan), "$" + (plan.price * 10).toLocaleString("en-US"));
    const perMonth = Math.round(((plan.price * 10) / 12) * 100) / 100;
    assert.equal(yearlyPerMonthFact(plan), `$${Number.isInteger(perMonth) ? perMonth : perMonth.toFixed(2)} a month`, plan.name);
    assert.ok(perMonth < plan.price);
  }
  assert.deepEqual(PLANS.map(yearlyPriceFact), ["$690", "$1,490", "$2,990", "$3,990"]);
  assert.deepEqual(PLANS.map(yearlyPerMonthFact), ["$57.50 a month", "$124.17 a month", "$249.17 a month", "$332.50 a month"]);
});

test("a listing is defined once, as the allowance counts it", () => {
  assert.equal(
    LISTING_LINE,
    "A listing is one design written by AI or by you, with all its photos. Rewriting the same design is free up to 3 times. Photos per listing and the number of drafts in your shops do not count.",
  );
  // The number in that sentence is the one the server uses (core/allowance.py).
  assert.equal(FREE_REWRITES, 3);
  const server = fs.readFileSync(path.join(FRONTEND, "..", "backend", "app", "core", "allowance.py"), "utf8");
  assert.match(server, /^FREE_REWRITES = 3$/m);
  assert.ok(code(read(PRICING)).includes("{LISTING_LINE}"));
});

test("the pricing page lays the plans out from the constants and types none of them", () => {
  const page = code(read(PRICING));
  const cards = code(read(CARDS));
  assert.match(page, /<PlanCards \/>/);
  assert.match(cards, /import \{[^}]*\bPLANS\b[^}]*\} from "@\/lib\/plans";/);
  assert.match(cards, /\{PLANS\.map\(\(plan\) => \(/);
  for (const piece of ["{plan.name}", "yearly ? yearlyPriceFact(plan) : priceFact(plan)", "listingsFact(plan)", "shopsFact(plan)", "schedulingFact(plan)", "{CUSTOM.body}", "YEARLY_NOTE", "yearlyPerMonthFact(plan)"]) {
    assert.ok(cards.includes(piece), piece);
  }
  for (const piece of ["{PRICING_HEADING}", "{PRICING_LINE}", "{PLANNED_NOTICE}", "{LISTING_LINE}", "{INCLUDED.map("]) assert.ok(page.includes(piece), piece);
  // No plan name, price or count of their own.
  for (const source of [page, cards]) {
    for (const plan of PLANS) {
      assert.ok(!source.includes(`"${plan.name}"`) && !source.includes(`>${plan.name}<`), plan.name);
      for (const fact of [priceFact(plan), yearlyPriceFact(plan), listingsFact(plan)]) assert.ok(!source.includes(fact), fact);
    }
    assert.doesNotMatch(source, /\$\s?\d/);
  }
  assert.doesNotMatch(page + cards, /\bSolo\b|Priority support|Everything in (Solo|Studio)/, "the old feature lists are gone");
  assert.deepEqual(PLANS.map(schedulingFact), ["Publish now (no scheduling)", "Publish now (no scheduling)", "Scheduling", "Scheduling"]);
  assert.deepEqual(PLANS.map(shopsFact), ["1 shop", "2 shops", "4 shops", "8 shops"]);
});

test("the page opens with the invitation, then the planned-prices notice, then the plans", () => {
  assert.equal(PRICING_HEADING, "Request an invite to try Listyro");
  assert.equal(PRICING_LINE, "Invite-only early access. No payment is taken today; the plans below apply after launch.");
  const page = code(read(PRICING));
  const order = ["<h1", "{PRICING_HEADING}", 'href="/request-invite"', "{PRICING_LINE}", "{PLANNED_NOTICE}", "<PlanCards />", "{LISTING_LINE}"].map((piece) => page.indexOf(piece));
  assert.ok(order.every((at) => at !== -1), String(order));
  assert.deepEqual(order, [...order].sort((a, b) => a - b));
  assert.match(page, /<Link href="\/request-invite"[^>]*>Request an invite<\/Link>/);
  // The early-access card is gone: nothing on the page is offered at a price of nothing.
  assert.doesNotMatch(page + code(read(CARDS)), /plan-early-access|Early access<|>Now</);
  // Each planned card says it is planned, in both views of the switch.
  const cards = code(read(CARDS));
  assert.match(cards, />Planned<\/span>/);
  assert.match(cards, /yearly \? "a year, planned" : "a month, planned"/);
});

test('"free" is not a headline or a badge on the pricing page or the home page', () => {
  const page = read(PRICING);
  const cards = read(CARDS);
  for (const source of [page, cards]) {
    // Headings, and the small tags that sit on a card.
    for (const [, heading] of source.matchAll(/<h[1-3][^>]*>([\s\S]*?)<\/h[1-3]>/g)) assert.doesNotMatch(heading, /\bfree\b/i, heading);
    for (const [, tag] of source.matchAll(/<span className="[^"]*rounded-full[^"]*">([^<{]*)<\/span>/g)) assert.doesNotMatch(tag, /\bfree\b/i, tag);
    assert.doesNotMatch(code(source), />\s*Free\b/, "no line that opens with Free");
  }
  assert.doesNotMatch(PRICING_HEADING + PLANNED_NOTICE + PRICING_LINE, /\bfree\b/i);
  // The home page: its headings, the line under the hero's buttons, and the hero walkthrough's labels.
  for (const heading of headings()) assert.doesNotMatch(heading, /\bfree\b/i, heading);
  assert.equal(HOME.hero.note, "Invite-only early access. No payment is taken today.");
  for (const text of [HOME.hero.eyebrow, HOME.hero.body, HOME.closing.body, ...Object.values(HOME.demo.pills), HOME.demo.label]) assert.doesNotMatch(text, /\bfree\b/i, text);
  // That no payment is taken is still said plainly, in each place a visitor looks for it.
  assert.match(PRICING_LINE, /No payment is taken today/);
  assert.match(PLANNED_NOTICE, /No payment is taken today/);
  assert.match(HOME.faq.items.find((item) => item.q === "What does it cost?")!.a, /no payment of any kind is taken/);
});

test("the pricing page has no checkout and no payment form", () => {
  const page = code(read(PRICING));
  const cards = code(read(CARDS));
  for (const source of [page, cards]) {
    assert.doesNotMatch(source, /<form\b|<input\b|<select\b|<textarea\b|type="submit"|onSubmit|action=|method=/);
    assert.doesNotMatch(source, /<a\b/);
    assert.doesNotMatch(source, /stripe|paddle|paypal|lemon ?squeezy|braintree|iyzico|checkout\.|\/checkout|\/pay\b|\/billing|\/subscribe|buy now|add to cart|start (free )?trial|subscribe now|fetch\(|api\./i);
  }
  // The only buttons are the two halves of the Monthly / Yearly switch: plain state, nothing sent anywhere.
  assert.doesNotMatch(page, /<button\b|"use client"|useState/);
  assert.equal((cards.match(/<button /g) ?? []).length, 2);
  assert.equal((cards.match(/<button type="button" aria-pressed=\{!?yearly\} onClick=\{\(\) => setYearly\((?:true|false)\)\}/g) ?? []).length, 2);
  assert.match(cards, /const \[yearly, setYearly\] = useState\(false\);/, "Monthly is what is shown first");
  // Every link leads to the invite form, with or without the quote request.
  const links = [...(page + cards).matchAll(/<Link href=(?:"([^"]+)"|\{([^}]+)\})/g)].map((m) => m[1] ?? m[2]);
  assert.deepEqual(links, ["/request-invite", "CUSTOM.cta.href"]);
  assert.equal(CUSTOM.cta.href, "/request-invite?plan=custom");
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

test("the application to Etsy lists the same plans, and the home page and the Terms name no price", () => {
  const application = fs.readFileSync(path.join(FRONTEND, "..", "docs", "commercial-application.md"), "utf8").replace(/\r\n/g, "\n");
  for (const plan of PLANS) assert.ok(application.includes(`- ${planLine(plan)}\n`), planLine(plan));
  assert.ok(application.includes("- Custom: more listings or shops, quoted on request\n"));
  assert.equal((application.match(/^- [A-Z][a-z]+: [\d,]+ listings a month/gm) ?? []).length, PLANS.length, "no plan in the document that is not in lib/plans.ts");
  assert.ok(application.includes("Prices will be published on listyro.com/pricing before any charge is made, and existing sellers will be told in advance."));
  // The capacity the largest plan needs, worked out from the plan itself.
  const largest = PLANS[PLANS.length - 1];
  assert.equal(largest.listings, 500);
  assert.ok(Math.abs((largest.listings * 35) / 30 - 600) < 30, "about 600 calls a day at about 35 per listing");
  assert.ok(application.includes(
    "At about 35 API calls per listing, our largest planned plan (500 listings a month) needs about 600 calls a day per seller, so today's 5,000-a-day limit serves several sellers on every plan. We ask for a higher limit to grow, not to run a single seller.",
  ));
  assert.doesNotMatch(application, /need more than the default 5,000|well above 5,000/);
  const compliance = fs.readFileSync(path.join(FRONTEND, "..", "docs", "etsy-compliance.md"), "utf8");
  assert.ok(compliance.includes("our largest planned plan (500 listings a month) needs about 600 calls a day per seller"));
  assert.ok(compliance.includes("about 33 to 40 requests for a listing with 13 photos"));
  // No prices on the home page or in the Terms: both point to the pricing page.
  for (const text of allCopy()) assert.doesNotMatch(text, /\$\s?\d/, text);
  const cost = HOME.faq.items.find((item) => item.q === "What does it cost?")!;
  assert.match(cost.a, /The plans we intend to offer after launch are shown on the pricing page/);
  assert.deepEqual(cost.link, { label: "See pricing", href: "/pricing" });
  const terms = read("app/(marketing)/terms/page.tsx").replace(/\s+/g, " ");
  assert.doesNotMatch(terms, /\$\s?\d(?!\d*\))/, "the Terms name no plan price");
  assert.match(terms, /The plans we intend to offer after launch are shown on the\{" "\} <Link href="\/pricing"/);
});
