# Product screenshots on the public site

The screenshots in `frontend/public/screens/` are crops of the real app, signed
in to a **synthetic demo account that holds sample data only**: designs drawn by
us (`components/marketing/Mockup.tsx`), fictional shops ("Northwind Tees",
"Juniper Print Co."), generated sales and fee totals. No real seller's shop,
design or figure appears in them, and they must stay that way. On the home page
each one carries the label "Sample data"; the two feature cards that are
drawings, not captures, are labelled "Illustration".

## Retaking them

1. Seed the demo account (local development stack, never production):

   ```bash
   docker compose exec -T api python -m app.cli seed-demo
   ```

   It creates `demo-shots@example.test` with one batch ("Summer drop"), two
   shops, two profiles, one scheduled listing and a year of generated fee
   totals, and prints a password to sign in with.

2. Capture each crop below. Every file is **4:3**, saved as WebP (quality about
   80) at 1120×840, cut from the top-left of the region named. Use the app's
   phone layout where a width is given, so the text is large enough to read in
   a card; the browser's time zone is set to the account's (America/Chicago).

   | File | Page | Width, scale | The region |
   |---|---|---|---|
   | `feature-text-2.webp` | `/batches/<id>/review` | 1366, 2x | first listing card: Title, Tags and Attributes |
   | `feature-approve.webp` | `/batches/<id>/review` | 430 (phone), 3x | first listing card, from "Saved / Approved" down: the draft, "Publish now", the scheduled time |
   | `feature-shops.webp` | `/batches/<id>/review` | 430 (phone), 3x | "Shops for these drafts", from the first shop's tick box |
   | `feature-schedule.webp` | `/scheduled` | 520 (phone), 3x | from the calendar down to the scheduled listing |
   | `feature-uploads.webp` | `/batches/<id>`, "Arrange photos" open | 700 (phone), 2x | the first four listing groups |
   | `feature-report.webp` | `/analytics`, Month tab | 430 (phone), 3x | the "Etsy fees" lines |

   If a picture changes, give the file a new name and update
   `frontend/lib/homeCopy.ts`, so no image cache serves the old one. Nothing in a
   crop may show a real e-mail address: the app's footer (which prints the
   support address of the machine it runs on) is outside every region above.

3. Delete the demo account afterwards:

   ```bash
   docker compose exec -T api python -m app.cli seed-demo --remove
   ```

   It removes the demo account with everything of its own, its sample image
   files (`demo-shots/` in storage) and the sample invite requests. Nothing
   about it may be left in a database that holds real sellers.

## The Open Graph image

`frontend/public/og-v3.png` (1200×630) is drawn from the home page's own words
(`HOME.hero` in `frontend/lib/homeCopy.ts`) and the same sample designs: the
wordmark, the hero heading and four sample cards. It names no one but Listyro.
If the hero heading changes, draw it again under a new file name and update
`app/layout.tsx` and `app/(marketing)/page.tsx`.
