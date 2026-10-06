/**
 * What every number in Analytics means, in one place.
 *
 * The screens show `label` and use `text` as the tooltip. docs/analytics.md is
 * written from this file (`node scripts/analytics-doc.mjs`) and the tests fail
 * if the two differ, so the document and the tooltips can't drift apart.
 */

export interface Definition {
  label: string;
  /** The tooltip: what the number is and what it is made from. */
  text: string;
}

/** How a figure was arrived at. Every number carries one of these. */
export const BASIS: Record<"exact" | "calculated" | "estimated", Definition> = {
  exact: {
    label: "Exact",
    text: "Etsy's own figure, taken as it is: from your monthly statement, from Etsy's payment ledger for your shop, or from your shop's sales.",
  },
  calculated: {
    label: "Calculated",
    text: "Worked out by us from exact figures: a subtraction, an order's amount split between its items by price, or your own product cost times the items sold.",
  },
  estimated: {
    label: "Estimated",
    text: "Rests on an assumption, because the exact figure isn't available. The note beside it says which assumption.",
  },
};

/** Where a month's totals come from, best first. */
export const SOURCES: Record<"statement" | "ledger" | "sales" | "none", Definition> = {
  statement: { label: "From your statement", text: "The monthly statement you imported from Shop Manager. It is Etsy's final account of the month and the best source there is." },
  ledger: { label: "From Etsy's ledger", text: "Etsy's payment ledger for your shop, read through Etsy's API. It has the fees and ad charges but not refunds or fee credits. Import the month's statement for the full picture." },
  sales: { label: "From your sales only", text: "Only your shop's sales have been read for this month. Fees and refunds are blank until the statement is imported or Etsy's ledger is read." },
  none: { label: "Nothing yet", text: "Nothing has been read or imported for this month." },
};

export const SECTIONS: Record<string, Definition> = {
  revenue: { label: "Revenue", text: "What buyers paid you for items and shipping, after the sales tax Etsy collected for the state, less refunds." },
  etsy_fees: { label: "Etsy fees", text: "What Etsy charged for listing, selling and processing payment, including the tax on those fees and any fees Etsy gave back." },
  marketing: { label: "Advertising", text: "Etsy Ads, Offsite Ads and any other marketing Etsy billed in the month." },
  other: { label: "Shipping labels and other", text: "Shipping labels bought on Etsy, and any row of the statement we could not classify (listed on the import screen)." },
  your_costs: { label: "Your product costs", text: "What the items sold cost you to make and to have shipped, from the costs you entered per profile. Never assumed." },
};

export const LINES: Record<string, Definition> = {
  items: { label: "Items sold", text: "What buyers paid for the items themselves, after discounts and after sales tax. On a statement month: revenue less the shipping buyers paid." },
  shipping_paid: { label: "Shipping paid by buyers", text: "What buyers paid you for shipping, from each order in your shop's sales. It is income; what you paid to ship is under your product costs or shipping labels." },
  refunds: { label: "Refunds", text: "What you refunded to buyers, less the sales tax Etsy returned with it." },
  transaction_fee_items: { label: "Transaction fee on items", text: "Etsy's fee on the price of each item sold." },
  transaction_fee_shipping: { label: "Transaction fee on shipping", text: "Etsy's fee on the shipping the buyer paid." },
  processing_fee: { label: "Payment processing fee", text: "Etsy Payments' fee for taking the buyer's payment." },
  listing_fee: { label: "Listing fees", text: "The fee for listing an item and for each renewal, including the automatic renewal when it sells." },
  fee_credits: { label: "Fees returned by Etsy", text: "Fees Etsy credited back, for example on a refunded order." },
  other_fees: { label: "Other Etsy fees", text: "Any other fee on the statement, such as a currency conversion or subscription fee." },
  fee_taxes: { label: "Tax on Etsy's fees", text: "VAT or sales tax Etsy charged on its own fees, less any it credited back." },
  etsy_ads: { label: "Etsy Ads", text: "Charged by Etsy this month. A cost of the whole shop: Etsy does not say which listing a charge is for, so it is not given to listings, not even in proportion." },
  offsite_ads: { label: "Offsite Ads fees", text: "Etsy's fee on orders that came from its ads on other sites, less any credited back. Billed per order, so each listing's result includes its own." },
  other_marketing: { label: "Other marketing", text: "Any other marketing charge on the statement." },
  shipping_labels: { label: "Shipping labels", text: "Postage you bought through Etsy, less label refunds." },
  unrecognised: { label: "Not classified", text: "Rows of the statement that fit none of our rules. They are counted here so the total still matches Etsy's, and listed on the import screen." },
  product_cost: { label: "Production", text: "Your production cost per item, times the items sold. Only for profiles whose cost you entered." },
  provider_shipping: { label: "Provider's shipping", text: "What your print provider charges you to ship an item, times the items sold. If they charge less for extra items in one parcel, enter your average." },
};

export const TOTALS: Record<string, Definition> = {
  net_etsy: { label: "Profit before product cost", text: "Revenue less everything Etsy charged in the month. On a statement month it equals the statement's net amount to the cent." },
  profit: { label: "Profit", text: "Profit before product cost, less your production and provider shipping costs. Shown only when every item sold has a cost entered." },
  deposits: { label: "Paid out to your bank", text: "Transfers from Etsy to your bank in the month. A transfer is neither income nor a cost, so it is beside the account, not in it." },
  ads_charged: { label: "Charged by Etsy this month", text: "Etsy Ads as billed on the month's statement. Etsy bills each day's clicks on the next day, so this includes the last day of the month before and leaves out the month's own last day." },
  ads_clicks: { label: "Ad spend for clicks this month", text: "Etsy Ads for clicks made in the month, from the Ads report you imported. It differs from the amount charged by the one-day billing lag." },
  break_even_roas: { label: "Break-even ROAS", text: "The sales your ads must bring per 1 of ad spend for Etsy Ads to stop losing money, after Etsy's fees and your product cost. One divided by what a unit of sales leaves you before ads." },
  actual_roas: { label: "Your Etsy Ads ROAS", text: "Sales Etsy attributes to your ads divided by ad spend, both from the Ads report for the month. Above break-even, ads earn; below it, they cost more than they bring." },
  not_attributed: { label: "Not attributed to a listing", text: "Amounts on the statement that cannot be tied to a listing: orders the sales read does not have yet, and charges Etsy bills to the shop as a whole." },
  stake: { label: "At stake", text: "The money the item is about, for the month: a loss already made, sales that went missing, or sales whose cost is unknown." },
};

export const COLUMNS: Record<string, Definition> = {
  class: { label: "Class", text: "Where the listing stands this month. Each class shows its reason." },
  units: { label: "Sold", text: "Items sold in the month's orders." },
  revenue: { label: "Sales", text: "What buyers paid for the listing's items and its share of the shipping they paid, after sales tax. An order with several items is split between them by price, to the cent." },
  fees: { label: "Etsy fees", text: "The listing's share of each order's Etsy fees and tax on fees, its Offsite Ads fees, and its own listing and renewal fees. Needs the month's statement." },
  refunds: { label: "Refunds", text: "Refunds on the listing's orders, less the sales tax Etsy returned." },
  product_cost: { label: "Product cost", text: "Your production and provider shipping cost for the items sold, from the listing's profile. Blank until you enter it." },
  result: { label: "Profit before ads", text: "Sales less refunds, Etsy fees and your product cost. Before Etsy Ads, which is a cost of the whole shop. Without a product cost it is the result before product cost, and says so." },
  per_unit: { label: "Per item", text: "Profit before ads divided by the items sold: what one more sale is worth." },
  trend: { label: "Last 6 months", text: "Items sold in each of the last six months, oldest first." },
  views: { label: "Views", text: "Listing views on Etsy in the month, not search impressions. Read once a day, only for listings published with the app. Etsy's API has no impressions, search terms or traffic sources." },
  favorites: { label: "Favourites", text: "Times the listing was favourited in the month (listings published with the app)." },
  conversion: { label: "Conversion", text: "Orders ÷ listing views on Etsy in the month. Not a search click-through rate." },
};

export const CLASSES: Record<"winner" | "steady" | "fading" | "losing" | "new", Definition> = {
  winner: { label: "Winner", text: "In the top fifth of your listings by profit before ads, among those that sold at least 3 and made money." },
  steady: { label: "Steady", text: "Selling and not losing money, without a sharp change from the month before." },
  fading: { label: "Fading", text: "Sold half or less of the month before, when it sold at least 3 then." },
  losing: { label: "Losing money", text: "Its sales left less than they cost: after Etsy's fees and your product cost, or even before product cost." },
  new: { label: "New", text: "Listed fewer than 45 days before the end of the month: too early to judge." },
};

export function refundsParts(refunded: string, taxReturned: string): string {
  return `${refunded} refunded to buyers, of which ${taxReturned} was sales tax that Etsy returned.`;
}

/** The whole reference, as the document. */
/** The title style comparison's thresholds (backend pipeline/listing_traffic.py). */
export const STYLE_MIN_LISTINGS = 30;
export const STYLE_MIN_VIEWS = 1000;

export function document(): string {
  const out: string[] = [
    "# Analytics: what every number means",
    "",
    "Written from `frontend/lib/analyticsDefinitions.ts` (`node scripts/analytics-doc.mjs`). The same sentences are the tooltips on the screens; change them there, not here.",
    "",
    "## Where the numbers come from",
    "",
    "A month's totals come from the best source there is for that month, in this order:",
    "",
  ];
  for (const s of Object.values(SOURCES)) out.push(`- **${s.label}.** ${s.text}`);
  out.push(
    "",
    "Sales per listing come from your shop's own sales. A statement's orders are tied to listings by order number, never by title. When an order has several items, what belongs to the order as a whole is split between them by price, to the cent.",
    "",
    "Etsy Ads is a cost of the whole shop. It is never given to listings, not even in proportion, so every listing's result is before ads.",
    "",
    "A number with nothing behind it is left blank with the reason beside it. It is never shown as zero.",
    "",
    "## How each number is labelled",
    "",
  );
  for (const b of Object.values(BASIS)) out.push(`- **${b.label}.** ${b.text}`);
  const table = (title: string, items: Record<string, Definition>) => {
    out.push("", `## ${title}`, "", "| Number | What it is |", "|---|---|");
    for (const d of Object.values(items)) out.push(`| ${d.label} | ${d.text} |`);
  };
  table("The month's account, section by section", SECTIONS);
  table("Its lines", LINES);
  table("Totals, advertising and what is beside the account", TOTALS);
  table("The listings table", COLUMNS);
  table("Listing classes", CLASSES);
  out.push(
    "",
    "## Views, favourites and title styles",
    "",
    "For the listings published with the app, Etsy's lifetime views and favourites are read once a day and the day's increase is kept for 13 months (deleted with the shop). A listing's first reading is a starting point, not a day's views, unless it went live in the 48 hours before; a day that was not read is included in the next reading. The read costs one Etsy request per 100 listings per shop per day, and none for listings the shop sync already read that day.",
    "",
    "These are **listing views on Etsy, not search impressions**. Etsy's API has no impressions, no search terms and no traffic sources, so how often a listing appeared in search, for which searches, and where its visitors came from cannot be measured here. Conversion is orders ÷ views; it is not a search click-through rate.",
    "",
    `The title style comparison takes the listings published with the app in the same period, by the style their first published text was written in ("Etsy recommended (short)" or "Long keyword"), while that text was live. It shows views per listing per day, favourites per view and orders per view, each with its number of listings and a 95% interval clustered by listing. Below ${STYLE_MIN_LISTINGS} listings or ${STYLE_MIN_VIEWS.toLocaleString("en-US")} views for a style it says "not enough data yet". Etsy gives new listings a small temporary boost and shoppers' context varies, so the comparison is of listings published at the same time; it is a measurement, not a ranking promise, and nothing is ever rewritten from it.`,
  );
  out.push(
    "",
    "## Refunds",
    "",
    `Refunds are shown net. The tooltip gives both parts, for example: "${refundsParts("95.28", "6.26")}"`,
    "",
  );
  return out.join("\n");
}
