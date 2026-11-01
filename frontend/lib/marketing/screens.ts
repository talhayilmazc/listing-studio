/**
 * The product screenshots on the public site. Crops of the real app, signed in to the synthetic
 * demo account (docs/marketing-screens.md; scripts/marketing-shots.mjs retakes them): sample shops,
 * sample designs, no real seller. Each comes in two widths so a phone never downloads the large one.
 * Both paths are written out because scripts/public-assets.cjs reads them from this source.
 */

export type Screen = { src: string; small: string; alt: string };

export const SCREENS = {
  upload: {
    src: "/screens/site-upload.webp",
    small: "/screens/site-upload-560.webp",
    alt: "The upload page's listing groups: each design's photos in one card, with its SKU and cover photo marked",
  },
  write: {
    src: "/screens/site-write.webp",
    small: "/screens/site-write-560.webp",
    alt: "A review card with a written title, its length, and thirteen tags with their lengths",
  },
  review: {
    src: "/screens/site-review.webp",
    small: "/screens/site-review-560.webp",
    alt: "The bottom of a review card: the search-readiness checklist, the Approved tick, the draft with Publish now, and its scheduled time",
  },
  schedule: {
    src: "/screens/site-schedule.webp",
    small: "/screens/site-schedule-560.webp",
    alt: "The Scheduled page: shop groups with Pause and Settings, filters, and a listing with its shop and publish time",
  },
  mockups: {
    src: "/screens/site-mockups.webp",
    small: "/screens/site-mockups-560.webp",
    alt: "A mockup template: blank T-shirts in several colours, each marked Confirmed, with its print area outlined",
  },
  shops: {
    src: "/screens/site-shops.webp",
    small: "/screens/site-shops-560.webp",
    alt: "A batch's shop workspace: working on one shop, with a row per listing showing its profile, SKU and text in that shop",
  },
  profiles: {
    src: "/screens/site-profiles.webp",
    small: "/screens/site-profiles-560.webp",
    alt: "Two profile cards: each with its template, title prefix, listing style and title length limits",
  },
  report: {
    src: "/screens/site-report.webp",
    small: "/screens/site-report-560.webp",
    alt: "The Analytics page for one month, with sales and fees and where each figure comes from",
  },
} as const satisfies Record<string, Screen>;
