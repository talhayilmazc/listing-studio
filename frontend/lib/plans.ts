/**
 * The plans we intend to offer after launch: the one place they are written.
 *
 * These are planned prices. Nothing here is charged or enforced: the app has no
 * payment code, and an account's allowance and shop limit are still set per
 * account in Admin. The pricing page lays these out, and the application to Etsy
 * (docs/commercial-application.md) lists the same lines; lib/plans.test.ts fails
 * if either drifts from this file, or if the pricing page grows a payment form.
 */

export const PLANNED_NOTICE = "Planned prices after launch. No payment is taken today. Prices may change before launch.";

/** What a "listing" is, as the allowance counts it today (core/allowance.py). */
export const LISTING_LINE =
  "A listing is one design. It is counted once, whichever of your shops it is listed in; having its text written again counts again.";

export type Plan = {
  name: string;
  /** New listings prepared in a month. */
  listings: number;
  shops: number;
  /** false: a draft is published when you press Publish; true: also at a time you choose. */
  scheduling: boolean;
  /** US dollars a month. */
  price: number;
};

export const PLANS: Plan[] = [
  { name: "Starter", listings: 1000, shops: 1, scheduling: false, price: 69 },
  { name: "Growth", listings: 2000, shops: 2, scheduling: false, price: 149 },
  { name: "Studio", listings: 3500, shops: 4, scheduling: true, price: 299 },
  { name: "Catalog", listings: 5000, shops: 8, scheduling: true, price: 399 },
];

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

export const listingsFact = (plan: Plan) => `${count(plan.listings)} listings a month`;
export const shopsFact = (plan: Plan) => `${plan.shops} ${plan.shops === 1 ? "shop" : "shops"}`;
export const schedulingFact = (plan: Plan) => (plan.scheduling ? "Scheduling" : "Publish now (no scheduling)");
export const priceFact = (plan: Plan) => `$${count(plan.price)}`;

/** A plan in one line, as the application to Etsy lists it. */
export function planLine(plan: Plan): string {
  const scheduling = plan.scheduling ? "scheduling" : "publish now (no scheduling)";
  return `${plan.name}: ${listingsFact(plan)}, ${shopsFact(plan)}, ${scheduling}, ${priceFact(plan)}/month`;
}
