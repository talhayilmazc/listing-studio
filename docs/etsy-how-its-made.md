# How it's made (Etsy's Creativity Standards)

Etsy's listing editor asks two questions on every listing:

- **How does your shop produce this item?** One answer: Made from scratch, Assembled from purchased parts, An item my shop alters, A curated set of purchased goods, or A natural material.
- **What tools are used to make this item?** Any of Handheld or hand-guided tools, Computerized tools or machines, An AI generator. Or None, which can't be combined with the others.

**The owner's rule (23 October 2026):** the seller does nothing per listing for these. There is no approval step and no checklist, and nothing about these two answers ever holds up drafting, scheduling or publishing.

## Why the profile's selector never reached Etsy

The profile stores the seller's choice:

- `listing_profile.how_made_production`;
- `listing_profile.how_made_tools`.

Every draft path builds the draft in `etsy/publisher.py`, which adds `how_made.draft_fields(profile)`. That function returns nothing, for two reasons:

- `FIELD_NAMES` is empty: Etsy publishes no field to put the answers in;
- `ETSY_HOW_ITS_MADE_FIELDS` is off.

So `createDraftListing` never carried the answers. Nothing was refused and nothing failed: there was simply nothing to send them in. No other endpoint can take them either (below).

## Evidence: Etsy's API has neither question

Checked against Etsy's official OpenAPI document. This is the generated OAS file linked from developer.etsy.com: `https://www.etsy.com/openapi/generated/oas/3.0.0.json`, "Etsy Open API v3", 76 paths. It was downloaded on 2026-10-23, and checked before on 2026-09-24, 2026-09-26 and 2026-10-22.

- **a) Readable?** No.
  - `getListing` returns 69 fields. None is about production method, tools or AI: the closest are `who_made`, `when_made`, `is_supply`, `materials` and `production_partners`, which the app already copies and verifies.
  - `getListingProperties` and `getListingProperty` return category properties (`property_id`, `property_name`, values). The 2026-09-24 check of the apparel category's properties found neither question among them. The 45 real draft responses checked that day carried no such field.
- **b) Writable?** No.
  - `createDraftListing` takes 36 body fields. Its required fields are quantity, title, description, price, who_made, when_made and taxonomy_id.
  - `updateListing` takes 30.
  - `updateListingProperty` takes only `value_ids`, `values` and `scale_id` for a category property.
  - None of them takes either question.
- **The whole document**, descriptions and enum values included, has no match for: "how it's made", "made from scratch", "assembled", "purchased parts", "alters", "curated", "natural material", "handheld", "hand-guided", "computerized", "AI generator", "artificial intelligence", "generative", "production method", "produce this item", "tools used" or "creativity". The single hit for "tools" is about inventory editing tools.
- **The live API** for one reference listing can be checked on the server with three read-only GETs: `docker compose -f docker-compose.prod.yml exec api python -m app.cli etsy-raw-listing --profile "<name>"`.
- **Etsy's developer repository** has an open request for exactly these fields (etsy/open-api discussion #1630, 22 June 2026), with no answer from Etsy.

Listyro never automates Etsy's website (no browser automation, extensions or internal endpoints, ever: CLAUDE.md rule 1). So the answers can't be set automatically today.

## Has a missing answer ever blocked a publish?

No evidence that it has.

- **The spec**: activation is `updateListing` with `state=active`, which has no required How it's made field.
- **The stored records** (the local database, read-only):
  - 41 stored Etsy publish replies and 52 draft replies; none mentions these questions.
  - 28 publishes through the API succeeded, although the app has never sent these answers.
  - The 13 failed publishes were 6 Etsy 404s (listing not found) and 7 internal errors on 2026-09-24, from before Etsy's replies were stored; none names these questions.
  - The 17 failure messages that matched "production" were the app's own note about stale production partners.
- **On the server**: `python -m app.cli draft-failures <email>` shows the production records the same way.

Should Etsy ever refuse a publish over them, the seller is told exactly what to set (`etsy/errors.py::HOW_MADE_FIX`): "Etsy wants How it's made set on this listing before it goes live: in Shop Manager open it (or select several under Listings, then Editing options), set … then publish it again."

That listing alone fails. Every other listing in a scheduled run goes live as planned: each go-live is its own job (`tests/test_how_made.py::test_a_scheduled_run_goes_on_after_one_listing_is_refused`).

## What the app does

- **Profile**:
  - The seller answers both questions once per profile. "None" stands alone.
  - The card shows "How it's made: shop alters it · computerized tools · change", with one line under it: "Etsy doesn't let apps set this yet, so it can't be sent automatically. You can set it for many listings at once in Shop Manager → Listings → select → Editing options."
  - The profile's Shops table has "Apply to all profiles…".
- **Nothing per listing**:
  - No step on a draft, no line on a review card or in Where and when.
  - No sidebar count, no "Finish in Etsy" page; it was removed on 2026-10-23, and migration `0090_drop_how_made_opened` drops its column.
  - The first-run guide doesn't ask for the answers.
- **Nothing waits on them**: a draft, a schedule and a publish go on whether or not they are set.
- **Ready to send** the day Etsy adds the field: every draft path calls `how_made.draft_fields(profile)`, in every shop. Its source order:
  1. the reference listing's own answers, read when the profile is refreshed (`cached_payload["how_made"]`);
  2. else the profile's selection.
- **Watched**: every Monday at 05:10 UTC, `workers/etsy_spec.py` downloads the document. If a production, tools or AI field appears on createDraftListing, updateListing or getListing, it alerts `ALERT_WEBHOOK_URL` once with the field names. Nothing changes automatically.

## Turning on sending, once Etsy adds the fields

1. Read the new fields in Etsy's OpenAPI document: their names, their allowed values, and whether getListing returns them.
2. In `backend/app/pipeline/how_made.py`:
   - fill `FIELD_NAMES` (`{"production": "<Etsy field>", "tools": "<Etsy field>"}`);
   - fill `VALUES` (our answer key → Etsy's value, per field).
3. In the profile refresh (`workers/profiles.py`), store the reference listing's answers under `cached_payload["how_made"]` (`{"production": <key>, "tools": [<keys>]}`), so they come first.
4. Add a read-back check next to the Creativity Standards checks in `etsy/publisher.py`. If Etsy doesn't keep the answers, show a plain warning on the card and continue; never stop the draft or the schedule.
5. Update `tests/test_how_made.py` and the end-to-end fake's `CREATE_FIELDS` in `tests/test_end_to_end.py`.
6. Deploy with `bash deploy/update.sh`, set `ETSY_HOW_ITS_MADE_FIELDS=true` in the server's `.env`, and run `docker compose -f docker-compose.prod.yml up -d api worker`.
7. Smoke-test one draft on the test shop only, then check the listing in Etsy's editor.
8. Update this file, CLAUDE.md and `docs/etsy-compliance.md`.

With the setting off, or with no field names, nothing is sent.
