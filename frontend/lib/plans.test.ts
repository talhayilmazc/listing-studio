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
import { CUSTOM, INCLUDED, LISTING_LINE, PLANNED_NOTICE, PLANS, listingsFact, planLine, priceFact, schedulingFact, shopsFact } from "./plans.ts";
import { HOME, allCopy } from "./homeCopy.ts";

const FRONTEND = path.join(import.meta.dirname, "..");
const read = (relative: string) => fs.readFileSync(path.join(FRONTEND, relative), "utf8").replace(/\r\n/g, "\n");
const code = (source: string) => source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[\s;{}(),])\/\/[^\n]*/g, "$1");
const PRICING = "app/(marketing)/pricing/page.tsx";

test("the planned plans are the ones agreed", () => {
  assert.deepEqual(PLANS.map(planLine), [
    "Starter: 1,000 listings a month, 1 shop, publish now (no scheduling), $69/month",
    "Growth: 2,000 listings a month, 2 shops, publish now (no scheduling), $149/month",
    "Studio: 3,500 listings a month, 4 shops, scheduling, $299/month",
    "Catalog: 5,000 listings a month, 8 shops, scheduling, $399/month",
  ]);
  assert.equal(PLANNED_NOTICE, "Planned prices after launch. No payment is taken today. Prices may change before launch.");
  assert.equal(CUSTOM.body, "More listings or shops? Tell us what you need and we'll send you a quote.");
  // Each plan is larger than the one before it in everything that is counted.
  for (let i = 1; i < PLANS.length; i++) {
    assert.ok(PLANS[i].listings > PLANS[i - 1].listings && PLANS[i].shops > PLANS[i - 1].shops && PLANS[i].price > PLANS[i - 1].price);
  }
  // Everything else is in every plan, and a listing is a design counted once.
  for (const word of ["Profiles", "write-it-yourself", "Trademark", "Review and approve", "Size charts", "Profit"]) {
    assert.ok(INCLUDED.some((item) => item.includes(word)), word);
  }
  assert.match(LISTING_LINE, /^A listing is one design\. It is counted once, whichever of your shops it is listed in/);
});

test("the pricing page lays the plans out from the constants and types none of them", () => {
  const page = code(read(PRICING));
  assert.match(page, /import \{[^}]*\bPLANS\b[^}]*\} from "@\/lib\/plans";/);
  assert.match(page, /\{PLANS\.map\(\(plan\) => \(/);
  for (const piece of ["{plan.name}", "{priceFact(plan)}", "listingsFact(plan)", "shopsFact(plan)", "schedulingFact(plan)", "{CUSTOM.body}", "{LISTING_LINE}", "{INCLUDED.map("]) {
    assert.ok(page.includes(piece), piece);
  }
  // No plan name, price or count of its own.
  for (const plan of PLANS) {
    assert.ok(!page.includes(`"${plan.name}"`) && !page.includes(`>${plan.name}<`), plan.name);
    for (const fact of [priceFact(plan), listingsFact(plan), `${plan.price}`, plan.listings.toLocaleString("en-US")]) assert.ok(!page.includes(fact), fact);
  }
  assert.doesNotMatch(page, /\$\s?\d/);
  assert.doesNotMatch(page, /\b(Solo)\b|Priority support|Everything in (Solo|Studio)/, "the old feature lists are gone");
  // The labels a plan shows.
  assert.deepEqual(PLANS.map(schedulingFact), ["Publish now (no scheduling)", "Publish now (no scheduling)", "Scheduling", "Scheduling"]);
  assert.deepEqual(PLANS.map(shopsFact), ["1 shop", "2 shops", "4 shops", "8 shops"]);
});

test("the planned-prices notice is above the plans, and the early-access card comes first", () => {
  const page = code(read(PRICING));
  const notice = page.indexOf("{PLANNED_NOTICE}");
  const early = page.indexOf('data-testid="plan-early-access"');
  const plans = page.indexOf("{PLANS.map(");
  const custom = page.indexOf('data-testid="plan-custom"');
  assert.ok(notice !== -1 && notice < early && early < plans && plans < custom);
  assert.match(page, /Free during early access\./);
  assert.match(page, /<span className="[^"]*">Free<\/span>|>Free<\/p>/);
  // Each planned card says it is planned.
  assert.match(page, />Planned<\/span>/);
  assert.match(page, /a month, planned/);
});

test("the pricing page has no checkout and no payment form", () => {
  const page = code(read(PRICING));
  assert.doesNotMatch(page, /<form\b|<input\b|<select\b|<textarea\b|<button\b|type="submit"|onSubmit|"use client"/);
  // Every link on it leads to the invite form, with or without the quote request.
  const links = [...page.matchAll(/<Link href=(?:"([^"]+)"|\{([^}]+)\})/g)].map((m) => m[1] ?? m[2]);
  assert.deepEqual(links, ["/request-invite", "CUSTOM.cta.href"]);
  assert.equal(CUSTOM.cta.href, "/request-invite?plan=custom");
  assert.doesNotMatch(page, /<a\b/);
  assert.doesNotMatch(page, /stripe|paddle|paypal|lemon ?squeezy|braintree|iyzico|checkout\.|\/checkout|\/pay\b|\/billing|\/subscribe|buy now|add to cart|start (free )?trial|subscribe now/i);
  // Nor is there a payment library anywhere in the frontend.
  const pkg = JSON.parse(read("package.json"));
  const packages = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies }).join(" ");
  assert.doesNotMatch(packages, /stripe|paddle|paypal|braintree|lemonsqueezy|iyzi|chargebee|recurly/i);
  // The page says so itself.
  assert.match(read(PRICING).replace(/\s+/g, " "), /no payment of any kind is taken; there is no checkout on this site/);
});

test("a quote request asks for the listings a month and the number of shops", () => {
  const form = code(read("components/marketing/InviteForm.tsx"));
  assert.match(form, /get\("plan"\) === "custom"/);
  assert.match(form, /htmlFor="ir-listings" className="label">New listings a month</);
  assert.match(form, /htmlFor="ir-count" className="label">Number of shops</);
  assert.match(form, /const sent = custom \? quoteNote\(listings, note\) : note;/);
  assert.match(form, /"Custom plan: about \$\{Number\(listings\)\.toLocaleString\("en-US"\)\} new listings a month\."|`Custom plan: about \$\{Number\(listings\)\.toLocaleString\("en-US"\)\} new listings a month\.`/);
});

test("the application to Etsy lists the same plans, and the home page and the Terms name no price", () => {
  const application = fs.readFileSync(path.join(FRONTEND, "..", "docs", "commercial-application.md"), "utf8").replace(/\r\n/g, "\n");
  for (const plan of PLANS) assert.ok(application.includes(`- ${planLine(plan)}\n`), planLine(plan));
  assert.ok(application.includes("- Custom: more listings or shops, quoted on request\n"));
  assert.equal((application.match(/^- [A-Z][a-z]+: [\d,]+ listings a month/gm) ?? []).length, PLANS.length, "no plan in the document that is not in lib/plans.ts");
  assert.ok(application.includes("Prices will be published on listyro.com/pricing before any charge is made, and existing sellers will be told in advance."));
  assert.ok(application.includes("These plans need more than the default 5,000 requests a day: we ask Etsy for a higher daily limit together with Commercial Access, and will not sell a plan we cannot serve within our limits."));
  // No prices on the home page or in the Terms: both point to the pricing page.
  for (const text of allCopy()) assert.doesNotMatch(text, /\$\s?\d/, text);
  const cost = HOME.faq.items.find((item) => item.q === "What does it cost?")!;
  assert.match(cost.a, /The plans we intend to offer after launch are shown on the pricing page/);
  assert.deepEqual(cost.link, { label: "See pricing", href: "/pricing" });
  const terms = read("app/(marketing)/terms/page.tsx").replace(/\s+/g, " ");
  assert.doesNotMatch(terms, /\$\s?\d(?!\d*\))/, "the Terms name no plan price");
  assert.match(terms, /The plans we intend to offer after launch are shown on the\{" "\} <Link href="\/pricing"/);
});
