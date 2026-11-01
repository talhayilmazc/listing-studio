/**
 * Every fee the calculator uses: one dated file, a source page per fee.
 *
 * The figures were read on Etsy's Help pages on FEES_CHECKED, and each quote below is word for word
 * (docs/guide-sources.md). Etsy's legal fee page (https://www.etsy.com/legal/fees) answers automated
 * requests with 403, so a person still has to compare them with it: docs/free-tools.md lists each one.
 * Until then every fee is "to confirm" and the page says "coming soon" (FEE_CALCULATOR_CONFIRMED).
 *
 * To confirm: correct any figure here, set each status to "confirmed", set FEES_CHECKED to that day,
 * and set FEE_CALCULATOR_CONFIRMED=true in .env for the next build.
 */
import type { Source } from "../marketing/content.ts";
import { READ } from "../marketing/sources.ts";

export const FEES_CHECKED = "2026-11-01";
export type FeeStatus = "to confirm" | "confirmed";

/** The page's one switch. It is read when the site is built: static pages have nothing else to ask. */
export const feeCalculatorOpen = (flag: string | undefined = process.env.FEE_CALCULATOR_CONFIRMED): boolean => flag === "true";

export type Fee = { id: string; name: string; quote: string; source: Source; status: FeeStatus };

export const LISTING_FEE_USD = 0.2;
export const TRANSACTION_PERCENT = 6.5;
export const OFFSITE_ADS = { under: 15, over: 12, capUsd: 100, thresholdUsd: 10000 };

export const FEES: Fee[] = [
  { id: "listing", name: "Listing fee", quote: "The listing fee is a flat rate charge of $0.20 USD per listing.", source: READ.feeBasics, status: "to confirm" },
  { id: "transaction", name: "Transaction fee", quote: "This fee applies to the total cost of the item, along with shipping and gift wrap (if you charge your customers for those).", source: READ.feeBasics, status: "to confirm" },
  { id: "processing", name: "Payment processing", quote: "The fees are taken from the item's total sale price, including its shipping fees, and any applicable sales tax.", source: READ.paymentFees, status: "to confirm" },
  { id: "regulatory", name: "Regulatory operating fee", quote: "Sellers in these countries will be charged the following Regulatory Operating fees, which are charged on the item price plus shipping cost and gift wrap:", source: READ.regulatoryFee, status: "to confirm" },
  { id: "offsite", name: "Offsite Ads", quote: "The Offsite Ads fee for any individual order will never exceed $100 USD, regardless of the order total.", source: READ.offsiteAds, status: "to confirm" },
];

export const NOT_INCLUDED = [
  "currency conversion (Etsy charges 2.5% when it is needed)",
  "VAT on Etsy's fees",
  "Etsy Ads",
  "Etsy shipping labels",
  "deposit fees in some countries",
];
export const NOT_INCLUDED_SOURCES = [READ.feesAndTaxes, READ.paymentFees];

/** A row of Etsy's table: where your bank account is, percent, flat fee and its currency. */
export type Processing = { place: string; percent: number; flat: number; currency: string };

const row = (place: string, percent: number, flat: number, currency: string): Processing => ({ place, percent, flat, currency });

/** "Fees for Etsy Payments (% of total sale price + flat fee per order)", in the order of Etsy's table. */
export const PROCESSING: Processing[] = [
  row("Argentina", 6.5, 0.3, "USD"),
  row("Australia (domestic orders)", 3, 0.25, "AUD"),
  row("Australia (international orders)", 4, 0.25, "AUD"),
  row("Austria", 4, 0.3, "EUR"),
  row("Belgium", 4, 0.3, "EUR"),
  row("Brazil", 6.5, 0.3, "USD"),
  row("Bulgaria", 4, 0.3, "EUR"),
  row("Canada (domestic orders or orders from the US)", 3, 0.25, "CAD"),
  row("Canada (international orders)", 4, 0.25, "CAD"),
  row("Chile", 6.5, 0.3, "USD"),
  row("China", 6.5, 0.3, "USD"),
  row("Croatia", 4, 0.3, "EUR"),
  row("Cyprus", 4, 0.3, "EUR"),
  row("Czech Republic", 4, 0.3, "EUR"),
  row("Denmark", 4, 2.5, "DKK"),
  row("Egypt", 6.5, 0.3, "USD"),
  row("Estonia", 4, 0.3, "EUR"),
  row("Finland", 4, 0.3, "EUR"),
  row("France", 4, 0.3, "EUR"),
  row("Georgia", 6.5, 0.3, "USD"),
  row("Germany", 4, 0.3, "EUR"),
  row("Greece", 4, 0.3, "EUR"),
  row("Hong Kong", 4.4, 2, "HKD"),
  row("Hungary", 4, 0.3, "EUR"),
  row("India", 5, 25, "INR"),
  row("Indonesia", 4.5, 7000, "IDR"),
  row("Ireland", 4, 0.3, "EUR"),
  row("Israel", 4.5, 2, "ILS"),
  row("Italy", 4, 0.3, "EUR"),
  row("Japan", 6, 0.3, "USD"),
  row("Kazakhstan", 6.5, 0.3, "USD"),
  row("Latvia", 4, 0.3, "EUR"),
  row("Lithuania", 4, 0.3, "EUR"),
  row("Luxembourg", 4, 0.3, "EUR"),
  row("Malaysia", 4.5, 2, "MYR"),
  row("Malta", 4, 0.3, "EUR"),
  row("Mexico", 4.5, 8, "MXN"),
  row("Morocco", 4.5, 5, "MAD"),
  row("Netherlands", 4, 0.3, "EUR"),
  row("New Zealand (domestic orders)", 3, 0.3, "NZD"),
  row("New Zealand (international orders)", 4, 0.3, "NZD"),
  row("Norway", 4, 2.5, "NOK"),
  row("Pakistan", 6.5, 0.3, "USD"),
  row("Peru", 6.5, 0.3, "USD"),
  row("Philippines", 4.5, 25, "PHP"),
  row("Poland", 4, 0.3, "EUR"),
  row("Portugal", 4, 0.3, "EUR"),
  row("Romania", 4, 0.3, "EUR"),
  row("Serbia", 6.5, 0.3, "USD"),
  row("Singapore", 4.4, 0.35, "SGD"),
  row("Slovakia", 4, 0.3, "EUR"),
  row("Slovenia", 4, 0.3, "EUR"),
  row("South Africa", 4.5, 8, "ZAR"),
  row("South Korea", 6.5, 0.3, "USD"),
  row("Spain", 4, 0.3, "EUR"),
  row("Sweden", 4, 3, "SEK"),
  row("Switzerland", 4, 0.5, "CHF"),
  row("Thailand", 6, 0.3, "USD"),
  row("Türkiye", 6.5, 14, "TRY"),
  row("Ukraine", 6, 0.3, "USD"),
  row("United Arab Emirates", 6.5, 0.3, "USD"),
  row("United Kingdom", 4, 0.2, "GBP"),
  row("United States", 3, 0.25, "USD"),
  row("Vietnam", 4.5, 11500, "VND"),
];

/** Etsy's table of Regulatory Operating fees, percent. Countries not here pay none. */
export const REGULATORY: Record<string, number> = {
  Canada: 0.5,
  France: 1.14,
  Hungary: 1.97,
  Italy: 0.8,
  India: 0.05,
  Spain: 0.88,
  "Türkiye": 1.67,
  "United Kingdom": 0.48,
  Vietnam: 1.24,
};

/** "Canada (international orders)" pays Canada's regulatory fee. */
export const regulatoryPercent = (place: string): number => REGULATORY[place.replace(/ \(.*\)$/, "")] ?? 0;

export const DEFAULT_PLACE = "United States";
