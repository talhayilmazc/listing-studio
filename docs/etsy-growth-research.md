# Writing listings the way Etsy's current guidance says

Research behind the "Etsy recommended (short)" listing style (Part C). Only Etsy's
own Seller Handbook is used; nothing here comes from other sellers' listings (rule 2).
Read on 2026-10-06; the dates are the articles' own.

## Sources

| # | Article | Date |
|---|---|---|
| S1 | How Etsy Search Works — https://www.etsy.com/seller-handbook/article/375461474487 | updated 2025-08-26 |
| S2 | New Guidance for Listing Titles — https://www.etsy.com/seller-handbook/article/1399426136697 | 2026-04-27 |
| S3 | Keywords 101 — https://www.etsy.com/seller-handbook/article/382774281517 | updated 2025-08-26 |
| S4 | Anatomy of a Well-Crafted Listing — https://www.etsy.com/seller-handbook/article/1347574487014 | 2025-08-26 |

## What Etsy says

**Query matching (S1).** Search matches a query against the title, tags, attributes,
description, the first photo and reviews.

**Ranking (S1).** After matching: relevancy (exact matches), listing quality (several
good photos), customer service (review rating, response rate, case rate; the last
3 months), the US shipping price, conversion, a small temporary boost for new or
renewed listings, translations and the shopper's context.

**Titles (S2, S4).**
- Under 15 words.
- Say what the item is first, then its most important objective traits (colour,
  material, style).
- No subjective words ("perfect", "beautiful").
- No gifting or aspirational phrases ("gift for her").
- Holidays, occasions or recipients only when they are essential to what the item is.
- Don't repeat words.
- No price, shipping or sale information.
- Etsy does **not** say long titles are penalised. It says search now takes a "more
  holistic view", and the keywords moved out of the title belong in tags, attributes
  and the description.

**Tags (S3, S4).**
- Use all 13; each at most 20 characters.
- Multi-word, natural phrases, with variety.
- Don't repeat tags; don't duplicate the category or attribute values (Etsy already
  matches on those).
- No separate plural/singular variants: Etsy matches root words.

**Attributes (S1, S4).** Add all relevant options; attributes act like tags.

**Description (S3, S4).** Keywords naturally in the first few sentences, essential
details first, no keyword lists, don't copy the title.

## How the app applies it

| Guidance | Where | Kind |
|---|---|---|
| Max 15 words, 140 characters, product first | `pipeline/search_rules.py::SEARCH_TITLE`, prompt `content/apparel_search` | checked after writing; a failing title is rewritten once |
| No repeated word (the profile's prefix excluded) | `search_rules.title_errors(prefix=…)` | checked |
| No subjective, gift, price/sale/shipping words | `SUBJECTIVE`, `GIFT`, `COMMERCE` | checked |
| Recipient/profession only when it defines the item ("Nurse Shirt") | `GIFT` bans "for her/him/mom…", not "Nurse" | checked (the "for …" form); the rest is in the prompt |
| Holiday/occasion only when essential | prompt | **not checkable**: whether an occasion is essential is a judgement |
| 13 tags, ≤20 chars, phrases, variety | `search_rules.select_tags` + `tag_errors` | enforced: unusable tags are dropped, a short set is rewritten |
| No tag duplicating category, attribute value, another tag's root, or (when a better phrase exists) the title | `select_tags` | enforced |
| Gift, occasion, recipient phrases | tags (prompt) | — |
| All relevant attributes | `pipeline/attribute_fill.py` + the model's choice from Etsy's lists | only values Etsy lists; only what is visible or in the profile |
| Opening 1–2 sentences, naming the item and its main phrases | `search_rules.opening_errors` | checked |

## What this does not claim

The app follows Etsy's written guidance; it does not claim a ranking, click or sales
effect, and the interface says "optimised for search matching". Whether the short
style performs better in a given shop is measured from that shop's own data
(views, favourites, orders) — see `docs/analytics.md`.
