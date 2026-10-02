# Product screenshots on the public site

The screenshots in `frontend/public/screens/` are captures of the real app,
signed in to a **demo account that holds sample data only**: designs drawn by
us (`components/marketing/Mockup.tsx`), fictional shops ("Northwind Tees",
"Juniper Print Co."), generated sales and fee totals. No real seller's shop,
design or figure appears in them, and they must stay that way.

## Retaking them

1. Seed the demo account (local development stack):

   ```bash
   docker compose exec -T api python - < backend/scripts/demo_screens_seed.py
   ```

   It prints the batch id and a session token. Put the token in a browser as
   the `session` cookie for `localhost`.

2. Capture at 1440×900 with a device pixel ratio of 2 (phone: 390×844 at 3):

   | File | Page | Framing |
   |---|---|---|
   | `review.webp` (2880×1800) | `/batches/<id>/review` | scrolled to the "Dog Mom Sweatshirt" card |
   | `review-card.webp` | the same capture | cropped to that card (shown on phones) |
   | `batch-content.webp` (2400×1800) | `/batches/<id>` | scrolled to the first groups, side rail cropped off |
   | `analytics-content.webp` (2400×1800) | `/analytics`, Overview tab | scrolled to the headline figures, rail cropped off |
   | `phone.webp` (1170×2532) | `/batches/<id>/review` on a phone | the same card |

   Save as WebP, quality about 82. If a file changes, give it a new name (or
   update `components/marketing/screens.ts` with the new size), so no image
   cache serves the old one.

3. Delete the demo account afterwards: its tenant (`demo-shots@example.test`),
   its uploads (`demo-shots/` in storage) and its Redis session. Nothing about
   it may be left in a database that holds real sellers.

The Open Graph image (`frontend/public/og-v2.png`, 1200×630) is drawn the same
way, from the same sample designs.
