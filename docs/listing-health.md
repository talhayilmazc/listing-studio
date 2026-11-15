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

Run on the synthetic shop in `backend/tests/health_fixture.py` (34 active listings, 16 with a sale in the last 60 days). The shop is **synthetic** and shaped like the early-access print-on-demand shops in general (long keyword titles, all 13 tags, no materials, some without a section); production data is never copied to a development machine, so this is a model of the problem, not a measurement of real shops. For a real account on the server, `python -m app.cli listing-health-report <e-mail>` (read only, counts only) gives version 2's buckets and how often each problem fires on selling and non-selling listings.

Version 1's average score: **76% for the selling listings (lowest 64%) and 88% for the others.** The listings that sell scored worse than the ones that do not.

| Rule | Fires on selling (16) | Fires on not selling (18) |
|---|---|---|
| repeats | 16 (100%) | 2 |
| shorter | 16 (100%) | 18 |
| title_words | 14 (87%) | 0 |
| gift | 14 (87%) | 1 |
| materials | 12 (75%) | 18 |
| attributes (vs the reference listing) | 7 (43%) | 2 |
| photos | 5 (31%) | 1 |
| subjective | 3 (18%) | 1 |
| tag_title_words | 3 (18%) | 2 |
| section | 3 (18%) | 0 |
| description, stray_characters, brand | 1 each | 0 |
| every other rule | 0 | 0 to 6 |

### Findings

- **Wrong or irrelevant on selling listings** (fire on most of them; `attributes` on almost half, by comparing with the profile's reference listing): `repeats`, `shorter`, `title_words`, `gift`, `materials`. They describe the long keyword style, which is how these listings were written and how they sell. Telling the seller to change them asks them to risk what works.
- **Not about the listing's problems at all**: `shorter` and `materials` fire on nearly every listing whether it sells or not, so they separate nothing.
- **Rules of thumb with no source**: `photos` (5), `description` (160), `section`, `tag_phrases`, `tag_title_words`, `item_first`. Ours, never measured, and nothing a seller could act on in one click.
- **Real problems** fire rarely and were drowned out: `brand`, `stray_characters`, the Etsy limits (title over 140, tag over 20, characters), and a missing main photo or required attribute (version 1 did not check either).
- **The percentage is the core mistake**: a count of style checks reads as a grade. It cannot say whether a listing is doing well, because it never asks whether it sells.

## 2. Version 2 (`fix/listing-health-v2`)

No percentage, no score, no average. Every listing goes into exactly one bucket (`pipeline/listing_health.py::classify`), in this order:

1. **Fix now**: real problems only, whether or not the listing sells (below).
2. **Selling**: a sale in the last `LISTING_HEALTH_SELLING_DAYS` (60). Its title and tags are never questioned. It shows one note: "Changing the title or tags of a selling listing can lower its search position." Only a Fix now problem moves it, and then only that problem and its own fix are shown.
3. **Too new to judge**: younger than `LISTING_HEALTH_NEW_DAYS` (30, from `original_creation_timestamp`) and no sale. Quiet.
4. **Not judged yet**: the shop's sales have not been read (no finished sales read, or "Delete my sales history" is younger than the window). Nobody can say what sells, so no title or tag change is suggested; the page says so and links to Reports.
5. **Worth improving**: no sale in the window **and** at least one concrete issue with a one-click fix.
6. **Nothing to fix**: no sale, and nothing we can fix with one click. Left alone.

The page shows three counts (Fix now, Worth improving, Selling), each a filter. "To do" (Fix now and Worth improving) is the default, sorted Fix now first, then by past sales (the 13 months kept), then favourites. Each problem is one sentence with whose rule it is (Etsy's, quoted with its page, or "Our advice") and its fix. Both windows are settings (`backend/app/core/config.py`).

The server, not the page, decides which fixes a listing may get (`api/listing_health.py::_refused`): a preview of "Add missing tags" or "Rewrite title and tags" for a selling listing is refused ("it is selling: its title and tags are left as they are") unless one of its Fix now problems offers that fix.

### Fix now

| Problem | Fix | Source (checked 15 November 2026) |
|---|---|---|
| `brand`: a name on our trademark list, in any form, in the title or tags | Rewrite title and tags; Edit in Shop Manager | Ours (`compliance/trademarks.py`, the same sentence everywhere) |
| `title_rejected`: over 140 characters, a character Etsy does not take, % : & + more than once | Rewrite; Edit | [How to Create a Listing](https://help.etsy.com/hc/en-us/articles/115015628707-How-to-Create-a-Listing): "A listing's title can be up to 140 characters long." The character rules are the API schema's (`createDraftListing`, `docs/guide-sources.md`) |
| `tags_rejected`: more than 13, over 20 characters, a character Etsy does not take, starting with ' or - | Fix tags Etsy rejects (`fix_tags`: only those change, no AI, nothing counted) | [How to Use Tags](https://help.etsy.com/hc/en-us/articles/360000336307-How-to-Use-Tags-to-Get-Found-in-Search): "You can use up to 13 tags per listing." "Each tag can contain up to 20 characters. Tags can only include spaces, letters, numbers. You're able to use ' and - within words and phrases, but cannot start your tag with these characters." |
| `main_photo`: no photo, or the first has no image | Edit in Shop Manager | [Listing Image Requirements](https://help.etsy.com/hc/en-us/articles/44297885802647-How-Do-Etsy-s-Listing-Image-Requirements-Apply-to-My-Listings): "Make your first listing photo the clearest, most realistic representation of the item buyers will receive." |
| `required_attribute`: an attribute Etsy marks required in the category is empty | Fill attributes (from the reference listing, same category) or Edit | "Required" is Etsy's own flag (`is_required` from `getPropertiesByTaxonomyId`, read at the profile's refresh). Judged only once the listing's attributes are read and a profile in the same category knows them. The Help page does not name required attributes |
| `stopped_selling`: inactive, expired or sold out, and it sold in the 13 months kept | Edit in Shop Manager (nothing is renewed for the seller) | [How to Renew or Hide Your Listings](https://help.etsy.com/hc/en-us/articles/360000344368-How-to-Renew-or-Hide-Your-Listings): "If an item has been purchased or its listing has expired, you can start selling it again by renewing the listing." [How to Deactivate a Listing](https://help.etsy.com/hc/en-us/articles/360000336187-How-to-Deactivate-a-Listing): "Deactivating listings removes them from your shop page, and buyers can no longer buy them." |
| `stray_characters`: invisible or leftover code characters (as `scan-ai-text` finds) | Remove stray characters (`clean_text`) | Ours |

### Worth improving (only when the listing has not sold in the window)

| Problem | Fix | Source |
|---|---|---|
| `tags_missing`: fewer than 13 distinct tags | Add missing tags (from the reference listing) or Rewrite | Etsy: "You can use up to 13 tags per listing." |
| `attributes_missing`: the reference listing (same category) fills an attribute this one leaves empty | Fill attributes | [How to Use Attributes](https://help.etsy.com/hc/en-us/articles/115014502508-How-to-Use-Attributes-When-Listing-an-Item): "Attributes can also help buyers find your items more easily in search results." |
| `title_guidance`: 15 or more words, repeated words, opinion, price, shipping or gift words (not for a listing the app wrote in the profile's long keyword style) | Rewrite | Etsy: "Consider using less than 15 words when including the traits that make your item special." The other parts are our advice |
| `dated_tags`: a tag naming a holiday with a year that has passed ("christmas 2024") | Rewrite; season planner | Ours |

A listing that is not selling and names a holiday also gets a quiet suggestion with the holiday's next date and a link to the season planner. It moves the listing into no bucket.

### Removed from version 1, and why

| Rule | Why |
|---|---|
| The percentage, the shop average, "With something to look at" | A share of style checks reads as a grade, and it graded selling listings below non-selling ones |
| `repeats`, `shorter`, `title_words`, `gift`, `subjective`, `commerce`, `item_first` on every listing | They describe the long keyword style the selling listings use. Now only `title_guidance`, only for listings that are not selling |
| `materials`, `photos` (5), `description` (160), `section`, `tag_phrases`, `tag_title_words`, `tag_duplicates`, `personalization` | Our rules of thumb, never measured, firing on most listings, and none fixable in one click |
| `attributes` on every listing | Now `attributes_missing` (only when not selling) and `required_attribute` (Fix now) |
| `tag_start` as advice | Etsy's Help says a tag cannot start with ' or -: now part of `tags_rejected` |

### On the fixture shop

`backend/scripts/health_diagnosis.py` (synthetic, 38 listings): Fix now 7 (two of them selling: a brand name, stray characters), Worth improving 5, Selling 14, Too new 4, Nothing to fix 6; 1 expired listing that never sold is not listed. No selling listing is in Worth improving. `tests/test_listing_health.py::test_the_fixture_shop_falls_into_the_buckets_the_docs_give` keeps these numbers true. Screenshots: `docs/screens/listing-health-v2/` (before and after, the synthetic demo account).

## 3. AI rewrites of live listings count (`core/usage.py`)

"Rewrite title and tags" rewrites a listing that is already live on Etsy. It is not a `generated_content` row, so the listing counter did not see it: free AI use. Now:

- Each live Etsy listing counts **once per allowance period** when our AI rewrites it, however many times it is rewritten in that period. Its row has its own key, `live:<shop connection id>:<Etsy listing id>:<period start, UTC>`, unique per account like every other row, so two rewrites at once count once (tested on Postgres).
- If the Etsy listing is a Listyro listing already counted **in this period** (its first AI write or first draft), the rewrite adds nothing. Counted in an earlier period: this period's rewrite counts once.
- The row is written in the same transaction as the saved AI result (`workers/listing_fixes.py::write_listing_fix_text`). A failed or unusable AI call counts nothing.
- Before the AI is called, the allowance is checked: the preview refuses with the plain allowance sentence (429), and the job checks again and stops with it, without calling the AI, if the last listing was used in between. No draft, go-live or schedule path reads any of this.
- `ListingFix.allowance_kind` says what happened (`live_counted`, `live_free`). `usage-reconcile` lists live rewrites marked counted with no row; their rows are never orphans (the fix itself is kept only 7 days). Admin (Users and Publishing) and `limits-report` show how many of the period's listings are live rewrites (`AllowanceStatus.live_rewrites`). No migration: `usage_event` already has every column.
