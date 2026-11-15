# Listing health

Written for whoever changes Listing health. It records why version 1 was replaced, what version 2 does, and where each marketplace limit comes from.

## 1. Diagnosis of version 1 (15 November 2026)

A seller's best-selling listing scored about 50%. Version 1 (`pipeline/listing_health.py` as of `abbc390`) ran 25 checks on every active listing and showed **the share of checks met** as a percentage, on each row and as a shop average. Every check weighed the same (one check = one point out of the checks that could be judged; "note" verdicts and attributes not yet read were left out). Nothing looked at sales.

### The rules, their weight and why they existed

| Rule | Whose | Weight | Why it was there |
|---|---|---|---|
| title_length (over 140) | Etsy | 1 | Etsy's limit |
| title_characters | Etsy | 1 | Characters the API rejects |
| title_once (% : & + twice) | Etsy | 1 | The API rejects them twice |
| title_words (15 or more) | Etsy | 1 | Help: "Consider using less than 15 words" (advice, not a limit) |
| item_first | ours | 1 | Seller Handbook title guidance (not read by a person: supports no claim) |
| repeats | ours | 1 | Same guidance |
| subjective | ours | 1 | Same guidance |
| commerce (price, shipping) | ours | 1 | Same guidance |
| gift | ours | 1 | Same guidance |
| shorter (too many parts) | ours | 1 | Same guidance |
| tag_count (over 13) | Etsy | 1 | Etsy's limit |
| tag_length (over 20) | Etsy | 1 | Etsy's limit |
| tag_characters | Etsy | 1 | Etsy's limit |
| tag_start (' or -) | Etsy | 1 | Help: cannot start a tag with them |
| tag_duplicates | ours | 1 | A wasted tag |
| tag_title_words | ours | 1 | Our advice |
| tag_phrases (9 multi-word) | ours | 1 | Our advice |
| tag_all (fewer than 13) | ours | 1 | Use all 13 |
| attributes | Etsy | 1 | Help: fill out attributes and tags |
| materials | ours | 1 | Our advice |
| photos (fewer than 5) | ours | 1 | Our rule of thumb |
| description (under 160) | ours | 1 | Our rule of thumb |
| stray_characters | ours | 1 | Junk the buyer sees |
| section | ours | 1 | Our advice |
| personalization | ours | 1 | Title says personalized, listing asks nothing |
| brand | ours | 1 | Trademark risk |

### What fires on listings that sell

Run on the synthetic shop in `backend/app/pipeline/health_fixture.py` (34 active listings, 16 with a sale in the last 60 days). The shop is **synthetic** and shaped like the early-access print-on-demand shops in general (long keyword titles, all 13 tags, no materials, some without a section); production data is never copied to a development machine, so this is a model of the problem, not a measurement of real shops. `python -m app.cli listing-health-report <e-mail>` (read only) gives the same table for a real account on the server.

Version 1's average score: **74% for the selling listings and 85% for the others.** The listings that sell scored worse than the ones that do not.

| Rule | Fires on selling (16) | Fires on not selling (18) |
|---|---|---|
| repeats | 16 (100%) | 2 |
| shorter | 16 (100%) | 18 |
| attributes | 16 (100%) | 18 |
| title_words | 14 (87%) | 0 |
| gift | 14 (87%) | 1 |
| materials | 12 (75%) | 18 |
| photos | 5 (31%) | 1 |
| subjective | 3 (18%) | 1 |
| tag_title_words | 3 (18%) | 2 |
| section | 3 (18%) | 0 |
| description, stray_characters, brand | 1 each | 0 |
| every other rule | 0 | 0 to 6 |

### Findings

- **Wrong or irrelevant on selling listings** (fire on most of them): `repeats`, `shorter`, `title_words`, `gift`, `materials`, and `attributes` when it compares with the profile's reference listing. They describe the long keyword style, which is how these listings were written and how they sell. Telling the seller to change them asks them to risk what works.
- **Not about the listing's problems at all**: `shorter` and `materials` fire on nearly every listing whether it sells or not, so they separate nothing.
- **Rules of thumb with no source**: `photos` (5), `description` (160), `section`, `tag_phrases`, `tag_title_words`, `item_first`. Ours, never measured, and nothing a seller could act on in one click.
- **Real problems** fire rarely and were drowned out: `brand`, `stray_characters`, the Etsy limits (title over 140, tag over 20, characters), and a missing main photo or required attribute (version 1 did not check either).
- **The percentage is the core mistake**: a count of style checks reads as a grade. It cannot say whether a listing is doing well, because it never asks whether it sells.

## 2. Version 2

(See below; written with the change.)
