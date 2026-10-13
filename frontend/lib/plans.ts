/**
 * The plans we intend to offer after launch: the one place they are written.
 *
 * These are planned prices. Nothing here is charged or enforced: the app has no
 * payment code, and an account's allowance and shop limit are still set per
 * account in Admin. The pricing page lays these out, and the application to Etsy
 * (docs/commercial-application.md) lists the same lines; lib/plans.test.ts fails
 * if either drifts from this file, or if the pricing page grows a payment form.
 *
 * A plan's unit is "listings a month": listings written, the number the seller
 * sees in the app as "Listings generated" (core/allowance.py).
 */

export const PLANNED_NOTICE = "Planned prices after launch. No payment is taken today. Prices may change before launch.";

/** The top of the pricing page: an invitation, not an offer. */
export const PRICING_HEADING = "Request an invite to try Listyro";
export const PRICING_LINE = "Invite-only early access. No payment is taken today; the plans below apply after launch.";

/** What a "listing" is, as the allowance counts it (core/allowance.py; FREE_REWRITES = 3). */
export const FREE_REWRITES = 3;
export const LISTING_LINE =
  "A listing is one design written by AI or by you, with all its photos. Rewriting the same design is free up to 3 times. Photos per listing and the number of drafts in your shops do not count.";

export type Plan = {
  name: string;
  /** New listings written in a month. */
  listings: number;
  shops: number;
  /** false: a draft is published when you press Publish; true: also at a time you choose. */
  scheduling: boolean;
  /** US dollars a month, paid monthly. */
  price: number;
};

export const PLANS: Plan[] = [
  { name: "Starter", listings: 100, shops: 1, scheduling: false, price: 69 },
  { name: "Growth", listings: 200, shops: 2, scheduling: false, price: 149 },
  { name: "Studio", listings: 350, shops: 4, scheduling: true, price: 299 },
  { name: "Catalog", listings: 500, shops: 8, scheduling: true, price: 399 },
];

/** Paid yearly, a plan costs ten months. */
export const YEARLY_MONTHS = 10;
export const YEARLY_NOTE = "Pay for 10 months, get 12";

/** In every plan, whatever its size. */
export const INCLUDED = [
  "Profiles from your own listings",
  "AI-suggested or write-it-yourself text",
  "Trademark checks",
  "Review and approve",
  "Size charts",
  "Profit, cost and ads report",
];

export const CUSTOM = {
  name: "Custom",
  body: "More listings or shops? Tell us what you need and we'll send you a quote.",
  cta: { label: "Ask for a quote", href: "/request-invite?plan=custom" },
};

const count = (n: number) => n.toLocaleString("en-US");
/** "$57.50", "$124.17", "$69": cents only when there are any. */
const dollars = (n: number) => "$" + (Number.isInteger(n) ? count(n) : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export const listingsFact = (plan: Plan) => `${count(plan.listings)} listings a month`;
export const shopsFact = (plan: Plan) => `${plan.shops} ${plan.shops === 1 ? "shop" : "shops"}`;
export const schedulingFact = (plan: Plan) => (plan.scheduling ? "Scheduling" : "Publish now (no scheduling)");

export const yearlyPrice = (plan: Plan) => plan.price * YEARLY_MONTHS;
export const priceFact = (plan: Plan) => dollars(plan.price);
export const yearlyPriceFact = (plan: Plan) => dollars(yearlyPrice(plan));
/** What a year's price comes to a month, to the cent. */
export const yearlyPerMonthFact = (plan: Plan) => `${dollars(Math.round((yearlyPrice(plan) / 12) * 100) / 100)} a month`;

/** A plan in one line, as the application to Etsy lists it. */
export function planLine(plan: Plan): string {
  const scheduling = plan.scheduling ? "scheduling" : "publish now (no scheduling)";
  return `${plan.name}: ${listingsFact(plan)}, ${shopsFact(plan)}, ${scheduling}, ${priceFact(plan)}/month, ${yearlyPriceFact(plan)}/year`;
}
