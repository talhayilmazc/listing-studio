# Analytics: what every number means

Written from `frontend/lib/analyticsDefinitions.ts` (`node scripts/analytics-doc.mjs`). The same sentences are the tooltips on the screens; change them there, not here.

## Where the numbers come from

A month's totals come from the best source there is for that month, in this order:

- **From your statement.** The monthly statement you imported from Shop Manager. It is Etsy's final account of the month and the best source there is.
- **From Etsy's ledger.** Etsy's payment ledger for your shop, read through Etsy's API. It has the fees and ad charges but not refunds or fee credits. Import the month's statement for the full picture.
- **From your sales only.** Only your shop's sales have been read for this month. Fees and refunds are blank until the statement is imported or Etsy's ledger is read.
- **Nothing yet.** Nothing has been read or imported for this month.

Sales per listing come from your shop's own sales. A statement's orders are tied to listings by order number, never by title. When an order has several items, what belongs to the order as a whole is split between them by price, to the cent.

Etsy Ads is a cost of the whole shop. It is never given to listings, not even in proportion, so every listing's result is before ads.

A number with nothing behind it is left blank with the reason beside it. It is never shown as zero.

## How each number is labelled

- **Exact.** Etsy's own figure, taken as it is: from your monthly statement, from Etsy's payment ledger for your shop, or from your shop's sales.
- **Calculated.** Worked out by us from exact figures: a subtraction, an order's amount split between its items by price, or your own product cost times the items sold.
- **Estimated.** Rests on an assumption, because the exact figure isn't available. The note beside it says which assumption.

## The month's account, section by section

| Number | What it is |
|---|---|
| Revenue | What buyers paid you for items and shipping, after the sales tax Etsy collected for the state, less refunds. |
| Etsy fees | What Etsy charged for listing, selling and processing payment, including the tax on those fees and any fees Etsy gave back. |
| Advertising | Etsy Ads, Offsite Ads and any other marketing Etsy billed in the month. |
| Shipping labels and other | Shipping labels bought on Etsy, and any row of the statement we could not classify (listed on the import screen). |
| Your product costs | What the items sold cost you to make and to have shipped, from the costs you entered per profile. Never assumed. |

## Its lines

| Number | What it is |
|---|---|
| Items sold | What buyers paid for the items themselves, after discounts and after sales tax. On a statement month: revenue less the shipping buyers paid. |
| Shipping paid by buyers | What buyers paid you for shipping, from each order in your shop's sales. It is income; what you paid to ship is under your product costs or shipping labels. |
| Refunds | What you refunded to buyers, less the sales tax Etsy returned with it. |
| Transaction fee on items | Etsy's fee on the price of each item sold. |
| Transaction fee on shipping | Etsy's fee on the shipping the buyer paid. |
| Payment processing fee | Etsy Payments' fee for taking the buyer's payment. |
| Listing fees | The fee for listing an item and for each renewal, including the automatic renewal when it sells. |
| Fees returned by Etsy | Fees Etsy credited back, for example on a refunded order. |
| Other Etsy fees | Any other fee on the statement, such as a currency conversion or subscription fee. |
| Tax on Etsy's fees | VAT or sales tax Etsy charged on its own fees, less any it credited back. |
| Etsy Ads | Charged by Etsy this month. A cost of the whole shop: Etsy does not say which listing a charge is for, so it is not given to listings, not even in proportion. |
| Offsite Ads fees | Etsy's fee on orders that came from its ads on other sites, less any credited back. Billed per order, so each listing's result includes its own. |
| Other marketing | Any other marketing charge on the statement. |
| Shipping labels | Postage you bought through Etsy, less label refunds. |
| Not classified | Rows of the statement that fit none of our rules. They are counted here so the total still matches Etsy's, and listed on the import screen. |
| Production | Your production cost per item, times the items sold. Only for profiles whose cost you entered. |
| Provider's shipping | What your print provider charges you to ship an item, times the items sold. If they charge less for extra items in one parcel, enter your average. |

## Totals, advertising and what is beside the account

| Number | What it is |
|---|---|
| Profit before product cost | Revenue less everything Etsy charged in the month. On a statement month it equals the statement's net amount to the cent. |
| Profit | Profit before product cost, less your production and provider shipping costs. Shown only when every item sold has a cost entered. |
| Paid out to your bank | Transfers from Etsy to your bank in the month. A transfer is neither income nor a cost, so it is beside the account, not in it. |
| Charged by Etsy this month | Etsy Ads as billed on the month's statement. Etsy bills each day's clicks on the next day, so this includes the last day of the month before and leaves out the month's own last day. |
| Ad spend for clicks this month | Etsy Ads for clicks made in the month, from the Ads report you imported. It differs from the amount charged by the one-day billing lag. |
| Break-even ROAS | The sales your ads must bring per 1 of ad spend for Etsy Ads to stop losing money, after Etsy's fees and your product cost. One divided by what a unit of sales leaves you before ads. |
| Your Etsy Ads ROAS | Sales Etsy attributes to your ads divided by ad spend, both from the Ads report for the month. Above break-even, ads earn; below it, they cost more than they bring. |
| Not attributed to a listing | Amounts on the statement that cannot be tied to a listing: orders the sales read does not have yet, and charges Etsy bills to the shop as a whole. |
| At stake | The money the item is about, for the month: a loss already made, sales that went missing, or sales whose cost is unknown. |

## The listings table

| Number | What it is |
|---|---|
| Class | Where the listing stands this month. Each class shows its reason. |
| Sold | Items sold in the month's orders. |
| Sales | What buyers paid for the listing's items and its share of the shipping they paid, after sales tax. An order with several items is split between them by price, to the cent. |
| Etsy fees | The listing's share of each order's Etsy fees and tax on fees, its Offsite Ads fees, and its own listing and renewal fees. Needs the month's statement. |
| Refunds | Refunds on the listing's orders, less the sales tax Etsy returned. |
| Product cost | Your production and provider shipping cost for the items sold, from the listing's profile. Blank until you enter it. |
| Profit before ads | Sales less refunds, Etsy fees and your product cost. Before Etsy Ads, which is a cost of the whole shop. Without a product cost it is the result before product cost, and says so. |
| Per item | Profit before ads divided by the items sold: what one more sale is worth. |
| Last 6 months | Items sold in each of the last six months, oldest first. |
| Views | Listing views on Etsy in the month, not search impressions. Read once a day, only for listings published with the app. Etsy's API has no impressions, search terms or traffic sources. |
| Favourites | Times the listing was favourited in the month (listings published with the app). |
| Conversion | Orders ÷ listing views on Etsy in the month. Not a search click-through rate. |

## Listing classes

| Number | What it is |
|---|---|
| Winner | In the top fifth of your listings by profit before ads, among those that sold at least 3 and made money. |
| Steady | Selling and not losing money, without a sharp change from the month before. |
| Fading | Sold half or less of the month before, when it sold at least 3 then. |
| Losing money | Its sales left less than they cost: after Etsy's fees and your product cost, or even before product cost. |
| New | Listed fewer than 45 days before the end of the month: too early to judge. |

## Views, favourites and title styles

For the listings published with the app, Etsy's lifetime views and favourites are read once a day and the day's increase is kept for 13 months (deleted with the shop). A listing's first reading is a starting point, not a day's views, unless it went live in the 48 hours before; a day that was not read is included in the next reading. The read costs one Etsy request per 100 listings per shop per day, and none for listings the shop sync already read that day.

These are **listing views on Etsy, not search impressions**. Etsy's API has no impressions, no search terms and no traffic sources, so how often a listing appeared in search, for which searches, and where its visitors came from cannot be measured here. Conversion is orders ÷ views; it is not a search click-through rate.

The title style comparison takes the listings published with the app in the same period, by the style their first published text was written in ("Etsy recommended (short)" or "Long keyword"), while that text was live. It shows views per listing per day, favourites per view and orders per view, each with its number of listings and a 95% interval clustered by listing. Below 30 listings or 1,000 views for a style it says "not enough data yet". Etsy gives new listings a small temporary boost and shoppers' context varies, so the comparison is of listings published at the same time; it is a measurement, not a ranking promise, and nothing is ever rewritten from it.

## Refunds

Refunds are shown net. The tooltip gives both parts, for example: "95.28 refunded to buyers, of which 6.26 was sales tax that Etsy returned."
