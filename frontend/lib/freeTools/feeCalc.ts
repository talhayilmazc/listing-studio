/**
 * What one sale leaves. Pure arithmetic over lib/freeTools/fees.ts, in whole cents, each fee rounded to
 * the cent on its own line so the lines add up to the total the page shows. Etsy does not publish how
 * it rounds, so a real statement can differ by a cent.
 *
 * A figure with nothing behind it is blank, never zero: with no price there is no answer, and a shop not
 * in US dollars needs the rate for the fees Etsy sets in dollars before anything can be added up.
 */
import { LISTING_FEE_USD, OFFSITE_ADS, PROCESSING, TRANSACTION_PERCENT, regulatoryPercent, type Processing } from "./fees.ts";

export type OffsiteChoice = "none" | "under" | "over";

export type FeeInput = {
  place: string;
  price: number | null;
  shippingCharged: number | null;
  salesTax: number | null;
  baseCost: number | null;
  shippingCost: number | null;
  offsite: OffsiteChoice;
  /** What 1 USD is in the seller's currency. Needed only when that is not USD. */
  usdRate: number | null;
};

export type FeeLine = { id: "listing" | "transaction" | "processing" | "regulatory" | "offsite"; name: string; how: string; amount: number };

export type FeeResult = {
  currency: string;
  revenue: number;
  fees: FeeLine[];
  totalFees: number;
  costs: number;
  profit: number;
  /** Profit as a percent of what the buyer paid for item and shipping, one decimal. */
  margin: number | null;
};

export type FeeOutcome = { ok: true; result: FeeResult } | { ok: false; needs: string };

const cents = (n: number): number => Math.round(n * 100 + Number.EPSILON);
const money = (c: number): number => c / 100;
/** A percent of an amount in cents, to the cent. */
const percentOf = (amount: number, percent: number): number => Math.round((amount * Math.round(percent * 100)) / 10000);

export const placeOf = (name: string): Processing | undefined => PROCESSING.find((row) => row.place === name);

export function calculate(input: FeeInput): FeeOutcome {
  const place = placeOf(input.place);
  if (!place) return { ok: false, needs: "Choose where your bank account is." };
  if (input.price === null || !(input.price > 0)) return { ok: false, needs: "Enter your item's price." };
  const amounts = [input.shippingCharged, input.salesTax, input.baseCost, input.shippingCost];
  if (amounts.some((n) => n !== null && n < 0)) return { ok: false, needs: "Amounts cannot be below zero." };
  const usd = place.currency === "USD" ? 1 : input.usdRate;
  if (usd === null || !(usd > 0)) return { ok: false, needs: `Enter what 1 USD is in ${place.currency}: Etsy sets the listing fee and the Offsite Ads cap in US dollars.` };

  const revenue = cents(input.price) + cents(input.shippingCharged ?? 0);
  const withTax = revenue + cents(input.salesTax ?? 0);
  const regulatory = regulatoryPercent(place.place);

  const fees: FeeLine[] = [
    { id: "listing", name: "Listing fee", how: `$${LISTING_FEE_USD.toFixed(2)} USD`, amount: cents(LISTING_FEE_USD * usd) },
    { id: "transaction", name: "Transaction fee", how: `${TRANSACTION_PERCENT}% of item and shipping`, amount: percentOf(revenue, TRANSACTION_PERCENT) },
    { id: "processing", name: "Payment processing", how: `${place.percent}% + ${place.flat} ${place.currency}, on item, shipping and sales tax`, amount: percentOf(withTax, place.percent) + cents(place.flat) },
  ];
  if (regulatory > 0) fees.push({ id: "regulatory", name: "Regulatory operating fee", how: `${regulatory}% of item and shipping`, amount: percentOf(revenue, regulatory) });
  if (input.offsite !== "none") {
    const percent = OFFSITE_ADS[input.offsite];
    fees.push({ id: "offsite", name: "Offsite Ads", how: `${percent}% of the order, at most $${OFFSITE_ADS.capUsd} USD`, amount: Math.min(percentOf(revenue, percent), cents(OFFSITE_ADS.capUsd * usd)) });
  }

  const totalFees = fees.reduce((sum, fee) => sum + fee.amount, 0);
  const costs = cents(input.baseCost ?? 0) + cents(input.shippingCost ?? 0);
  const profit = revenue - totalFees - costs;
  return {
    ok: true,
    result: {
      currency: place.currency,
      revenue: money(revenue),
      fees: fees.map((fee) => ({ ...fee, amount: money(fee.amount) })),
      totalFees: money(totalFees),
      costs: money(costs),
      profit: money(profit),
      margin: Math.round((profit / revenue) * 1000) / 10,
    },
  };
}

/** A typed amount: blank is null, and so is anything that is not a number. */
export function parseAmount(text: string): number | null {
  const clean = text.trim().replace(",", ".");
  if (!clean) return null;
  const n = Number(clean);
  return Number.isFinite(n) ? n : null;
}

export const showMoney = (n: number, currency: string): string => `${n.toFixed(2)} ${currency}`;
