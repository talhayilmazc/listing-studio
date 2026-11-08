# Site v2: the print shop (design plan, 7 November 2026)

The public site has to sell before launch. Today it looks like a template: cream background, a serif face,
a purple accent, tracked all-caps labels over every section, identical rounded cards, shrunken screenshots
and a defensive headline. v2 keeps every route, title, description, JSON-LD block, the sitemap and the cache
rules. It replaces the look and the home page.

The subject is a print-on-demand apparel seller's workbench: garment flats, ink, colour swatches, hang
tags and SKU labels. Shapes come from that world. A hang tag is the mark, a SKU label is the small
monospace chip, and a colour swatch row is the mockup strip. Panels are cut paper. Lines are the heather
grey of a tee.

## Tokens (one place: `frontend/app/globals.css` + `tailwind.config.ts`; the app reads the same)

| Token | Value | Role | Contrast |
|---|---|---|---|
| paper | `#FFFFFF` | page | |
| ink | `#14213D` | text, primary buttons, the mark's outline | 16.0:1 on paper, 14.5:1 on fabric, 10.8:1 on tag |
| heather | `#D9DCE1` | lines, borders | decorative |
| fabric | `#F3F4F6` | panels that group something | |
| tag | `#FFCF33` | the single accent: highlights, the mark's fill, the selected state of a switch, the highlighted plan's tag | ink on tag 10.8:1; never text on paper (1.5:1) |
| go | `#1F8A4C` | status marks only (dots, ticks, fills) | 3.7:1 as a graphic; small status text uses `go-ink` `#1A7340` (5.9:1) |
| muted text | `#545B67` | secondary text | 6.8:1 on paper, 6.2:1 on fabric |

No purple, cream, gradients or Etsy orange. The app's existing colour names keep working: `brand-*` now
points at the ink ramp, `slate-*` at cool neutrals ending in ink, and the rail is ink. No layout changes.

Focus: a 2 px ink outline with a 2 px paper gap, on everything that takes focus. Yellow is never the only
signal (it fails 3:1 against white).

## Type

Archivo, one variable file (weight 100 to 900, width 62 to 125), self-hosted by `next/font` with the
`wdth` axis. Headlines use the expanded width (`font-stretch: 125%`) at 800. Body text uses normal width
at 400, and UI text at 500 or 600. Sentence case everywhere. No tracked capitals and no italics for
emphasis. The body measure stays under 75 characters (`max-w-[65ch]`).

| Step | Size / line (desktop) | Phone | Use |
|---|---|---|---|
| display | 56 / 58, wdth 125, 800 | 34 / 38 | the one h1 on the home page |
| h1 | 44 / 48, wdth 125, 800 | 32 / 36 | page titles |
| h2 | 32 / 36, wdth 125, 800 | 26 / 30 | sections |
| h3 | 20 / 26, wdth 100, 700 | 19 / 25 | items |
| lead | 20 / 30, 400 | 18 / 28 | the line under a title |
| body | 17 / 28, 400 | 17 / 28 | prose |
| small | 15 / 24 | 15 / 24 | UI, captions |
| label | 13 / 18, 600 | 13 / 18 | SKU chips, table heads (sentence case) |

## Logo

A hang tag: a tag with chamfered top corners and a punched hole, in an ink outline with a tag-yellow
fill. The hole is cut out, so it shows whatever sits behind it. On dark, the yellow body carries the
shape. The wordmark is "Listyro" in Archivo expanded at 800. The mark is drawn on a 32 grid with a
2.5 px stroke so it holds at 16 px. Files: `components/marketing/Logo.tsx` (inline SVG),
`app/icon.svg`, `app/favicon.ico` (16 and 32).

## Shapes by role (not one card everywhere)

- **Panel** (`fabric`, no border, square corners or 4 px): groups a block, such as the pricing teaser
  or the FAQ.
- **Tag** (paper, heather border, chamfered top-left corner via `clip-path`): a single plan, one per
  plan; the highlighted plan's tag has a yellow band.
- **Label chip** (ink text on fabric, monospace numbers): SKUs, counts, times.
- **Detail crop** (paper, 1 px heather frame, no shadow, "Sample data" chip): a real UI crop shown at
  its natural CSS size (taken at 2x).
- **Rule rows** (heather top lines): steps, FAQ, help answers, compare rows.

Motion: only in the hero demonstration (opacity and transform), once on load. Nothing else fades in.

## Home (top to bottom)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [tag] Listyro     Features Pricing Free tools Compare Guides   Sign in [Request an invite] │
├──────────────────────────────────────────────────────────────────────────────┤
│ Upload your designs.                 │  ┌ design.png ┐                       │
│ Get finished listings, with          │  └────────────┘  ▸ ■ ■ ■ ■ ■ mockups  │
│ mockups, for every shop you run.     │  ┌ listing card: title + 13 tags ───┐ │
│                                      │  └──────────────────────────────────┘ │
│ Stop writing titles and tags, making │  ● Northwind Tees     Tue 9:00 AM     │
│ mockups and copying listings shop by │  ● Juniper Print Co.  Tue 9:05 AM     │
│ shop.                                │  ● Harbor Thread      Tue 9:10 AM     │
│ [Request an invite]  See how it works│  Sample data from our demo account     │
├──────────────────────────────────────────────────────────────────────────────┤
│ How it works                                                                 │
│ 1 Upload   [crop]   2 Write   [crop]   3 Review   [crop]   4 Send   [crop]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Why sellers switch                                                           │
│ A. Mockups on your own garment photos   blank photo + design → mockup row    │
│ B. AI that reads each design            design │ its title and tags          │
│ C. One write, many shops                shop rows: own photos, SKU, text     │
├──────────────────────────────────────────────────────────────────────────────┤
│ (Numbers: only if measured honestly; left out in v2, see below)              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Pricing panel: Starter · Growth (yellow band) · Pro · Business  → /pricing   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Four questions                                                               │
├──────────────────────────────────────────────────────────────────────────────┤
│ Closing: one line, one button                                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ Footer: columns · mark · trademark notice (exact text)                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

On a phone the hero stacks: text, then the demonstration at full width.

**The hero demonstration** is HTML and CSS around real output from the synthetic demo account. The
design is the demo's NW1042 mountain drawing. The mockups are made by our own mockup engine
(`pipeline/mockups.py`) on the demo's "Ten colours" blanks (`backend/scripts/hero_assets.py`). The title,
the 13 tags and the shop names are the demo's. It plays once (about 4 seconds) and then rests on the final
state. Every element holds its final place from the first frame, so nothing shifts. With
`prefers-reduced-motion` it shows the final state. Budget: under 120 KB of images.

**The numbers section is left out.** Upload to drafts for 50 designs cannot be measured honestly on a
development machine: Etsy calls are blocked there and the AI writer would be a stub. If it is measured
later on production with the test shop, the result and the method go in `lib/homeCopy.ts`.

## Other pages

- **Features**: each page opens with one cropped real UI detail at its natural size.
- **Guides**: an article layout with a 65ch measure, real h2s and a sticky "on this page" list on wide
  screens.
- **Compare**: clean tables (paper, heather rules, ink head row), and the "what they do that we do not"
  list kept.
- **Help**: a list you can search (filtering happens in the browser, and every answer is in the HTML).
- **Pricing**: the plan tags, the monthly/yearly switch (yellow selected state) and the extra pack.
- **Legal and 404**: the same type and rules.

## Pricing (display only; `frontend/lib/plans.ts`)

| Plan | Monthly | AI listings a month | Shops |
|---|---|---|---|
| Starter | $19 | 150 | 1 |
| Growth (highlighted) | $29 | 300 | 3 |
| Pro | $49 | 600 | 5 |
| Business | $69 | 1,000 | 10 |
| Special | custom | more than 1,000 | "Contact us" |

- Yearly costs ten times the monthly price.
- The extra pack adds 100 AI listings for $9.
- Only AI-written listings count. Hand-written listings and sending a listing to more shops do not.
- The trial is 25 AI listings when you connect a shop.
- While billing is off, the page says these are launch prices and nothing is charged yet. The primary
  button comes from config (`lib/marketing/cta.ts`): "Request an invite" now, "Start free" when billing
  is on.

## Copy rules

Plain seller language, active verbs, sentence case and short sentences. No em dashes, emoji or filler.
These words are banned: seamless, effortless, unlock, supercharge, elevate. Buttons say what happens. A
claim must be true of the product today. `lib/homeCopy.test.ts` and `lib/siteDesign.test.ts` check these
rules.
