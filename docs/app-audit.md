# App audit, 8 November 2026 (before app/onboarding-v1)

Every signed-in screen walked at 1366 px and 375 px, with data (the synthetic `demo-shots@example.test`
account: nine shops, three profiles, three batches) and empty (`demo-empty@example.test`: a new
account with nothing in it). Screenshots: `docs/screens/app-before/` (`<account>-<screen>-<width>.webp`;
taken with `frontend/scripts/app-shots.mjs`; pages longer than 2,400 px are cut there).

Ranked by how much each one hurts a first-time seller: **P1** stops or misleads them on the way to a
first approved listing, **P2** slows them down or makes them guess, **P3** is polish.

## The ten that hurt most

| # | Rank | Where | Problem |
|---|---|---|---|
| 1 | P1 | Overview, empty | No single next action. Three "Connect" buttons (guide, Shop card, sidebar), four metric tiles of zeros, an "Etsy requests today 20,000 of 20,000" card and "Nothing needs your attention" compete for the first screen. At 375 px the metric tiles fill the whole first screen and the guide starts below it. |
| 2 | P1 | Connect → after | After connecting a shop the seller lands back on Shops & groups (`/connect?status=connected`), not on the next step. Nothing says what to do next. |
| 3 | P1 | Profiles (hidden prerequisite) | Writing a listing needs a profile, but nothing in the upload flow says so: the batch fails with "no profile selected for this group". The guide's step "Make a profile" uses the app's word for it and links to a page that is a wall of settings (template, listing style, title lengths, personalization, size charts with a 20-slot grid on every card: 7,486 px at 1366). |
| 4 | P1 | Every page | The four-tile metric strip ("Published with the app", "Draft listings awaiting publish · all shops", "Listings generated left this month 500 / 500", "Active profiles in your library") sits above every page's own content. At 375 px it pushes everything down a full screen and the page title is cut ("Revi…", "Over…") to fit the header's "New upload" button. |
| 5 | P1 | Review | The heart of the product is 8,406 px tall for 8 listings at 1366 (17,310 px at 375). Approve is a checkbox at the bottom of each card. No keyboard, no "next listing". The "Where and when" send panel and two production-partner notes come before the first listing. |
| 6 | P1 | Navigation | Eleven entries in three sections plus each profile as a child entry. There is no "Review" destination: listings waiting for approval are found through Batches → a batch → "Review listings". "Seasonal planner" and "Listing health" sit beside the daily work. |
| 7 | P2 | Naming | One thing, many names: Upload / New upload / Upload your first folder; Batches / listing groups / groups; Shops & groups / Connect shop / Connect a shop / Connect your shop; Scheduled / Schedule listings; Analytics (sidebar) vs "profit, cost and ads reporting"; Listings generated / written; Approve all / Approved / approved for. |
| 8 | P2 | Batch page | Each listing card carries about ten controls (Edit SKU, "+", Replace images on Etsy, Profile, Publish in another shop, Size charts, Profile default, View that listing on Etsy, Adjust cover crop, Attributes) and repeats three helper sentences per card. "Profile for all", "Working on: Choose a shop…", "Found 6 groups by SKU" and "Select groups" stack above the first card. |
| 9 | P2 | Waiting | Writing listings shows a spinner on the button only; the seller cannot leave the page and be told when it is done. No app-wide "your listings are ready" note (mockups have one: MockupRunWatcher). Shop sections show "Reading this shop's sections…" with no end; profile cards say "read with the next profile refresh". |
| 10 | P2 | Empty screens | Inconsistent: Batches "No batches yet. / Upload your first folder"; Scheduled, Reports, Listing health and Profiles show filters, zeros or explanations with no button, or a button that leads somewhere that needs a shop first. |

## Everything found, by kind

### Friction points and dead ends
- **P1** Overview (empty) has no one obvious next step (see 1).
- **P1** After connecting, back on Shops & groups (see 2).
- **P1** A batch cannot be written without a profile; the failure comes after the seller clicked Generate (see 3).
- **P2** Upload, empty: "Keep your own copies: image files are stored here for a limited time…" is the second thing read on the first upload; the mockup box below asks for blank garment photos the seller may not have.
- **P2** Review → approve needs scrolling past each card's whole description, checklist and personalization.
- **P2** Scheduled, empty: group cards, four filters and "Select all shown" for nothing.
- **P2** Profiles: "Same name in different shops" box, "Shop groups" and a search above the profiles; each card carries every advanced setting.
- **P3** Settings mixes account, time zone, trademark filter and deletion on one long page with no grouping.

### Duplicate controls
- **P1** Connect a shop: guide button, Overview "Shop" card button, sidebar "Connect shop", and the Shops page button.
- **P2** "New upload" in the header of every page beside "Upload" in the sidebar.
- **P2** Review card: the profile appears twice (top "Profile Standard Tee" and the picker under the image); attributes twice ("ATTRIBUTES Primary color" and "▶ Attributes 2 of 5 set").
- **P2** Review: "Approve all (2)" and "Publish all drafts (1)" beside each other at the top, and "Publish now" per draft row.
- **P3** Shops & groups: the page title repeated as an H2 inside the card.

### Jargon and confusing labels
- **P1** "profile", "reference listing", "template" (two meanings: profile template and mockup template).
- **P2** "batch", "listing group", "group" (also used for shop groups), "rank", "processed", "with content".
- **P2** "Etsy requests today", "Drafts, publishing, Replace images, new shop sections and SKU updates count. Setting a profile up in your shops and keeping them current do not."
- **P2** "Where and when", "Now as drafts", "Spread out", "Working on:", "Size charts: Own profile", "Profile default", "carried from above".
- **P2** "Search-readiness checklist", "Item options and section", "Personalization: from its profile (read when its reference is read)".
- **P3** "Listings generated left this month 500 / 500" (the label's two halves read as one).

### Inconsistent style
- **P2** Upper-case micro labels (TITLE, TAGS, SEND TO, SHOP, ETSY REQUESTS TODAY, RECENT ACTIVITY) beside sentence-case ones.
- **P2** Several button looks for the same weight: black filled ("Review listings", "Approve all"), outlined small ("New upload", "Rename"), underlined text links doing actions ("Edit SKU", "change", "Publish in another shop").
- **P2** Status chips in amber, green, grey outlined and filled forms with different radii.
- **P3** Cards at three radii and two shadow strengths; section headers at three sizes.
- **P3** Old palette names (`brand-*`, `slate-*`, `heather`, `fabric`) beside site v3 tokens; the sidebar is a dark panel unlike the site's white header.

### Screens with no single obvious next action
- Overview (empty and with data: the metric strip, "Needs your attention", "All shops" table, Etsy requests card, recent activity and profiles carry equal weight).
- Batch page (Generate all / Review listings is clear only once the seller finds it top right; each card offers ten more).
- Review (Approve all and Publish all drafts at the top, Create N drafts inside the panel, the per-card approve box).
- Scheduled (empty), Reports (empty), Listing health (empty), Profiles (empty: the only button is "Detect from <shop>" when a shop exists, none otherwise).

### Where the seller waits without knowing what is happening
- Writing listings (Generate all): the button spins; no "Writing 3 of 12", no note when it finishes elsewhere.
- Shop listings being read after connecting: Overview says "reading…" without how long or what next.
- Profile reference read: "read with the next profile refresh", "Not read yet".
- Sections: "Reading this shop's sections…".
- Drafts being created: each card's draft row changes when the job ends; nothing tells the seller on another page.

### Errors
- **P2** Etsy's failures are explained (`etsy/errors.py::explain`) but shown as one long sentence with no action button and no way to see the code Etsy returned.
- **P3** Some failures still read like logs ("client_error", "max_attempts_exceeded" in old saved jobs: re-explained since October, but the raw text is not offered separately).

### Confirmations
- **P2** Deleting a batch asks for confirmation although it can be restored for 7 days: an undo note fits better.
- **P3** "I deleted it on Etsy" (unfinished drafts) asks with `window.confirm`; it only takes a row off a list.
- Kept as confirmations (cannot be undone): deleting a mockup template or photo, removing a subgroup, account deletion, turning the trademark filter off.

## What this audit leads to (app/onboarding-v1)
- Navigation of five work entries (Home, Designs, Review, Schedule, Reports) plus Shops and Settings; profiles, mockup templates, listing health and the season planner move under the entries they serve or under Settings.
- A first-run checklist on Home, stored on the server, that ticks itself from what the seller did; after connecting, the seller lands on "Upload your first design".
- Example designs with a saved result for a seller with nothing ready: no AI call, no usage, never sent.
- The metric strip leaves every page but Home; one primary action per page.
- Review: a focused one-listing-at-a-time screen with keyboard shortcuts, the card's checks, and per-shop overrides in a "Per shop" tab.
- Empty states with one sentence and one button; progress for writing; an app-wide note when listings are ready; an undo note for reversible actions; Etsy errors with an action and a "Details" disclosure.
- One button set, input style, radius and shadow scale from the site v3 tokens.

## What was done (app/onboarding-v1)

| # | Problem | Done |
|---|---|---|
| 1 | Home had no single next action for a new seller | Home is the server's first-run checklist (`components/SetupChecklist.tsx`, `GET /api/onboarding`): four steps, the next one the only primary button. A new account sees no metric tiles, no shop card, no "Needs your attention". One "Connect your shop" left (the checklist's); the sidebar's duplicate is gone. |
| 2 | After connecting, back on Shops | A seller with nothing uploaded lands on Upload: "Your shop is connected. Now upload your first design." (`api/auth.py`). Others still return to Shops. |
| 3 | The profile was a hidden prerequisite | Not removed: a listing still copies category, price and shipping from one of the seller's own listings. The checklist and the empty Profiles screen now say so in one sentence. Sellers with no design or profile ready can try three example listings (no AI call, nothing counted, never sent). See "Not done" in the report. |
| 4 | Metric strip on every page | Home only, and only once a shop is connected; the header's "New upload" button is gone, so page titles are no longer cut at 375 px. |
| 5 | Review: long, approve at the bottom, no keyboard | Photos large on the left with a strip of every photo; one "Approve" button with an Undo note; J/K/A/E/T/? keys; "Approve selected"; per-shop details tucked into a "Per shop" tab; the duplicate profile line removed; live checks under the tags. |
| 6 | Eleven nav entries, no Review destination | Six entries in seller words (Home, Designs, Review, Schedule, Reports, Shops) and Settings; the rest are tabs under their entry. A new Review page lists what waits for approval and what is approved and not sent. |
| 7 | Many names for one thing | One word list (`lib/words.ts`): Upload designs, Write listings, Approve/Approved, Connect your shop/another shop; page titles are the sidebar's words. |
| 8 | Batch page: ten controls per card | "Generate all" is now "Write listings" with real progress; the per-card controls were not reorganised in this pass (see "Not done"). |
| 9 | Waiting without knowing | "Writing 3 of 12" with a progress bar; a "Listings ready" note wherever the seller is; fixed-size loading skeletons. |
| 10 | Inconsistent empty screens | One component (`EmptyState`): one sentence, one primary button, on Designs, Review, Schedule, and the shop-only pages (Profiles, Reports, Listing health, Season planner). |
