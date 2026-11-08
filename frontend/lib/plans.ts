/**
 * The plans we intend to offer at launch: the one place they are written.
 *
 * These are launch prices for display. Nothing here is charged or enforced: the app has no
 * payment code, and an account's allowance and shop limit are still set per account in Admin.
 * The pricing page and the home page's teaser lay these out, and the dated note in the application
 * to Etsy (docs/commercial-application.md) and docs/etsy-compliance.md list the same lines;
 * lib/plans.test.ts fails if either drifts from this file, or if the pricing page grows a payment form.
 *
 * A plan's unit is "AI listings a month": listings written by AI. Listings the seller writes by hand
 * and sending a listing to more shops do not count. (Today's allowance in the app, core/allowance.py,
 * still counts a hand-written listing; it changes when billing does.)
 */
import { BILLING_ON } from "./marketing/cta.ts";

/** Beside every price while billing is off. */
export const PLANNED_NOTICE = BILLING_ON
  ? "Prices in US dollars. Change or cancel your plan in Settings."
  : "Launch prices. Nothing is charged yet: billing starts after launch, and we will tell you before it does.";

/** The top of the pricing page. */
export const PRICING_HEADING = "Pay for the listings Listyro writes";
export const PRICING_LINE = BILLING_ON
  ? "Every plan has every feature. Plans differ in AI listings a month and the number of shops."
  : "Invite-only early access. No payment is taken today; these plans start after launch.";

/** What a "listing" is, as the plans count it (FREE_REWRITES as in core/allowance.py). */
export const FREE_REWRITES = 3;
export const LISTING_LINE =
  "Only AI-written listings count. A listing is one design written by AI, with all its photos, and rewriting the same design is free up to 3 times. Listings you write yourself, photos, and sending a listing to more shops do not count.";

export type Plan = {
  name: string;
  /** AI-written listings a month. */
  listings: number;
  shops: number;
  /** US dollars a month, paid monthly. */
  price: number;
  /** The plan the page points to first. */
  highlight?: boolean;
};

export const PLANS: Plan[] = [
  { name: "Starter", listings: 150, shops: 1, price: 19 },
  { name: "Growth", listings: 300, shops: 3, price: 29, highlight: true },
  { name: "Pro", listings: 600, shops: 5, price: 49 },
  { name: "Business", listings: 1000, shops: 10, price: 69 },
];

/** More AI listings in a month, on any plan. */
export const EXTRA_PACK = { name: "Extra pack", listings: 100, price: 9, line: "Add it on any plan, in a month that needs more." };

/**
 * The free trial, as the site states it everywhere (hero, closing, pricing, Help, sign-up): free AI
 * listings granted only after the account connects an Etsy shop that has never had a trial, once per Etsy
 * shop and per Etsy account, for life; none before a shop is connected. This is the wording only: the
 * grant itself comes with billing, and while billing is off nothing changes for any account.
 */
export const TRIAL = {
  listings: 10,
  line: "10 free AI listings once you connect an Etsy shop that has never had a trial. One trial per Etsy shop and per Etsy account, for life. No free listings before a shop is connected.",
};

/** Paid yearly, a plan costs ten months. */
export const YEARLY_MONTHS = 10;
export const YEARLY_NOTE = "Pay for 10 months, get 12";
/** The yearly half of the Monthly / Yearly switch: "Yearly, 2 months free". */
export const YEARLY_LABEL = `Yearly, ${12 - YEARLY_MONTHS} months free`;

/** In every plan, whatever its size. */
export const INCLUDED = [
  "Mockups on your own garment photos",
  "AI-written or write-it-yourself text",
  "Profiles from your own listings",
  "Each shop's own photos, SKU, text and attributes",
  "Scheduling",
  "Trademark checks",
  "Profit, cost and ads report",
];

export const SPECIAL = {
  name: "Special",
  body: "More than 1,000 AI listings a month? Tell us what you need and we'll quote a price.",
  price: "Custom",
  unit: "For big catalogs",
  listings: "1,000+ listings",
  shops: "Unlimited shops",
  cta: { label: "Talk to us", href: "/request-invite?plan=custom" },
};

const count = (n: number) => n.toLocaleString("en-US");
/** "$15.83", "$190": cents only when there are any. */
const dollars = (n: number) => "$" + (Number.isInteger(n) ? count(n) : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export const listingsFact = (plan: Plan) => `${count(plan.listings)} AI listings a month`;
export const shopsFact = (plan: Plan) => `${plan.shops} ${plan.shops === 1 ? "shop" : "shops"}`;

export const yearlyPrice = (plan: Plan) => plan.price * YEARLY_MONTHS;
export const priceFact = (plan: Plan) => dollars(plan.price);
export const yearlyPriceFact = (plan: Plan) => dollars(yearlyPrice(plan));
/** What a year's price comes to a month, to the cent. */
export const yearlyPerMonthFact = (plan: Plan) => `${dollars(Math.round((yearlyPrice(plan) / 12) * 100) / 100)} a month`;

/** What one AI listing costs on a plan, in cents to one decimal: "12.7¢ a listing" (a year counts twelve months). */
export const centsPerListing = (plan: Plan, yearly = false) =>
  Math.round(((yearly ? yearlyPrice(plan) / 12 : plan.price) / plan.listings) * 1000) / 10;
export const perListingFact = (plan: Plan, yearly = false) => `${centsPerListing(plan, yearly).toFixed(1)}¢ a listing`;
/** The pricing heading, from the plan the page points to first: "About 10 cents a listing." */
export const aboutPerListing = () => {
  const plan = PLANS.find((p) => p.highlight) ?? PLANS[0];
  return `About ${Math.round(centsPerListing(plan))} cents a listing.`;
};
/** "150 AI listings", as a plan card lists it. */
export const cardListingsFact = (plan: Plan) => `${count(plan.listings)} AI listings`;

/** "Extra pack: +100 AI listings for $9". */
export const extraPackFact = () => `${EXTRA_PACK.name}: +${count(EXTRA_PACK.listings)} AI listings for ${dollars(EXTRA_PACK.price)}`;

/** A plan in one line, as the documents list it. */
export function planLine(plan: Plan): string {
  return `${plan.name}: ${listingsFact(plan)}, ${shopsFact(plan)}, ${priceFact(plan)}/month, ${yearlyPriceFact(plan)}/year`;
}

/** The pack in one line, as the documents list it. */
export const packLine = () => `${extraPackFact()}, on any plan`;
