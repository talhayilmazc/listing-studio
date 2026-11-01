# The free tools

Written for whoever runs listyro.com. How the tools work, what is switched off, and what you need to check by hand.

| Tool | Address | Where it runs | State |
|---|---|---|---|
| Title checker | `/free-tools/title-checker` | In the visitor's browser | Live |
| Tag checker | `/free-tools/tag-checker` | In the visitor's browser | Live |
| Fee calculator | `/free-tools/fee-calculator` | In the visitor's browser | "Coming soon" until you confirm the fees |
| Title and tag ideas | `/free-tools/listing-ideas` | Our server and Anthropic | Off; its page is a 404 |

## The rules are the app's

The app's rules are in Python, and a page with no server behind it needs them in TypeScript. So there are two copies, tied together:

- **The words and limits are not typed twice.** `frontend/lib/listingRuleData.ts` is written from `pipeline/search_rules.py` and `compliance/trademarks.py`. Change a rule or `trademarks.txt` and `tests/test_free_tool_rules.py` fails until you write it again:
  `docker compose exec -T api python -m app.cli free-tool-rules > frontend/lib/listingRuleData.ts`
  A trademark added to the list reaches the app within seconds, and the tools at the next deploy.
- **Both copies answer the same cases.** `backend/tests/fixtures/free_tool_cases.json` is written from the Python rules (`scripts/free_tool_cases.py`). The backend tests check Python still gives those answers, and `lib/freeToolRules.test.ts` checks the browser gives the same.

Each rule on the page is either Etsy's, quoted with a link, or labelled "Our advice". The quotes must be in `docs/guide-sources.md`, and a test fails if they are not.

**Two differences from the app, on purpose:**

- **The brand warning finds the same names but uses different words.** The app says "Etsy often removes listings that use it without permission", and no page we have read says that. The public tools quote Etsy's Help pages instead.
- **The tag checker also applies "cannot start your tag with these characters".** Etsy's Help page says this about ' and -. The app does not stop on it, because the API schema does not state it.

## The fee calculator

`frontend/lib/freeTools/fees.ts` is the one dated file. While `FEE_CALCULATOR_CONFIRMED` is off the page says "Coming soon" and carries no figure.

**To turn it on, after checking the list below:**

1. In `fees.ts`, correct any figure that differs.
2. Set each fee's `status` to `"confirmed"`. A fee left as `"to confirm"` shows that tag on the public page.
3. Set `FEES_CHECKED` to the day you checked.
4. Add `FEE_CALCULATOR_CONFIRMED=true` to `.env` on the server. It is read when the site is built.
5. Deploy with `bash deploy/update.sh`, then purge the Cloudflare cache.

### Fees to confirm by hand

Read on Etsy's Help pages on 1 November 2026. Etsy's own fee page, **https://www.etsy.com/legal/fees**, refuses automated requests, so please compare each of these with it.

| Fee | Value used | Etsy page |
|---|---|---|
| Listing fee | $0.20 USD per listing | https://help.etsy.com/hc/en-us/articles/360035902374 |
| Transaction fee | 6.5% of item price, shipping and gift wrap | https://help.etsy.com/hc/en-us/articles/360035902374 |
| Payment processing | By country: a percent plus a flat fee, on item, shipping and sales tax (full table below) | https://help.etsy.com/hc/en-us/articles/115015628847-Fees-and-Payments |
| Regulatory operating fee | Canada 0.50%, France 1.14%, Hungary 1.97%, Italy 0.80%, India 0.05%, Spain 0.88%, Türkiye 1.67%, United Kingdom 0.48%, Vietnam 1.24%; on item, shipping and gift wrap | https://help.etsy.com/hc/articles/1500011073202 |
| Offsite Ads | 15% under $10,000 in 365 days, 12% at $10,000 or more, at most $100 USD an order | https://help.etsy.com/hc/en-us/articles/360000338367 |

**Please look at these in particular:**

- **Hungary.** The regulatory fee article lists Hungary at 1.97%. The [fees overview](https://help.etsy.com/hc/en-us/articles/115014483627-What-are-the-Fees-and-Taxes-for-Selling-on-Etsy-) lists the countries without Hungary. The calculator follows the article.
- **What Offsite Ads is charged on.** Etsy says "the order total" and "the amount you made from your sale before the advertising fee was applied", and defines neither. The calculator uses item plus shipping, without tax.
- **The fixed amounts in other currencies.** The listing fee and the Offsite Ads cap are in US dollars and Etsy converts them, so a seller outside the US types an exchange rate. We do not assume one.
- **Rounding.** Etsy does not say how it rounds. The calculator rounds each fee to the cent, so a real statement can differ by a cent.

**Not included, and the page says so:** currency conversion (2.5%), VAT on Etsy's fees, Etsy Ads, shipping labels, and deposit fees in some countries.

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
| Indonesia / Vietnam | 4.5% + 7000 IDR / 4.5% + 11500 VND |
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
- **Caching:** the page is private, never cached, and it is a 404 while the flag is off. It is on the sitemap and the Free tools page only in a build with the flag on, so setting the flag needs a deploy.

## Caching

The checkers and the calculator are public pages like any other (`lib/marketing/routes.mjs`). The ideas page is deliberately not listed there, so it is `private, no-store`. The Cloudflare rule is in `docs/seo.md`.
