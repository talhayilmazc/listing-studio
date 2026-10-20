# Commercial Access application: answers

> **Approved by Etsy on 19 October 2026.** Listyro's limits are now 150 requests a second and 100,000 a day, and the personal-access limit of 5 users no longer applies. This file is kept unchanged below as the record of what was submitted and approved: the figures in it (5,000 a day, 3 a second, 4,500 per account, 20 shops, "5 users") describe the app at the time. Today's settings are in `docs/etsy-compliance.md` and `CLAUDE.md`.

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

Listyro can also make product mockups from the seller's own files: the seller uploads photos of a blank garment they sell (for example the same T-shirt in several colours), confirming that each photo is their own or that they have the right to use it, and Listyro places each of the seller's designs on them as if printed on the fabric. This is deterministic image processing on our own server: the design is fitted into the print area, warped to the fabric's perspective, folds and creases, shaded by the fabric and placed behind hair, skin, accessories or things held in front of the garment; it is never redrawn, and no AI image generation is used. To find each garment in the photo, its print area and what is in front of it, three open-source image-analysis models (Google's MediaPipe pose landmarker and multiclass segmenter, and the IS-Net segmentation model, all Apache-2.0) run once per blank photo on our own server's CPU; they are bundled into our server image, nothing is downloaded or sent to any outside service, no photo leaves the server, and no model is trained on sellers' photos. A photo with several garments gets a print area for each, and each photo is marked as showing the front or the back. The detected print area is only a suggestion: every photo opens in an editor where the seller checks it, moves or resizes it if needed and presses "Looks right", and a photo is used only after that check. A template can hold many photos of a garment (for example each colour); one photo's area can be applied to the others, each placed by its own garment, and confirmed together; then designs are added in bulk and each is placed on every confirmed photo. Where the analysis misses hair or a hand in front of the garment, the seller can paint it with a simple brush; the mask stays on our server. No Etsy API call is made and no Etsy data is used to make a mockup. The seller can use the mockups as that design's listing photos (the listing is then reviewed and approved like any other draft), download them as a ZIP that is deleted 24 hours later, or both.

Listyro also gives the seller a profit, cost and ads report for their own shop, worked out from their own shop's sales and fees.

## Who uses it

Individual Etsy sellers, each using only their own shops. The app is in invite-only early access. Each seller signs up with their own account, agrees to the Terms of Service and Privacy Policy by an explicit tick, and connects their own shop through Etsy's OAuth page. A seller who runs several shops connects each one by signing in to Etsy as that shop's owner; a shop can be connected to one Listyro account at a time. The number of shops an account may connect is set per account: there is a default, and it can be raised for a seller who runs more shops of their own. We also cap the total number of shops connected across all accounts, because every shop shares the one daily request budget.

Listyro has reached the user limit of personal access (5 users): sellers who have asked to join, including a seller with 16 shops, cannot connect until Commercial Access is approved. Five Etsy users are connected today, and the next connection fails.

A seller who runs 16 shops, each its own Etsy account and brand, has asked to use Listyro. With the personal-access limit reached, they cannot connect yet. Listyro would let them manage those shops from one login, each with its own listings and settings. If Etsy prefers limits on how a seller's designs are shared between their own shops, we will build them in.

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

> **Changed after approval (20 October 2026), `shops_w`:** the table above is the text Etsy approved. Since 20
> October 2026 setting a profile up in a shop (the seller's own click: "Use in", "Finish setup", "Use all
> reference listings here") creates there, without a second confirmation, the shipping profile, return policy or
> processing profile it lacks: a copy of the seller's main shop's, only where that shop has none, once per
> setting and shop. A copy Listyro made that is deleted on Etsy is made again; nothing is ever deleted in a shop.
> Background work never creates a new setting. The current wording is in `docs/etsy-compliance.md` and
> `backend/app/etsy/oauth.py::SCOPE_REASONS`; tell Etsy if it asks about scope use.

## AI use

AI suggests a title, 13 tags and a description from the seller's own images and the reference listing they chose. The seller reviews and edits every listing; nothing is generated or published without the seller.

The AI provider is Anthropic (Claude API). It receives the seller's own images and listing text only: the design image the seller asks a listing for; its SKU, the kind of product and the profile's title prefix; the title and tags of one of the seller's own listings, if they chose it as an example to follow; and occasionally an image from the seller's own reference listing, to tell a size chart from artwork. It never receives buyer data, sales or orders, and under its commercial terms it does not train on API data. The mockup generator and the search-readiness checklist send nothing to the AI provider: the checklist is rules only, and the mockup generator's image analysis runs on our own server with bundled open-source models that analyse the blank photo and never draw anything. We do not train or fine-tune any model ourselves. A trademark filter, on by default (the seller can turn it off after an explicit warning), keeps brand and character names out of the listing's text and flags artwork that appears to contain a recognisable character. A seller can also write the title and tags themselves, in which case no AI is used for that listing.

## Quality safeguards

Listyro is built for sellers' own original designs. These safeguards apply to every listing:

- every listing waits on a review page for the seller's approval before it is created in their shop, and it is created as a draft; nothing is published without the seller;
- a trademark filter, on by default, keeps brand and character names out of titles, tags and descriptions, and holds a listing back when the AI's analysis of its artwork finds what may be a recognisable character (a seller who turns the filter off, after an explicit warning, sees a warning instead; a listing written without AI is checked for names, but its artwork is not analysed);
- drafts copy who made the item, when it was made, whether it is a supply and the production partners from the seller's own listing, and read them back from Etsy after the draft is made; the review card warns when a print-on-demand profile has no production partner (Creativity Standards);
- a seller can write titles and tags themselves with no AI;
- each review card shows a search-readiness checklist (title length, repeated title words for the short title style, all 13 tags used and valid, no duplicate tags, the title's words early in the description, Occasion and Holiday, trademark findings, a production partner for print-on-demand profiles). It is computed only from that listing's own text and attributes and its profile's settings against Etsy's published Seller Handbook guidance; it makes no API calls, uses no other shops' or marketplace data, says on screen that it does not predict how Etsy ranks a listing, and never blocks a draft;
- mockups show only the seller's own design on the seller's own blank garment photo, placed and shaded by deterministic image processing, never redrawn; the seller confirms they have the right to use each blank photo, and is reminded that photos must show the item they sell accurately;
- a design is not created twice in the same shop: a shop that already has its draft or listing is skipped, and a retry never creates a second draft;
- each connected shop has its own drafts and its own shipping, return, processing and section settings. The seller chooses the shop or shops each design is listed in, and is warned when a design already sent to one group of their shops is sent to another;
- our Terms require sellers to follow Etsy's Seller Policy, including the rules against duplicate shops and search manipulation.

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
- **Per account:** each account has its own daily ceiling for its own work (4,500 by default). The seller sees what is left of it on their Overview page, and is told on every page when it makes their work wait. A job whose estimated cost does not fit in what the account has left waits for the reset too. Setting a profile up in the seller's other shops counts toward the app's daily limit, not this ceiling.
- **Per draft:** creating a draft costs about 33 to 40 requests for a listing with 13 photos: one for each image, one for each attribute written, and about 12 that do not depend on its size (the shop, its sections, the create call, the read-backs, inventory and personalization).
- **429:** if Etsy answers 429, every worker waits for the Retry-After period, and the retry passes through the same limiter and counters.
- **Background reads** (sales, ledger, statistics) are paced so that keeping shops current uses a small, bounded share of the daily budget.

## Fees and payments

No payment of any kind is taken today, and the app contains no payment or checkout code. The planned model is a subscription, paid monthly or yearly; paid yearly, a plan costs ten months. These are the planned plans, shown on listyro.com/pricing as planned prices that may change before launch:

- Starter: 100 listings a month, 1 shop, scheduling not included (available as an add-on), $69/month, $690/year
- Growth: 200 listings a month, 2 shops, scheduling not included (available as an add-on), $129/month, $1,290/year
- Studio: 350 listings a month, 4 shops, scheduling included, $239/month, $2,390/year
- Catalog: 500 listings a month, 8 shops, scheduling included, $329/month, $3,290/year
- Custom: more listings or shops, quoted on request
- Scheduling add-on for Starter and Growth: +$19/month, or +$190/year on the yearly plan

A listing is one design written by AI or by the seller, with all its photos. Rewriting the same design is free up to 3 times; photos per listing and the number of drafts in the seller's shops do not count. It is the number the seller sees in the app as "Listings generated". Every plan includes everything else: profiles, AI-suggested or write-it-yourself text, trademark checks, review and approve, size charts and the profit report. The plans are not enforced or sold today: there is no sign-up for one. Prices will be published on listyro.com/pricing before any charge is made, and existing sellers will be told in advance. At about 35 API calls per listing, our largest planned plan (500 listings a month) needs about 600 calls a day per seller, so today's 5,000-a-day limit serves several sellers on every plan. We ask for a higher limit to grow, not to run a single seller. That is for one draft of each listing; a listing drafted in more than one of the seller's shops costs about 35 calls in each. Paid plans will be introduced only after Commercial Access is approved. What a plan will pay for is the work Listyro does itself: preparing images, suggesting listing text, compliance checks, the review and scheduling workflow, and the profit report. Plans differ by how much work Listyro does each month: the listings it prepares and the number of shops it prepares them for, which drives our AI and processing costs and our share of the API budget. Connecting a shop, creating listings on Etsy and Etsy's own statistics are free on Etsy and are not what a seller pays for. The profit report is the seller's own profit, cost and ads reporting, not a copy of Etsy Stats.

## Branding

The product is named Listyro, on listyro.com. "Etsy" does not appear in the product name, logo, domain or page titles, and no Etsy logo is used. The notice "The term 'Etsy' is a trademark of Etsy, Inc. This Application uses Etsy's API, but is not endorsed or certified by Etsy." is shown prominently on the home page under the first call to action, in the footer of every page, beside "Connect a shop", and in the Terms. The Terms state who provides the application: "THIS APPLICATION IS SOLELY PROVIDED BY TALHA YILMAZ AND AHMET DÜZGÜN, OPERATING AS LISTYRO."

## What Listyro does not do

It does not manage Etsy Ads, handle checkout or payments, direct buyers away from Etsy, read other sellers' data, publish anything without the seller's approval, generate images with AI, promise search ranking, or resell or sublicense API access.

Checked before sending: the trademark notice matches Etsy's API Terms word for word; support@listyro.com is a working, read mailbox.
