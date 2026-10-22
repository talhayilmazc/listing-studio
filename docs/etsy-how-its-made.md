# How it's made (Etsy's Creativity Standards)

Etsy's listing editor asks two questions on every listing:

- **How does your shop produce this item?** One answer: Made from scratch, Assembled from purchased parts, An item my shop alters, A curated set of purchased goods, or A natural material.
- **What tools are used to make this item?** Any of Handheld or hand-guided tools, Computerized tools or machines, An AI generator. Or None, which can't be combined with the others.

## What Etsy allows today

- **The API has neither field.** Checked on 22 October 2026 against Etsy's OpenAPI document (getListing response, createDraftListing and updateListing requests). The fields that come close are already handled:
  - `who_made`, `when_made` and `is_supply`, copied from the reference listing and read back.
  - `production_partner_ids`, mapped per shop.
- **Etsy hasn't answered the request for them.** Etsy's developer repository has an open request for exactly these fields (etsy/open-api discussion #1630, 22 June 2026). There was no reply from Etsy as of 21 October 2026.
- **We never automate Etsy's website.** No browser automation, extension, scraping or internal endpoints, ever (API Terms of Use; CLAUDE.md rule 1). The seller ticks the answers in Etsy's own editor.
- **The server can check what the live API returns.** This makes three read-only GETs and prints the raw JSON:

  ```
  docker compose -f docker-compose.prod.yml exec api python -m app.cli etsy-raw-listing --profile "<name>"
  ```

## The workflow

1. **Answer once per profile.**
   - Where: the profile card's line, "How it's made: shop alters it · computerized tools · change". A profile without answers says "How it's made: not set · set it".
   - The Overview first-run guide counts a confirmed profile without answers as unfinished.
   - Bulk: the profile's Shops table has "Apply to all profiles…", which gives every confirmed profile the same answers after one confirmation.
   - Stored in `listing_profile.how_made_production` and `how_made_tools`. Validated by `pipeline/how_made.py::clean` and checked again in `lib/howMade.ts`.
2. **Review cards and "Where and when" say what to tick.**
   - With answers: "After publishing, tick in Etsy: shop alters it · computerized tools", with a link to Finish in Etsy.
   - Without answers: the line asks the seller to set them on the profile first.
   - It never blocks approving, drafting, scheduling or publishing.
   - Each draft's two steps say Etsy's exact wording, for example "Tick in Etsy: An item my shop alters."
3. **Finish in Etsy** (`/batches/finish`, sidebar under Batches, with a count badge).
   - What it lists: every listing Listyro created, draft or active, in a connected shop, that isn't marked done. Grouped by the answers to tick ("Shop alters it + computerized tools: 24 listings"), then by shop.
   - Each row: thumbnail, title, shop, state, "Open in Etsy" (the listing in Etsy's editor, `https://www.etsy.com/your/shops/me/listing-editor/edit/<id>`, for drafts and active listings alike) and "Done".
   - Bulk: select rows, then "Mark done". "Open next" opens the group's listings one by one in a new tab.
   - The top line tells the two apart. Drafts: open, tick, press Publish. Active listings: open, tick, save.
   - "Done" uses the same two ticks a draft's steps keep (`listing_publication.manual_done`), so ticks made on the review card count here too.
4. **Noticing a draft that went live.**
   - Opening a draft from the page starts a watch for three days (`listing_publication.how_made_opened_at`).
   - The read: `watch_opened_drafts` reads those drafts' state, one getListingsByListingIds request per 100 listings. It runs at most once a minute per shop while the page is open, and every ten minutes from the cron.
   - Cost: it counts as upkeep against the app's budget, never against the seller's ceiling.
   - A draft that is live now asks "Mark done?" in one click. **Nothing is ever marked done by itself**, because the app can't read the answers.

## Watching for the fields

- **What runs.** Every Monday at 05:10 UTC the worker downloads Etsy's public OpenAPI document (`ETSY_OPENAPI_URL`). It's a file, not an API call: no token and no quota.
- **What it looks at.** It lists the fields of createDraftListing, updateListing and getListing (`workers/etsy_spec.py`).
- **When it alerts.** A field whose name looks like production, tools, making or AI, and isn't one of today's (`processing_min/max`, `production_partner_ids`, `production_partners`), triggers one alert to `ALERT_WEBHOOK_URL` with the field names. The same alert isn't repeated for 30 days.
- **What it never does.** It changes nothing.
- **Locally.** It never runs with `ETSY_CALLS_BLOCKED`.

## Turning on sending, once Etsy adds the fields

1. Read the new fields in Etsy's OpenAPI document: their names, their allowed values, and whether getListing returns them.
2. In `backend/app/pipeline/how_made.py`:
   - fill `FIELD_NAMES` (`{"production": "<Etsy field>", "tools": "<Etsy field>"}`);
   - fill `VALUES` (our answer key → Etsy's value, per field).

   Every draft path builds its body in `etsy/publisher.py`, which already adds `how_made.draft_fields(profile)` in every shop. That covers generated, edited and hand-written drafts, drafts to another shop, and scheduled and planned drafts.
3. Add a read-back check next to the Creativity Standards checks in `publisher.py`. Mark both ticks done when the read-back matches, so Finish in Etsy no longer lists those listings.
4. Update `tests/test_how_made.py` (`test_nothing_is_sent_today…`) and the end-to-end fake's `CREATE_FIELDS` in `tests/test_end_to_end.py`.
5. Deploy with `bash deploy/update.sh`, set `ETSY_HOW_ITS_MADE_FIELDS=true` in the server's `.env`, and run `docker compose -f docker-compose.prod.yml up -d api worker`.
6. Smoke-test one draft on the test shop only, then check the listing in Etsy's editor.
7. Update this file, CLAUDE.md and `docs/etsy-compliance.md`.

With the setting off, or with no field names, nothing is sent.
