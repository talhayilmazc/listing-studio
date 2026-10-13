# Commercial Access application: answers

Final answers for Etsy's Commercial Access form. Each answer is a statement about what the app does today; the evidence for each is in `docs/etsy-compliance.md`. Paste the text under each heading into the matching field. Fields Etsy words differently take the nearest answer.

## Application

- **Name:** Listyro
- **Etsy app ID:** 1509913215439
- **Website:** https://listyro.com
- **Developer:** Talha Yılmaz and Ahmet Düzgün, operating as Listyro (no company registered yet)
- **Support and privacy contact:** support@listyro.com
- **OAuth redirect:** https://api.listyro.com/api/auth/etsy/callback
- **Terms of Service:** https://listyro.com/terms
- **Privacy Policy:** https://listyro.com/privacy

## What the application does (purpose)

Listyro is a listing workflow tool for Etsy sellers who design their own products, mainly apparel and print-on-demand sellers who add new designs often. A seller uploads their own design images and chooses one of their own existing listings as a reference. Listyro prepares a complete draft listing from them: it copies the reference listing's category, attributes, price, variations, shipping, return and production-partner settings, resizes and orders the images, and suggests a title, tags and a description. The seller reviews and edits every listing and approves it; Listyro then creates it in the seller's shop as a draft through the Open API. A draft is published only when the seller presses Publish, or at a time the seller scheduled for a listing they approved.

Listyro also gives the seller a profit, cost and ads report for their own shop, worked out from their own shop's sales and fees.

## Who uses it

Individual Etsy sellers, each using only their own shops. The app is in invite-only early access with a handful of sellers. Each seller signs up with their own account, agrees to the Terms of Service and Privacy Policy by an explicit tick, and connects their own shop through Etsy's OAuth page. A seller who runs several shops connects each one by signing in to Etsy as that shop's owner; a shop can be connected to one Listyro account at a time. The number of shops an account may connect is set per account: there is a default, and we raise it for a seller who runs more shops of their own. We also cap the total connected across all accounts, below the 80 Etsy approved, because every shop shares the one daily request budget.

Our largest seller runs 16 shops today, each its own Etsy account and brand. Listyro lets them manage those shops from one login, each with its own listings and settings. If Etsy prefers limits on how a seller's designs are shared between their own shops, we will build them in.

## How Etsy data is used

Only the authenticated seller's own shop data is read, only through the Open API, and only to provide the service to that seller:

- **Listings (read):** to let the seller choose a reference listing, to show their shop's listings, and to read the views and favourites of listings created with Listyro. Cached for at most 6 hours, then deleted and read again on demand.
- **Reference listing settings and the shop's own lists** (sections, shipping profiles, return policies, processing profiles, production partners): to build drafts that match the seller's existing listings. Kept at most 24 hours.
- **Listings (write):** to create drafts, upload images, set inventory, attributes and personalization, and publish a draft on the seller's instruction.
- **Sales lines and the payment account ledger (read):** for the seller's own profit, cost and ads report. From a sale we keep the order number, listing id, quantity, price, shipping paid and date; from the ledger we keep one total per fee type per day. Kept 24 months, then deleted automatically.

We store **no buyer data**: no buyer names, addresses, e-mail addresses, usernames or messages. Buyer fields in Etsy's answers are never read into anything that is stored, and we do not call the receipts endpoint, which returns buyer names and addresses. A test proves that buyer fields reach no table, no log, no alert and no AI call.

We never read another seller's shop. We do not collect or compare data across sellers: each seller's data is used only for that seller, and our administrators see account facts and counts, not a seller's listings or sales. We do not sell or share data, we send nothing to advertising or analytics platforms, and we do not use Etsy data to train any model. We do not scrape Etsy.

When a seller disconnects a shop, its tokens are deleted at once, its queued and scheduled work is cancelled, and everything read from Etsy for it is deleted. A seller can delete their whole account from Settings. If Etsy ends our API access, we stop all use of Etsy data immediately and delete it (we keep a written runbook and a command for this).

## Scopes requested, and why

| Scope | Reason |
|---|---|
| `listings_r` | Read the seller's own listings: the reference listing for a profile, the shop's listings, and the views and favourites of listings made with Listyro. |
| `listings_w` | Create draft listings from the seller's designs, upload their images, set variations, attributes, personalization and SKUs, and publish a draft when the seller approves it. |
| `shops_r` | Read the shop's name, sections, shipping profiles, return policies, processing profiles and production partners, so a draft uses the shop's own settings. |
| `shops_w` | Create a shop section, shipping profile, return policy or processing profile in the seller's shop, only when the seller confirms it. |
| `transactions_r` | Read the seller's own sales lines and payment account ledger for their profit, cost and ads report. No buyer field is stored. |

Every scope requested is used; none is requested for future use.

## AI use

AI suggests a title, 13 tags and a description from the seller's own images and the reference listing they chose. The seller reviews and edits every listing; nothing is generated or published without the seller.

The AI provider is Anthropic (Claude API). It receives the seller's own design images and listing text only, never buyer data, sales or orders, and under its commercial terms it does not train on API data. We do not train or fine-tune any model ourselves. An optional filter keeps trademarked names out of the suggested text and flags artwork that appears to contain a recognisable character.

## Security

- OAuth access and refresh tokens are encrypted at rest and decrypted only to send a request. They are never logged, never shown in the app, never sent to the browser, and deleted the moment a shop is disconnected.
- All traffic is HTTPS; the server is reachable only through an encrypted Cloudflare Tunnel.
- Each account's data is isolated and checked on every request; a request for another account's resource is answered as "not found".
- Passwords are stored as argon2id hashes; repeated failed sign-ins are locked out; state-changing requests are checked against cross-site forgery; sessions are HttpOnly cookies.
- Logs and error reports are scrubbed of tokens, passwords, session ids, OAuth codes and e-mail addresses before they are written.
- The service runs on one server in Türkiye that we administer ourselves, with a verified database backup every night, kept 14 days.
- We keep a written incident runbook: Etsy (dpo@etsy.com) and the affected sellers are notified within 24 hours of discovering a breach, and any concern about API keys or credentials is reported to security@etsy.com.

## Rate limits

One API key. We send at most 3 requests per second, below Etsy's 5 per second, and pause at 90% of the daily limit.

- **Per second:** every request to the API, from every job and every shop, passes through one global limiter that spaces requests at least a third of a second apart. There is no burst: no one-second window holds more than 3 requests.
- **Per day:** one counter for the whole app against the daily limit of 5,000 requests. When 90% (4,500) is used, new jobs do not start: they wait for the reset at 00:00 UTC before making their first request, and the seller is told why. The remaining 10% is held back so that jobs already running can finish.
- **Per account:** each account has its own daily ceiling for its own work (4,500 by default), shown to the seller as "Etsy requests today". A job whose estimated cost does not fit in what the account has left waits for the reset too.
- **429:** if Etsy answers 429, every worker waits for the Retry-After period, and the retry passes through the same limiter and counters.
- **Background reads** (sales, ledger, statistics) are paced so that keeping shops current uses a small, bounded share of the daily budget.

## Fees and payments

No payment of any kind is taken today, and the app contains no payment or checkout code. Paid plans will be introduced only after Commercial Access is approved. What a plan will pay for is the work Listyro does itself: preparing images, suggesting listing text, compliance checks, the review and scheduling workflow, and the profit report. Nothing Etsy offers its sellers for free is charged for, and the profit report is the seller's own profit, cost and ads reporting, not a copy of Etsy Stats.

## Branding

The product is named Listyro, on listyro.com. "Etsy" does not appear in the product name, logo, domain or page titles, and no Etsy logo is used. The notice "The term 'Etsy' is a trademark of Etsy, Inc. This application uses Etsy's API, but is not endorsed or certified by Etsy." is shown in the footer of every page, beside "Connect a shop", and in the Terms. The Terms state who provides the application: "THIS APPLICATION IS SOLELY PROVIDED BY TALHA YILMAZ AND AHMET DÜZGÜN, OPERATING AS LISTYRO."

## What Listyro does not do

It does not manage Etsy Ads, handle checkout or payments, direct buyers away from Etsy, read other sellers' data, publish anything without the seller's approval, or resell or sublicense API access.

## Before sending

- Read the current API Terms at etsy.com/legal/api against `docs/etsy-compliance.md` (the tool that prepared these answers could not open that page).
- Make sure support@listyro.com is a working, read mailbox.
- Record the demo in `docs/reviewer-demo.md` and attach it if the form allows.
