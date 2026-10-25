# Release checklist: 20 minutes on the test shop after each deploy

Run this right after `bash deploy/update.sh` has finished, on the production site, signed in to **your own test account** with the **test shop** (and a second test shop for step 7).

- Never use a seller's account or shop for this.
- Every step goes to real Etsy.
- Everything you make stays a draft, except the one listing you publish in step 11.
- Delete what you made in Shop Manager when you're done.

Each step says what to click and what you should see. If a step shows something else, stop. Note the step, the time and what you saw, and check `docker compose -f docker-compose.prod.yml logs api worker --since 30m` before going on.

Before you start, have ready:

- two or three design PNGs with transparent backgrounds;
- one design file name with a SKU in it (e.g. `TEST101-front.png`).

## 1. Sign in and look around (1 min)

1. Open the site and sign in.
   - You land on **Overview**.
   - No red "Your shops could not be loaded" banner.
   - The **Etsy requests today** card shows a number.
2. Open each item in the sidebar once.
   - Every page loads.
   - Nothing says "Listyro could not finish this just now".

## 2. Shops & groups: connect (2 min)

1. Open **Shops & groups**.
   - The test shop is listed as connected.
   - The trademark notice is beside **Connect a shop**.
2. If the test shop was disconnected, press **Connect a shop**. Sign in to Etsy as the test shop's owner and allow access.
   - You come back to Shops & groups.
   - "Shop connected. Its listings are being fetched now." appears.
   - The shop's counts say "reading…" for a moment, then show numbers (never 0 while reading).
3. Optional: start a connection here, then open Etsy's link in a **private window** that is not signed in to Listyro.
   - You land on Shops & groups with "That shop was not connected: this browser is not signed in to the account that started the connection…".
   - No shop is added. This is the protection added in this release.

## 3. Profiles: refresh (2 min)

1. Open **Profiles** and pick the test shop's profile (or press **Use as profile** on one of the test shop's listings).
2. On its card, press **Refresh now**.
   - "Refreshing…", then the card shows the reference listing's picture and "kept up to date automatically" (or "refreshed when opened or used"), with no "Couldn't refresh this profile" box.
3. On the card, check the line **How it's made: …**. If it says "not set · set it":
   - press **set it**;
   - choose **An item my shop alters** and **Computerized tools or machines**;
   - press **Save**.
   - The line now reads "How it's made: shop alters it · computerized tools".

## 4. Upload (2 min)

1. Open **Upload** and drop your design files (or press **Add files**).
   - Each file becomes a card.
   - The SKU in the file name groups the right files together.
2. Press **Upload N items**, then **Open batch**.
   - The batch page lists your groups.
   - Nothing says "too large".

## 5. Generate (3 min)

1. On the batch page, choose the profile for the groups (if asked) and press **Generate all**.
   - "Generating 1 of N…" counts up.
   - Each group ends with a title, 13 tags and a description.
   - The top strip's **Listings generated** goes up by the number of groups.
2. Open the same batch in a second tab and press **Generate all** there while the first is still writing.
   - The second tab says "Listings are being written in another tab or window right now…" and stops.
   - Nothing is written twice.

## 6. Review (2 min)

1. Press **Review listings**.
   - Each card shows its photo, title, tags and description.
   - Nothing on the card asks you to tick How it's made (there is nothing to do per listing).
2. Edit one title, then reload the page.
   - The edit is still there (it saved as you typed).
3. Press **Approve all**.
   - One summary line appears.
   - The cards are marked approved.

## 7. Drafts in two shops (3 min)

1. In **Where and when**, press **Send to** and choose a group that has both test shops (or the two shops under "or one shop").
2. Choose **Now as drafts**.
   - The sentence reads like "N listings → 2 shops: 2N drafts."
   - The button says **Create 2N drafts**.
3. Press it.
   - Each card's drafts appear, one per shop, each with **Edit draft ↗**.
   - In Shop Manager, both shops have the drafts, with photos, price, variations and shipping profile set.
   - In the second shop, they use that shop's own shipping, return and processing profiles.
   - None of them is active.

## 8. Calculated shipping (1 min, if the test shop has a calculated shipping profile)

1. Make sure the profile's reference listing uses a calculated shipping profile and has a weight and a package size.
2. Check one of the drafts from step 7 in Shop Manager.
   - Its weight and size match the reference exactly (e.g. 0 lb 4 oz, 10 × 8 × 2 in).
   - It was not refused for a missing `item_weight`.

## 9. How it's made (1 min)

1. On **Profiles**, the profile card's line reads "How it's made: shop alters it · computerized tools · change".
   - Under it: "Etsy doesn't let apps set this yet, so it can't be sent automatically. You can set it for many listings at once in Shop Manager → Listings → select → Editing options."
2. In Shop Manager, open one of the drafts from step 7.
   - Its How it's made answers are empty: Etsy's API can't set them. Nothing in Listyro waited on them.
3. Optional: in Shop Manager → Listings, select the new drafts, open **Editing options** and set both answers for all of them at once.

## 10. Schedule and pause (2 min)

1. Back on the review page, in **Where and when** choose **Spread out**, check the summary line and press **Schedule N listings from …**.
2. Open **Scheduled**.
   - The listings are there with times in your time zone (e.g. "5:00 PM CDT").
3. On the group's summary, press **Pause**.
   - The group says it is paused.
   - Nothing goes live while it is paused.
4. Press **Resume**.

## 11. Publish now (1 min)

1. On **Scheduled**, select one listing and press **Publish now**, then **Save**.
   - Within a minute or two that listing is active on Etsy (check its link).
   - In Listyro its card shows **Live**.
2. Set that listing back to draft or delete it in Shop Manager.

## 11a. Personalization that Etsy would refuse (1 min)

1. On **Profiles**, open a profile's Personalization **edit** and paste instructions over 120 characters.
   - The counter turns red ("131 / 120 characters"), the rule is listed and **Save** stays off.
2. Press **Cancel**. Nothing changed.
3. If a profile shows the amber "Its personalization breaks Etsy's rules" panel: the offer beside the
   original changes nothing until **Use this**; after it, the panel goes and held listings go ahead.

## 12. Clean up (1 min)

1. In Shop Manager, delete the drafts and the listing you made.
2. In Listyro, delete the test batch: on **Batches**, tick it and confirm **Delete 1 batch?**.
   - "Published with the app" keeps its count: publication records outlive the batch.
3. Open **Overview**.
   - No red banners.
   - **Needs your attention** lists nothing unexpected.

If every step showed what it says, the release is good. Write the date and "release checklist passed" in your deploy notes.
