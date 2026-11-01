# The free tools

Written for whoever runs listyro.com. How the tools work, what is switched off, and what you need to check by hand.

| Tool | Address | Where it runs | State |
|---|---|---|---|
| Title checker | `/free-tools/title-checker` | In the visitor's browser | Live |
| Tag checker | `/free-tools/tag-checker` | In the visitor's browser | Live |
| Fee calculator | `/free-tools/fee-calculator` | In the visitor's browser | Fees confirmed; open when `FEE_CALCULATOR_CONFIRMED=true` is set at build time |
| Title and tag ideas | `/free-tools/listing-ideas` | Our server and Anthropic | Off; its page is a 404 |

Screenshots at 1366 and 375 px are in `docs/screens/tools/`. The fee calculator's and the ideas tool's are from a build with their switches on. In the ideas picture Cloudflare's check and the API are stubbed, so the word "checked" is the test's stand-in, not the real widget.

## The rules are the app's

The app's rules are in Python, and a page with no server behind it needs them in TypeScript. So there are two copies, tied together:

- **The words and limits are not typed twice.** `frontend/lib/listingRuleData.ts` is written from `pipeline/search_rules.py` and `compliance/trademarks.py`. Change a rule or `trademarks.txt` and `tests/test_free_tool_rules.py` fails until you write it again:
  `docker compose exec -T api python -m app.cli free-tool-rules > frontend/lib/listingRuleData.ts`
  A trademark added to the list reaches the app within seconds, and the tools at the next deploy.
- **Both copies answer the same cases.** `backend/tests/fixtures/free_tool_cases.json` is written from the Python rules (`scripts/free_tool_cases.py`). The backend tests check Python still gives those answers, and `lib/freeToolRules.test.ts` checks the browser gives the same.

Each rule on the page is either Etsy's, quoted with a link, or labelled "Our advice". The quotes must be in `docs/guide-sources.md`, and a test fails if they are not.

**The app and the tools now say the same thing.** The brand warning is one sentence (`trademarks.BRAND_WARNING`, quoting Etsy's Help), and a tag starting with ' or - is a note in both and stops nothing.

## The fee calculator

`frontend/lib/freeTools/fees.ts` is the one dated file. The owner confirmed every fee on 1 November 2026 against Etsy's [Fees & Payments Policy](https://www.etsy.com/legal/fees) (updated 5 October 2026) and [Etsy Payments Policy](https://www.etsy.com/legal/etsy-payments) (updated 31 July 2026).

The page opens when `FEE_CALCULATOR_CONFIRMED=true` is in `.env` at build time. Without it the page says "Coming soon" and carries no figure.

**If a fee changes:** correct it in `fees.ts`, set `FEES_CHECKED` to the day you checked, and deploy. A fee whose `status` is set back to `"to confirm"` shows that tag on the public page.

| Fee | Value used | Etsy page |
|---|---|---|
| Listing fee | $0.20 USD per listing | https://help.etsy.com/hc/en-us/articles/360035902374 |
| Transaction fee | 6.5% of item price (with personalization), shipping and gift wrap | https://help.etsy.com/hc/en-us/articles/360035902374 |
| Payment processing | By country: a percent plus a flat fee, on item, shipping, gift wrap and sales tax (full table below) | https://help.etsy.com/hc/en-us/articles/115015628847-Fees-and-Payments |
| Regulatory operating fee | Canada 0.50%, France 1.14%, Hungary 1.97%, Italy 0.80%, India 0.05%, Spain 0.88%, Türkiye 1.67%, United Kingdom 0.48%, Vietnam 1.24%; on item (with personalization), shipping and gift wrap | https://help.etsy.com/hc/articles/1500011073202 |
| Offsite Ads | 15% under $10,000 in 365 days, 12% at $10,000 or more, at most $100 USD an order; on item (with personalization), shipping and gift wrap, without US sales tax | https://help.etsy.com/hc/en-us/articles/360000338367 |

- **Personalization** has no field of its own: the price field says to include it, so it is in every base.
- **Tax outside the US** is inside the price the seller enters, so the profit shown still includes it. The tax field's hint says so.
- **The fixed amounts in other currencies.** The listing fee and the Offsite Ads cap are in US dollars and Etsy converts them, so a seller outside the US types an exchange rate. We do not assume one.
- **Rounding.** Etsy does not say how it rounds. The calculator rounds each fee to the cent, so a real statement can differ by a cent.

**Not included, and the page says so:** VAT on Etsy's fees (all Etsy fees are listed exclusive of VAT), deposit fees (in Türkiye, for example, 42 TRY on deposits under 600 TRY), currency conversion (2.5%), Etsy Ads and shipping labels.

### Payment processing, as read

| Where your bank account is | Fee |
|---|---|
| Argentina, Brazil, Chile, China, Egypt, Georgia, Kazakhstan, Pakistan, Peru, Serbia, South Korea, United Arab Emirates | 6.5% + 0.30 USD |
| Austria, Belgium, Bulgaria, Croatia, Cyprus, Czech Republic, Estonia, Finland, France, Germany, Greece, Hungary, Ireland, Italy, Latvia, Lithuania, Luxembourg, Malta, Netherlands, Poland, Portugal, Romania, Slovakia, Slovenia, Spain | 4% + 0.30 EUR |
| Australia | domestic 3% + 0.25 AUD; international 4% + 0.25 AUD |
| Canada | domestic or from the US 3% + 0.25 CAD; international 4% + 0.25 CAD |
| New Zealand | domestic 3% + 0.30 NZD; international 4% + 0.30 NZD |
| Denmark / Norway | 4% + 2.50 DKK / 4% + 2.50 NOK |
| Hong Kong / Singapore | 4.4% + 2.00 HKD / 4.4% + 0.35 SGD |
| India | 5% + 25 INR |
| Indonesia / Vietnam | 4.5% + 7000 IDR / 4.5% + 11550 VND |
| Israel / Malaysia | 4.5% + 2 ILS / 4.5% + 2 MYR |
| Japan / Thailand / Ukraine | 6% + 0.30 USD |
| Mexico / South Africa | 4.5% + 8 MXN / 4.5% + 8 ZAR |
| Morocco / Philippines | 4.5% + 5 MAD / 4.5% + 25 PHP |
| Sweden / Switzerland | 4% + 3.00 SEK / 4% + 0.50 CHF |
| Türkiye | 6.5% + 14 TRY |
| United Kingdom | 4% + 0.20 GBP |
| United States | 3% + 0.25 USD |

## The title and tag ideas tool

It stays off unless every one of these is set in `.env`:

```
FREE_TOOL_AI_ENABLED=true
TURNSTILE_SITE_KEY=...      # Cloudflare dashboard → Turnstile → Add widget → listyro.com
TURNSTILE_SECRET_KEY=...
```

| Setting | Default | What it does |
|---|---|---|
| `FREE_TOOL_AI_PER_VISITOR` | 3 | Uses a day per connection. A visitor is a hash of their connection, never the address, gone after 2 days. A failure on our side gives the use back |
| `FREE_TOOL_AI_DAILY_CAP_USD` | 2.00 | When the day's `free_tool` rows in `ai_call` reach this, the tool stops until 00:00 UTC and sends one alert to `ALERT_WEBHOOK_URL`. Calls already under way can pass it by what they cost |
| `FREE_TOOL_AI_MODEL` | claude-haiku-5-5 | The model it uses |

- **Admin:** its spending is under Admin → Usage, as "Free title and tag ideas on the public site", with no seller.
- **What is kept:** nothing the visitor types. The Privacy Policy says so.
- **What the model writes:** it passes the app's rules before anyone sees it. What fails is dropped, and a brand in the description is refused before anything is spent.
- **Before you turn it on:** read Cloudflare's Turnstile privacy terms. The Privacy Policy says there are no third-party cookies, and nobody has confirmed that holds on this one page while the widget runs.
- **Not yet tried for real:** the tests stub Cloudflare and the model. The first time it is switched on, run one real request and check the widget loads under the page's Content-Security-Policy.
- **Caching:** the page is private, never cached, and it is a 404 while the flag is off. It is on the sitemap and the Free tools page only in a build with the flag on, so setting the flag needs a deploy.

## Caching

The checkers and the calculator are public pages like any other (`lib/marketing/routes.mjs`). The ideas page is deliberately not listed there, so it is `private, no-store`. The Cloudflare rule is in `docs/seo.md`.
