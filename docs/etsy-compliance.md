# Listyro and Etsy's API Terms of Use

How Listyro meets each requirement, and where the evidence is. Every row names a file, a setting or a page; where a test proves the row, the test is named too.

**Read this first.** The requirements in the left column are the ones in the project's own rule file (`CLAUDE.md`) and in the brief for the Commercial Access application. Etsy's page (`etsy.com/legal/api`) refuses automated reading, so this table was **not** checked line by line against the live text by the tool that wrote it. The trademark notice is the exception: Talha Yılmaz compared it with Etsy's live API Terms in October 2026, and it matches word for word. The other rows are for a person to read against the current Terms (see "Only a person can do" at the end).

Developer: Talha Yılmaz and Ahmet Düzgün, operating as Listyro. Etsy app ID 1509913215439. Contact: support@listyro.com.

## 1. Identity, branding and legal texts

| Requirement | How Listyro meets it | Evidence |
|---|---|---|
| Show the trademark notice: "The term 'Etsy' is a trademark of Etsy, Inc. This application uses Etsy's API, but is not endorsed or certified by Etsy." | One copy of the text, shown in the footer of every public page, the footer of every signed-in page, the sign-in, sign-up and acceptance screens, beside "Connect a shop" on the Shops page, and in the Terms (section 3). | `frontend/lib/legal.ts` (`TRADEMARK_NOTICE`), `backend/app/api/schemas.py` (the API's copy). Pages: `/`, `/pricing`, `/terms`, `/privacy`, `/connect`. Test: `tests/test_legal_texts.py` (the two copies are identical; no page types its own; each frame uses it). |
| Say who provides the application | "THIS APPLICATION IS SOLELY PROVIDED BY TALHA YILMAZ AND AHMET DÜZGÜN, OPERATING AS LISTYRO." in the Terms (section 1, conspicuous), the Privacy Policy (section 1), at signup and on the acceptance screen. Etsy is named as not a party and as giving no warranty. | `PROVIDER_DISCLAIMER` in `frontend/lib/legal.ts`; `/terms#provider`, `/terms#warranty`. |
| Etsy's marks are not more prominent than ours; no endorsement or partnership implied; "Etsy" is not in the product name, logo, domain or page titles | The product is "Listyro" on listyro.com. No Etsy logo is used anywhere. No heading or page title on the public site contains "Etsy". The FAQ says plainly that Listyro is not made, endorsed or certified by Etsy. Screenshots are labelled "Sample data" and use our own drawings. | `frontend/app/layout.tsx` (titles), `frontend/components/marketing/*`, `docs/marketing-screens.md`. |
| The application has its own Terms of Service and Privacy Policy | `/terms` and `/privacy`, linked in every footer. Both are written from the code's behaviour; the Privacy Policy prints every retention period from one constants file that a test compares with the settings the code uses. | `frontend/app/(marketing)/terms/page.tsx`, `.../privacy/page.tsx`, `frontend/lib/legal.ts` (`RETENTION`), `tests/test_legal_texts.py`. |
| Click-through acceptance | Signup has an unticked checkbox "I agree to the Terms of Service and Privacy Policy" (both linked); the server refuses to create the account without it. The version and time are stored per account. An account that has not agreed to the current version sees only the acceptance screen, and the server refuses every other request until it agrees. A new version asks everyone again. | `backend/app/core/terms.py`, `api/accounts.py` (`/register`, `/terms`), `api/deps.py::active_tenant`, migration `0062`. Pages: `/register`, `/accept-terms`. Test: `tests/test_commercial_readiness.py`. |
| Visible support contact | support@listyro.com in every footer and on `/contact`. | `SUPPORT_EMAIL`; production refuses to start without it (`deploy/preflight.sh`). |
| 30 days' notice before the application is suspended or withdrawn | A commitment in the Terms (section 10): at least 30 days' written notice by email and in the app. | `/terms#suspension`. This is a promise people keep, not code. |

## 2. Access to Etsy

| Requirement | How Listyro meets it | Evidence |
|---|---|---|
| Official API only; no scraping, no browser automation, no internal endpoints | Every request goes through one client to Open API v3. There is no headless browser or HTML parser in the application or anything it runs with. (The frontend's development tools include a browser test runner, Playwright, used only to check the layout of our own pages on our own hosts; its configuration refuses any other address, and it is not part of the production image.) | `backend/app/etsy/api.py` (the only caller of Etsy's API host), `backend/pyproject.toml`. |
| Only the authenticated seller's own shops | Every endpoint that takes a shop checks that it is the caller's own connected shop, else 404. A shop can be connected to one account at a time. Nothing reads another seller's listings, tags, images or prices. | `etsy/shops.py::owned_shop`, `tests/test_tenant_isolation.py`, `tests/test_multi_shop.py`. |
| Scopes limited to what is used | Five scopes, each with a stated reason and a feature that uses it (below). A test fails if the app asks for a scope with no reason, or keeps a reason for a scope it no longer asks for. | `etsy/oauth.py::SCOPE_REASONS`, `ETSY_SCOPES`, the Shops page ("What Listyro asks Etsy for, and why"). |
| One API key; rate limits respected | See section 5. | |
| Tokens protected | Access and refresh tokens are encrypted at rest (Fernet), decrypted only to send a request, never logged, never in an error message, never sent to the browser. Disconnecting deletes them at once. | `core/crypto.py`, `etsy/connection.py`, `core/logsafety.py`, `tests/test_logsafety.py`. |
| No publishing without the seller | Every listing is created as a draft. A draft goes live only on the seller's own instruction: "Publish now", a time they set for a draft they approved, or a schedule they confirmed for listings they approved, re-checked at the moment of publishing. There is no auto-publish setting. | `etsy/publisher.py`, `workers/schedule.py`, `/terms#publishing`. |
| No Etsy Ads management; no checkout; no directing buyers off Etsy | None of these exists in the code. Advice in Analytics points to Shop Manager. | `CLAUDE.md` rules 4, 7, 8. |

### Scopes

| Scope | Why it is asked for (the feature that uses it) |
|---|---|
| `listings_r` | Read the seller's own listings: the reference listing of a profile, the shop's listing cache, and the views and favourites of listings made with Listyro. |
| `listings_w` | Create draft listings, upload their images, set inventory, attributes and personalization, publish a draft on the seller's instruction, replace a listing's images. |
| `shops_r` | Read the shop's name, sections, shipping profiles, return policies, processing profiles and production partners, so a draft uses the shop's own settings. |
| `shops_w` | Create a shop section, shipping profile, return policy or processing profile in the seller's shop, only when the seller confirms it. |
| `transactions_r` | Read the seller's own sales lines and payment account ledger for profit, cost and ads reporting. No buyer field is stored. |

No scope is unused, so none was removed.

## 3. Data: what is read, what is stored, for how long

| Requirement | How Listyro meets it | Evidence |
|---|---|---|
| Listing content shown is at most 6 hours old | The shop's listing cache is deleted at 6 hours and read again on demand; a profile's reference image links are stripped at 6 hours; Analytics takes listing titles, images and links from that same cache and says "titles refreshing" when it is not fresh. Nothing older is displayed. | `ShopListingCache.STALE_SECONDS`, `ListingProfile.DISPLAY_MAX_AGE_SECONDS`, `workers/retention.py`, `api/analytics.py` (header comment). |
| Other Etsy content is at most 24 hours old | A profile's reference settings, a shop's section list and listing counts are cleared or re-read at 24 hours. | `ListingProfile.CACHE_MAX_AGE_SECONDS`, `api/item_options.py::SECTIONS_MAX_AGE`. |
| Data kept only as long as needed, with a stated period | Every table that holds Etsy data has a period, enforced by a job that runs every 15 minutes. Analytics history is kept 24 months by default, set in Admin → Usage. The Privacy Policy prints the periods from the same constants. | `workers/retention.py`, `core/app_settings.py`, `/privacy#etsy`, `/privacy#retention`, `tests/test_legal_texts.py`. |
| No buyer personal data | Nothing about buyers is stored. See "Buyer data" below. | `tests/test_buyer_data.py`. |
| Deleted when the seller revokes access | Disconnecting a shop deletes its tokens at once, cancels its queued jobs, scheduled go-lives and planned drafts (and tells the seller what was cancelled), and deletes everything read from Etsy for it, in one transaction. | `etsy/connection.py::disconnect`, `workers/retention.py::purge_shop_etsy_content`, `/privacy#disconnect`. |
| The seller can delete their account | Settings → Delete account. At once: every shop is disconnected as above and the account can no longer sign in. Then a job deletes the uploads and the account with everything that belongs to it; a daily sweep catches a job that was lost. Within 30 days at the latest, normally minutes. | `workers/accounts.py`, `api/accounts.py` (`/delete`), `/privacy#deletion`. |
| Stop using Etsy data immediately if Etsy ends API access | A runbook: block every call with one setting, tell sellers, then one command deletes all stored Etsy data (a dry run unless `--apply`). | `docs/etsy-access-ended.md`, `python -m app.cli purge-etsy-data`, `app/ops/etsy_data.py`. |
| No selling or sharing of data; nothing to advertising or analytics platforms | There is no analytics, advertising or tracking script on the site or in the app, and no code that sends seller or Etsy data to such a service. | `tests/test_legal_texts.py::test_no_external_script_or_tracker_is_loaded_by_any_page`, section 6. |
| Etsy data is not used to train AI models | No training or fine-tuning code exists. The AI provider (Anthropic) receives the seller's own images and listing text only, under commercial terms that exclude training on API data. | `pipeline/llm.py`, `/privacy#ai`. |
| Each seller's data is used only for that seller | No report, comparison or model combines sellers. Admin views show account facts, counts and our own costs, never a seller's designs, listings or sales. | `api/admin.py` (response models), `tests/test_admin.py`, `tests/test_no_ai_cost_for_sellers.py`. |

### What is kept after an account is deleted, and why

| Kept | Why | What it contains |
|---|---|---|
| `audit_log` rows | Our record that the deletion and the disconnects happened | The action, the time, counts and internal ids. No titles, images, e-mail addresses or Etsy content. Their link to the account is cleared. |
| `ai_call` rows | Our own cost accounts | Model, token counts, cost. Unlinked from the account. 25 months. |
| The redeemed invite code | So a code cannot be used twice | Its hash and dates, unlinked. |
| Database backups | Recovery | Everything, for 14 days, then gone. |

### Buyer data: every field read and stored

| Source | Read from Etsy's answer | Stored | Not read, dropped in memory |
|---|---|---|---|
| Sales lines (`getShopReceiptTransactionsByShop`) | transaction id, receipt id, listing id, quantity, price, shipping cost, created time | `sale_line`: those fields as ids, a day and amounts. `sales_daily`: units, orders and revenue per listing per day. | `buyer_user_id`, the listing title and description, variations (which can carry a buyer's personalization text), and every other field. |
| Payment ledger (`getShopPaymentAccountLedgerEntries`) | entry type, amount, date | `ledger_daily`: one total per type per day. | The entries, their descriptions and references. |
| Monthly statement CSV (uploaded by the seller) | date, type, title, info, amounts | Per month: totals by category; per order: the order number and amounts; per listing: the listing id and fee amounts; rows of an unknown type with their title and amount. | The file itself. Etsy's statement has no buyer names or addresses. |
| Etsy Ads report CSV (uploaded by the seller) | day, views, clicks, orders, revenue, spend | `ads_daily`: those figures per day, for the shop. | The file itself. |
| Receipts (`getShopReceipts`) | Not called at all: it returns the buyer's name and address. | | |

No buyer name, address, e-mail or message has ever had a column to be stored in, so there was nothing to purge and no purge migration was needed. `tests/test_buyer_data.py` sends sales carrying buyer fields through the real sales job and proves that none reaches any table, any log record, any alert or the queue; it also proves from the code's imports that nothing that calls the AI provider can see sales, ledger or statement data.

## 4. Third parties

Every external service the site and the app use:

| Service | What it receives | Seller or Etsy data? |
|---|---|---|
| Anthropic (Claude API) | The design images the seller asks a listing for; SKU, product kind, the profile's title prefix; the title and tags of one of the seller's own listings when they chose it as an example; occasionally an image of the seller's own reference listing, to tell a size chart from artwork. | The seller's own images and listing text. Never buyer data, sales or orders. Not used for training. |
| Cloudflare (Tunnel, proxy) | All traffic, encrypted, on its way to our server. | In transit only. |
| Our server in Türkiye | The database and files. | Everything we store. Administered by us. |
| Etsy | What the seller instructs us to send to their own shop. | |
| Sentry (only if `SENTRY_DSN` is set) | Error reports, scrubbed of e-mail addresses, IPs, cookies, tokens and submitted content. | No. |
| The alert channel (`ALERT_WEBHOOK_URL`) | Operational alerts (deploy, disk). One exception: a new invite request sends the requester's e-mail address, shop and shop count, so it is answered. | No seller or Etsy data. A visitor's e-mail address, disclosed in the Privacy Policy. |

On the pages themselves: fonts are downloaded at build time and served from our own domain (`next/font`); there is no analytics, pixel, tag manager or CDN script; the only third-party host a browser contacts is `i.etsystatic.com`, for the seller's own listing images, each linked back to its listing on Etsy. Nothing was removed, because nothing of the kind was there; a test now fails if one is added.

## 5. Rate limits

| Limit | How it is enforced | Evidence |
|---|---|---|
| One API key | One key and shared secret for the whole app, read from the environment. | `ETSY_CLIENT_ID`, `ETSY_CLIENT_SECRET`. |
| Queries per second | **We send at most 3 requests per second, below Etsy's 5 per second.** Every request, from every job and every shop, passes through one global limiter that spaces requests at least a third of a second apart; there is no burst, so no one-second window holds more than 3. A 429 makes every worker wait for `Retry-After`; the retry goes through the limiter again. | `etsy/rate_limiter.py`, `ETSY_REQUESTS_PER_SECOND`, `tests/test_rate_limiter.py`. |
| Requests per day | One counter for the whole app against the daily limit of **5,000**. **We pause at 90% of the daily limit** (4,500): new jobs wait for the reset at 00:00 UTC before making their first request, and the seller is told why. The remaining 10% is held back so that jobs already running can finish. | `GLOBAL_DAILY_LIMIT`, `GLOBAL_PAUSE_PERCENT`, `workers/gate.py`. |
| Per-account quota | Each account has its own daily ceiling for its own work (4,500 by default, set per account in Admin). The seller sees what is left of it on their Overview page and is told on every page when it makes their work wait. Background upkeep, and setting a profile up in the seller's other shops, are counted against the app's budget, not the seller's. |
| What a draft costs | Creating a draft costs about 33 to 40 requests for a listing with 13 photos: one for each image, one for each attribute written, and about 12 that do not depend on its size (the shop, its sections, the create call, the read-backs, inventory and personalization). At about 35 API calls per listing, our largest planned plan (500 listings a month) needs about 600 calls a day per seller, so today's 5,000-a-day limit serves several sellers on every plan. We ask for a higher limit to grow, not to run a single seller. That is for one draft of each listing; a listing drafted in more than one of the seller's shops costs about 35 calls in each. | `etsy/publisher.py`, `core/request_cost.py` (the measured average a draft is admitted on), `python -m app.cli draft-cost`, `frontend/lib/plans.ts`. | `core/limits.py`, `ACCOUNT_DAILY_CEILING`, `/api/quota`. |
| Shops | The number of shops an account may connect is set per account: a default (`MAX_SHOPS_PER_TENANT`, 8) that an admin can change for one account to any number. Personal access allows 5 Etsy users, and that limit is reached: five are connected today and the next connection fails. A seller who runs 16 shops, each its own Etsy account and brand, has asked to use Listyro. With the personal-access limit reached, they cannot connect yet. Listyro would let them manage those shops from one login, each with its own listings and settings. If Etsy prefers limits on how a seller's designs are shared between their own shops, we will build them in. Separately, the total connected across all accounts is capped by `MAX_SHOPS_APP_WIDE` (20 unless the server sets another value), because every shop shares the one daily budget. | `MAX_SHOPS_PER_TENANT`, `MAX_SHOPS_APP_WIDE`, `tenant.max_shops`, Admin → Users. |
| Bounded background work | A shop's first sales read is paced at 250 requests a day; re-reads together use at most 20% of the daily budget; the ledger back-fill waits whenever half the budget is used. | `workers/sales.py`, `workers/ledger.py`. |

## 6. Product rules

| Requirement | How Listyro meets it | Evidence |
|---|---|---|
| Etsy's Creativity Standards | A draft copies who made it, when it was made, whether it is a supply, and the production partners from the seller's own reference listing, and reads them back after the draft is made. A print-on-demand profile whose reference names no production partner gets a warning on the review card before any draft. The Terms make the seller responsible for stating them correctly. | `etsy/publisher.py` (lines that set and verify `who_made`, `when_made`, `is_supply`, `production_partner_ids`), `pipeline/creativity.py`, `/terms#responsibilities`. |
| No prohibited items | The app has no category or template for items Etsy prohibits and never suggests one: a draft takes its category from the seller's own existing listing. The Terms forbid using the Service for them. | `pipeline/reference.py`, `/terms#responsibilities`. |
| No duplicate shops or search manipulation | The Terms name both rules. Titles are written without keyword repetition and tags are checked against Etsy's tag rules. Shop groups send a listing to the shops of one group only. See the note below. | `/terms#responsibilities`, `pipeline/search_rules.py`. |
| No payment before Commercial Access; nothing charged for what Etsy gives free | There is no payment or checkout code. The pricing page shows planned plans, paid monthly or yearly (a year costs ten months): Starter: 100 listings a month, 1 shop, scheduling not included (available as an add-on), $69/month, $690/year; Growth: 200 listings a month, 2 shops, scheduling not included (available as an add-on), $129/month, $1,290/year; Studio: 350 listings a month, 4 shops, scheduling included, $239/month, $2,390/year; Catalog: 500 listings a month, 8 shops, scheduling included, $329/month, $3,290/year; Custom: quoted on request; Scheduling add-on for Starter and Growth: +$19/month, or +$190/year on the yearly plan. They are shown under the notice "Planned prices after launch. No payment is taken today. Prices may change before launch.", with no sign-up for one. A listing is one design written by AI or by the seller; its first three rewrites are free, and photos and drafts do not count. Plans differ by how much work Listyro does each month: the listings it prepares and the number of shops it prepares them for, which drives our AI and processing costs and our share of the API budget. Connecting a shop, creating listings on Etsy and Etsy's own statistics are free on Etsy and are not what a seller pays for. (The pricing page says that creating listings on Etsy and Etsy's own statistics stay free, as Etsy offers them.) Analytics is described as the seller's own profit, cost and ads reporting. | `frontend/app/(marketing)/pricing/page.tsx`, `frontend/lib/plans.ts`, `frontend/lib/plans.test.ts` (no form, no payment code, the notice). |
| New connections can be paused | An operator setting (off by default) stops new shop connections and shows: "New shop connections are paused until Etsy approves our commercial access. Your existing shops keep working." | Admin → Usage, `core/app_settings.py`. |

**A note the developers must weigh.** A seller with several shops can send one design's listing to more than one of their own shops (the shop matrix, and shop groups). The wording on the site and in the app now describes this as managing several shops, each with its own listings, and the Terms tell sellers not to run duplicate shops. The wording does not change what the feature can be used for. Whether it is acceptable under Etsy's Seller Policy is a question for Etsy. The application raises it: it tells Etsy about the seller with 16 shops and says that if Etsy prefers limits on how a seller's designs are shared between their own shops, we will build them in (`docs/commercial-application.md`, "Who uses it").

## Only a person can do

1. Read the current API Terms of Use at etsy.com/legal/api and confirm each row above, and the exact wording asked for the provider disclaimer. (The trademark notice has been compared and matches; support@listyro.com is a working, read mailbox.)
2. Set the production values: `SUPPORT_EMAIL=support@listyro.com`, `OPERATOR_NAME`, `OPERATOR_LOCATION`, `GOVERNING_LAW`, `DISPUTE_VENUE`.
3. Confirm with Anthropic's current commercial terms that API data is not used for training (the Privacy Policy says so and links to them).
4. The question in the note above is raised in the application itself; act on Etsy's answer.
