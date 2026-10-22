# UX review, October 2026: one simple app for one shop or fifteen

Written for: the Listyro team (owner and developers) planning and checking Part B.

Walked as two sellers, both from the synthetic demo account (`python -m app.cli seed-demo
--admin --shops N --groups M`; nothing real): **one shop** (no groups) and **fifteen shops in
six groups**, each shop with its own settings. Every app page at 1366 px, then 375 px. Below,
per page: what confuses, single-shop leftovers, questions asked twice or about things the app
already knows, dead ends and bugs. Then the plan, in the order it is carried out, and the
choices made where the request left room.

## What a seller meets today

### Every page: the top strip
- Four figure cards open every page (published with the app, draft listings, listings
  generated, active profiles). Two of them are about **one shop** on most pages ("this month ·
  Northwind Tees", "Active profiles in Northwind Tees") while the page (Batches, a batch, Review,
  Uploads) is not about a shop at all. On Scheduled the same cards say "all shops". A 15-shop
  seller cannot tell which numbers belong to what.
- "all listings published in the shop: unknown (last read more than 6 hours ago; open Overview
  to refresh)": a technical sentence on a page that is not Overview.

### Sidebar
- "Profiles" lists the selected shop's profiles only, flat, under the menu. With 15 shops the
  seller must switch shops at the bottom of the menu to find a profile.
- The shop switcher at the bottom changes what several pages show (Profiles, Uploads), but most
  pages are not about a shop: a single-shop leftover that makes every page feel shop-scoped.

### Upload (`/upload`)
- Three add buttons (photos, folder, ZIP) where one drop zone and one picker would do.
- "For shop [shop]" (the upload's shop): uploads are not tied to a shop; the listing's profile
  and the review page decide where it goes. Asked again on the batch page.
- "How should these photos become listings?" is asked here and again on the batch page.
- "A folder was added twice. Merge or skip it before uploading" blocked uploads (fixed in A5).
- The page also carries the whole mockup maker below the upload; it is a second tool on the
  same page with its own drop zones.

### Batches (`/batches`)
- Each card carries a shop chip ("Northwind Tees"): the batch's shop, which no longer decides
  anything once a listing has its own profile and its own target shops.
- The badge said "Failed" for a batch with one failed draft (fixed in A4).

### A batch (`/batches/{id}`)
- "Arrange photos into listings" with an "Arrange photos" button: a separate step for what the
  group cards could do in place.
- "① Shop for this batch" and a "Shop" selector on every group: the 15-shop seller is asked a
  shop for every listing before writing, and again on the review page.
- "② For all groups: Profile, Size charts, Generate content for all, Write titles and tags
  myself" then, per group, Shop, Profile, Size charts, "Write title and tags myself",
  "Generate": the same controls twice.
- The grouping question again (asked on the upload page).

### Review (`/batches/{id}/review`)
- **Two sections do the same job and contradict each other.**
  - "Shops for these drafts": a table with one column per shop (15 columns overflow; the listing
    column shrinks to "Retr…"), a tick per listing per shop, "Tick a group for every listing",
    "written for …", "change on its group", and request counts.
  - "Send to shop groups and schedule": groups or single shops, then "How" (Split evenly / Send
    the listings I choose), a listing checklist, "Preview who gets what", then a schedule form
    (Start, From, Until, per shop per day, minutes between, stagger) and "Preview the schedule".
    Its text says a listing goes to one group and no other, while the table above lets it go
    anywhere.
- "Personalization: set for all listings": every listing given one personalization; sellers
  do not do that; each listing should follow its profile.
- Request counts ("≈ 69 requests", "about 23 each", "20,000 of your 20,000 left"): removed in
  A3.
- A large "No production partner" box on every card: one line per profile now (A6).
- Hand-written cards: "The same checks apply: title length, 13 tags, 20 characters each, no
  duplicates" described rules that are no longer blocking (replaced in the hand-written fix).

### Scheduled (`/scheduled`)
- A calendar strip and a list; no per-group view, no filters, no bulk changes, no way to move a
  listing except "change" one at a time, no pause per group.
- "Schedule drafts from a batch's review page": the page cannot schedule anything itself.

### Profiles (`/profiles`)
- "Listings below are from BLUECUSTOMTEESHIRT; switch shops at the bottom of the menu": a
  single-shop page; its stat cards cover one shop; with 15 shops the list of profiles is split
  by the switcher.
- Each profile card's shops are a list; to change a shop's settings the seller opens "change"
  per card.

### Shops (`/connect`)
- Connect, groups editor and scopes on one long page; groups are edited here and on Profiles.

### Settings, Analytics, Mockup templates
- Settings: fine; time zone, trademark filter, account.
- Analytics: shop-by-shop with an "All shops" overview; request counts in the sales read
  (removed in A3).
- Mockup templates: its own flow; consistent enough; long help paragraphs above forms.

## Plan (carried out in this order)

1. **B1 Upload:** no "For shop"; one drop zone and one "Add files" picker for photos, folders
   and ZIPs together; drag photos onto a listing, between listings, onto "+ New listing"; a
   small "+" per listing and "Move to…" for phones.
2. **B2 Grouping asked once:** only on the upload page; the batch page shows the result with
   "change" while nothing is written; never after Generate.
3. **B3 Batch page:** no batch shop and no per-group shop (existing batches keep working: a
   listing's shop comes from its profile's main shop, or from the shops chosen when sending);
   one top row (Profile for all, Size charts, "Generate all", "Write them myself"); photos
   arranged in place; profile names instead of "Written for <shop>"; no shop chip on batch
   cards.
4. **B4 Review: one "Where and when" panel** (Send to, When, Summary, one button) replacing both
   sections; the per-listing "→ GROUP 1 · change"; "Each shop uses the profile's shipping, return
   and processing settings automatically." **Owner's rule (2026-10-22): every approved listing
   goes to every chosen shop; several groups give all their shops, each once; no splitting.**
5. **B5 Personalization from each listing's profile:** the batch-wide section goes; one line per
   card; "Personalization…" for selected listings; migration keeps today's values.
6. **B6 Sidebar and Profiles:** a collapsible "Profiles" section of every shop's profiles grouped
   by main shop (search above 10); the Profiles page for all shops with a shop filter.
7. **B7 Shops table on a profile:** one row per shop (shipping, returns, processing, section,
   title prefix, scheduling), editable in place by name, "apply to all shops", "copy from shop…".
8. **B8 Scheduled:** per-group summaries, a timeline by day, filters, editing times one by one
   and in bulk (Set time, Shift by, Spread between, Move to day, Publish now, Remove), preview
   then one Save and one Undo, drag to move, pause/resume per group and for all, per-group
   settings with a reflow preview; shared shops never at the same minute; a shop's daily limit
   across groups.
9. **B9 One consistent interface:** one layout per page, one primary button, the same components
   and status words everywhere, one-sentence explanations, a sidebar that follows the work, a
   short first-run guide, no single-shop leftovers; screenshots under `docs/screens/`.
10. **B10 Every feature end to end** with faked Etsy, as a 1-shop and a 15-shop seller.
11. **B11 Performance:** 500 listings, 15 shops, 6 groups; a 200-photo upload.
12. **B12 Allowance:** reported first (a hand-written listing counts 1 when created today); the
    owner decides the rule before anything changes.

## Choices made where the request left room

- **The Overview card "Etsy requests today" stays** (A3): CLAUDE.md lists showing the remaining
  API quota as a compliance element Etsy's terms require. Everything else about request counts
  is admin-only.
- **Production method and tools** (owner, 2026-10-22): not in Etsy's API; listed on each draft
  for Shop Manager and said once on the profile; nothing guessed.
- **Several groups:** all of their shops, each once (owner's rule). The old "a listing goes to
  no other group" rule and "Split evenly" are removed; existing split schedules keep their
  assignment and nothing is re-sent.
- **Terms of Service** still say "Each of your shops should have its own listings". The owner's
  change covers the app and the internal documents; changing the Terms is left to the owner
  (a new terms version asks every seller to accept again).

## Per page: removed, merged, renamed

Filled in as each item lands (B9 completes it).

| Page | Removed | Merged | Renamed |
|---|---|---|---|
| Batch | the grouping question (B2); "Shop for this batch", every group's Shop selector, the bulk shop picker, the setup note, the shop chips, "Arrange photos" (B3) | "For all groups" and the per-group buttons into one top row: Profile for all, Size charts, Generate all, Write them myself (B3); arranging into the group cards: drop a photo on another card, "+" per card, "+ New listing", unsorted photos as tiles (B3) | the grouping is a result line, "3 listings, one per folder · change", while nothing is written (B2) |
| Every page | "New upload" as the page's primary button (B9); Settings in the sidebar footer (B9); the shop-scoped top figures on pages that are not about a shop (B9) | long explanations into one sentence plus "?" (B9); status words into one chip (B9) | sidebar: "Uploads" is "Upload", "Shops" is "Shops & groups", a new Account section with Settings (B9) |
| Overview | | a first-run guide (Connect a shop, Make a profile, Upload designs, Review and send), ticked from the account's data (B9) | |
| Batch | | one primary button: Generate all, then Review listings (B9) | |
| Batches | the shop chip on every card (B3) | | |
| Profiles | "Listings below are from <shop>; switch shops at the bottom of the menu" (B6); the per-card "change" panel of three selects per shop (B7) | every shop's profiles on one page with a Shop filter (the switcher's choice) and a "By shop" breakdown; the top figures cover every shop (B6); one Shops table per profile: shipping, returns, processing, section, title prefix and a day per shop, "apply to all shops", "Copy from shop…" (B7) | |
| Sidebar | the flat list of one shop's first six profiles (B6) | every shop's profiles under "Profiles", grouped by main shop, in a section that opens and closes, with a search above ten (B6) | |
| Review | request counts (A3); "Written for <shop>" on cards (B3, now "Profile <name>"); production-partner box per card (A6); the shop matrix, "Tick a group for every listing", "Send to shop groups and schedule", "Split evenly", "Schedule…" and "Create drafts for all" (B4) | the matrix and the group panel into one "Where and when" panel: Send to, When (Now as drafts / Publish now / Spread out), a sentence with the real total, "Show each listing", one button (B4); "change on its group" into a profile picker on the card (B4); "Personalization: set for all listings" into "Personalization…" for the selected listings (B5) | "Publish all" is "Publish all drafts" (B4); each listing says "→ GROUP 1 · change" (B4); personalization is one line per card, "Personalization: from its profile (on) · change" (B5) |
| Scheduled | the "Group schedules" list of plans with Cancel, the 14-day strip, "Schedule drafts from a batch's review page" (B8) | every go-live in one timeline by day: plans' slots and scheduled drafts together; per-group summaries with Pause, Select and Settings (reflow preview); filters; bulk actions with a preview, one Save and one Undo; each row's own time; drag to another day; "Schedule listings" opens a batch's Where and when (B8) | statuses "Planned", "Making the draft", "Scheduled", "Publishing", "Published", "Failed", "Not published" (B8) |
| Upload | Merge/Skip prompt (A5); "For shop" (B1); the three add buttons (B1) | photos, folders and ZIPs in one drop zone and one "Add files" button (photos or ZIPs in one dialog, a folder in the other: a browser's dialog cannot pick both at once) (B1) | the staged folders are now the listings they become, arranged in place: drag between cards, "+ New listing", "+" per card, "Move to…" per photo (B1) |

## Screenshots

`docs/screens/` holds every main page at 1366 and 375 px after Part B, from the synthetic demo
account only (`python -m app.cli seed-demo --admin --shops 4 --groups 2`): overview, upload, batches,
batch, review, scheduled, analytics, profiles, templates, shops, settings.
