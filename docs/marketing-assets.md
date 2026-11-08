# Marketing assets: sources and licences

Every picture the public site shows, where it comes from and under what licence. `lib/siteDesign.test.ts`
fails if a file in `frontend/public/` (or an app icon) is not listed here with its licence.

Rules: no generated images; no photo shows a visible brand logo or someone else's printed design; the designs on
the shirts are the demo account's own drawings (`backend/scripts/demo_screens_seed.py`), placed by our own mockup
engine, the same code sellers use (`backend/scripts/hero_assets.py`, then `frontend/scripts/hero-images.mjs`).

## The blank shirt photos (site v3, 8 November 2026)

Downloaded from Pexels' image server at 1600 px; each page was read on 8 November 2026 for the title,
the photographer and the licence label ("Free", linking to https://www.pexels.com/license/). Pexels' licence:
"All photos and videos on Pexels are free to use." "Attribution is not required." "You can modify the photos
and videos from Pexels." Not allowed, among others: selling unaltered copies, implying endorsement by people or
brands shown, redistributing on other stock sites, or using a photo as part of a trademark. We show modified
photos (our mockups) on our own pages only. Unsplash was not used: its pages refuse automated requests, and we
did not work around that.

| Photo | Page | Photographer | Licence | What it shows |
|---|---|---|---|---|
| `white-flat.jpg` | https://www.pexels.com/photo/white-t-shirt-on-white-surface-12039633/ | Mockupbee | Pexels License (free to use, modified and commercially; no attribution required) | White T-shirt, flat lay on a light ground |
| `white-rack.jpg` | https://www.pexels.com/photo/white-t-shirt-hanging-on-a-rack-11671964/ | Marina Podrez | Pexels License (free to use, modified and commercially; no attribution required) | White T-shirt on a hanger and a rack |
| `black.jpg` | https://www.pexels.com/photo/a-black-shirt-hanging-on-the-wall-8532616/ | Hanna Pad | Pexels License (free to use, modified and commercially; no attribution required) | Black T-shirt on a hanger against a wall |
| `pink.jpg` | https://www.pexels.com/photo/pink-long-sleeve-shirt-hanging-on-white-metal-bar-9558761/ | MART PRODUCTION | Pexels License (free to use, modified and commercially; no attribution required) | Pink T-shirt on a rack (Pexels' title says long sleeve; it is a short-sleeve tee) |
| `orange.jpg` | https://www.pexels.com/photo/a-person-holding-a-clothes-hanger-with-orange-shirt-8148577/ | Vie Studio | Pexels License (free to use, modified and commercially; no attribution required) | Orange T-shirt on a hanger, held by a hand |
| `mint.jpg` | https://www.pexels.com/photo/flat-lay-of-mint-green-t-shirt-with-cotton-stems-34156905/ | dayong tien | Pexels License (free to use, modified and commercially; no attribution required) | Folded mint T-shirt with cotton stems |
| `navy.jpg` | https://www.pexels.com/photo/dark-blue-shirt-on-hanger-22441317/ | dayong tien | Pexels License (free to use, modified and commercially; no attribution required) | Dark blue short-sleeve shirt with a front placket, on a hanger from a branch |

No moss-green or mustard-yellow blank tee with a plain background was found on Pexels; orange stands in for
mustard and mint for green. The navy one is a short-sleeve shirt with a front placket, not a plain tee.
The original photos are not committed; the page above and the id in it are enough to fetch each again.

## Every file in `frontend/public/` and the app icons

| File | Source | Licence |
|---|---|---|
| `brand/icon-192.png` | Our logo (`scripts/logo-assets.py`, `scripts/logo-icons.mjs`; wordmark outlines from Archivo) | Ours; Archivo is under the SIL Open Font License 1.1 |
| `brand/icon-512-maskable.png` | Our logo (`scripts/logo-assets.py`, `scripts/logo-icons.mjs`; wordmark outlines from Archivo) | Ours; Archivo is under the SIL Open Font License 1.1 |
| `brand/icon.svg` | Our logo (`scripts/logo-assets.py`, `scripts/logo-icons.mjs`; wordmark outlines from Archivo) | Ours; Archivo is under the SIL Open Font License 1.1 |
| `brand/logo-dark.svg` | Our logo (`scripts/logo-assets.py`, `scripts/logo-icons.mjs`; wordmark outlines from Archivo) | Ours; Archivo is under the SIL Open Font License 1.1 |
| `brand/logo.svg` | Our logo (`scripts/logo-assets.py`, `scripts/logo-icons.mjs`; wordmark outlines from Archivo) | Ours; Archivo is under the SIL Open Font License 1.1 |
| `brand/mark.svg` | Our logo (`scripts/logo-assets.py`, `scripts/logo-icons.mjs`; wordmark outlines from Archivo) | Ours; Archivo is under the SIL Open Font License 1.1 |
| `hero/colour-black-mountain-2x.avif` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-black-mountain-2x.webp` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-black-mountain.avif` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-black-mountain.webp` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-mint-mountain-2x.avif` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-mint-mountain-2x.webp` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-mint-mountain.avif` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-mint-mountain.webp` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-navy-mountain-2x.avif` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-navy-mountain-2x.webp` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-navy-mountain.avif` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-navy-mountain.webp` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-orange-mountain-2x.avif` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-orange-mountain-2x.webp` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-orange-mountain.avif` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-orange-mountain.webp` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-pink-mountain-2x.avif` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-pink-mountain-2x.webp` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-pink-mountain.avif` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-pink-mountain.webp` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-white-flat-mountain-2x.avif` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-white-flat-mountain-2x.webp` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-white-flat-mountain.avif` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/colour-white-flat-mountain.webp` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-mint-mountain-2x.avif` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-mint-mountain-2x.webp` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-mint-mountain-4x.avif` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-mint-mountain-4x.webp` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-mint-mountain.avif` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-mint-mountain.webp` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-navy-mountain-2x.avif` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-navy-mountain-2x.webp` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-navy-mountain-4x.avif` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-navy-mountain-4x.webp` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-navy-mountain.avif` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-navy-mountain.webp` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-white-flat-mountain-2x.avif` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-white-flat-mountain-2x.webp` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-white-flat-mountain-4x.avif` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-white-flat-mountain-4x.webp` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-white-flat-mountain.avif` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/shop-white-flat-mountain.webp` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-mountain-2x.avif` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-mountain-2x.webp` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-mountain.avif` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-mountain.webp` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-paw-2x.avif` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-paw-2x.webp` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-paw.avif` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-black-paw.webp` | Our mockup of a demo design on `black.jpg` (Hanna Pad, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-mint-leaf-2x.avif` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-mint-leaf-2x.webp` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-mint-leaf.avif` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-mint-leaf.webp` | Our mockup of a demo design on `mint.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-navy-books-2x.avif` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-navy-books-2x.webp` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-navy-books.avif` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-navy-books.webp` | Our mockup of a demo design on `navy.jpg` (dayong tien, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-orange-cup-2x.avif` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-orange-cup-2x.webp` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-orange-cup.avif` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-orange-cup.webp` | Our mockup of a demo design on `orange.jpg` (Vie Studio, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-pink-heart-2x.avif` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-pink-heart-2x.webp` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-pink-heart.avif` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-pink-heart.webp` | Our mockup of a demo design on `pink.jpg` (MART PRODUCTION, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-flat-mountain-2x.avif` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-flat-mountain-2x.webp` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-flat-mountain.avif` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-flat-mountain.webp` | Our mockup of a demo design on `white-flat.jpg` (Mockupbee, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-rack-cup-2x.avif` | Our mockup of a demo design on `white-rack.jpg` (Marina Podrez, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-rack-cup-2x.webp` | Our mockup of a demo design on `white-rack.jpg` (Marina Podrez, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-rack-cup.avif` | Our mockup of a demo design on `white-rack.jpg` (Marina Podrez, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `hero/tile-white-rack-cup.webp` | Our mockup of a demo design on `white-rack.jpg` (Marina Podrez, Pexels) | Pexels License (free to use, modified and commercially; no attribution required), modified; the design is ours |
| `screens/feature-mockups-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/feature-mockups.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/feature-profiles-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/feature-profiles.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/feature-report-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/feature-report.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/feature-schedule-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/feature-schedule.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-review-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-review.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-send-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-send.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-upload-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-upload.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-write-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/step-write.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/why-shops-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/why-shops.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/why-writer-phone.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `screens/why-writer.webp` | A crop of our own app with the synthetic demo account (`scripts/marketing-shots.mjs`) | Ours |
| `icon.svg` | The app icon (same as `brand/icon.svg`) (`app/icon.svg`, `scripts/logo-icons.mjs`) | Ours |
| `favicon.ico` | 16, 32 and 48 px of the app icon (`app/favicon.ico`, `scripts/logo-icons.mjs`) | Ours |
| `apple-icon.png` | 180 px app icon, full bleed (`app/apple-icon.png`, `scripts/logo-icons.mjs`) | Ours |

The share cards (`/og/*.png`) are drawn at build time by `app/og/[slug]/route.tsx` from our logo and the page's
heading, set in a static subset of Archivo (`app/og/archivo-900.ttf`, `app/og/archivo-500.ttf`; SIL Open Font
License 1.1).
