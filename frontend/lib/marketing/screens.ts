/**
 * The product pictures on the public site: crops of the real app, signed in to the synthetic demo account
 * (docs/marketing-screens.md; scripts/marketing-shots.mjs retakes them): sample shops, sample designs, no real
 * seller. Each is cropped to the part that matters and taken at twice its size; the page shows it at its
 * natural size (width x height, in CSS pixels) and never wider, so its text reads as it does in the app. On a
 * phone it shows the same part taken from the app's phone layout, so the text stays readable there too.
 * The paths are written out because scripts/public-assets.cjs reads them from this source.
 */

/** A crop in CSS pixels; its file is twice that. */
export type Crop = { src: string; width: number; height: number };
/** The wide layout's crop, and the phone layout's (shown below 640 px wide). */
export type Screen = Crop & { alt: string; phone: Crop };

export const SCREENS = {
  upload: {
    src: "/screens/step-upload.webp",
    width: 520,
    height: 300,
    alt: "A listing group from an upload: its SKU, its profile, its photos with the cover marked, and its size chart",
    phone: { src: "/screens/step-upload-phone.webp", width: 359, height: 390 },
  },
  write: {
    src: "/screens/step-write.webp",
    width: 520,
    height: 330,
    alt: "A written listing: its title with the length counter, and its 13 tags",
    phone: { src: "/screens/step-write-phone.webp", width: 359, height: 429 },
  },
  review: {
    src: "/screens/step-review.webp",
    width: 520,
    height: 300,
    alt: "The search-readiness checklist under a listing, its Approved tick, and its draft scheduled in a shop",
    phone: { src: "/screens/step-review-phone.webp", width: 359, height: 390 },
  },
  send: {
    src: "/screens/step-send.webp",
    width: 520,
    height: 300,
    alt: "Where and when: the shop a batch goes to, when, and how many drafts that makes",
    phone: { src: "/screens/step-send-phone.webp", width: 359, height: 390 },
  },
  writer: {
    src: "/screens/why-writer.webp",
    width: 520,
    height: 150,
    alt: "The start of a listing's description, which names what the design shows",
    phone: { src: "/screens/why-writer-phone.webp", width: 359, height: 195 },
  },
  shops: {
    src: "/screens/why-shops.webp",
    width: 520,
    height: 420,
    alt: "Working on one shop: a listing's profile and SKU in that shop, and its own photos and attributes there",
    phone: { src: "/screens/why-shops-phone.webp", width: 359, height: 546 },
  },
  mockups: {
    src: "/screens/feature-mockups.webp",
    width: 520,
    height: 420,
    alt: "A mockup template: blank T-shirts, each confirmed, with its print area outlined",
    phone: { src: "/screens/feature-mockups-phone.webp", width: 359, height: 546 },
  },
  schedule: {
    src: "/screens/feature-schedule.webp",
    width: 520,
    height: 420,
    alt: "The Scheduled page: shop groups with the next listing to go live, Pause and Settings",
    phone: { src: "/screens/feature-schedule-phone.webp", width: 359, height: 546 },
  },
  profiles: {
    src: "/screens/feature-profiles.webp",
    width: 520,
    height: 420,
    alt: "A profile card: its template, title prefix and listing style",
    phone: { src: "/screens/feature-profiles-phone.webp", width: 359, height: 546 },
  },
  report: {
    src: "/screens/feature-report.webp",
    width: 520,
    height: 420,
    alt: "What needs attention in the profit report: listings whose sales fell by half or more",
    phone: { src: "/screens/feature-report-phone.webp", width: 359, height: 546 },
  },
} as const satisfies Record<string, Screen>;
