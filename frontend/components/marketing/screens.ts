/** Product screenshots: 2x captures of a demo account holding sample data. */
export const SCREENS = {
  review: { src: "/screens/review-2.webp", width: 2880, height: 1800, alt: "Listyro's review page: a listing's images, title, 13 tags and description beside its approval and draft controls" },
  // The same capture cut to the listing card: on a phone the whole page would be too small to read.
  reviewCard: { src: "/screens/review-card-2.webp", width: 1698, height: 1632, alt: "A listing's card on the review page: its image, title, 13 tags, attributes and approval" },
  batch: { src: "/screens/batch-content.webp", width: 2400, height: 1800, alt: "A batch in Listyro: groups of mockups, each with the shop and profile it will be listed with" },
  analytics: { src: "/screens/analytics-content.webp", width: 2400, height: 1800, alt: "Listyro analytics: revenue, Etsy fees, ad spend and profit per listing, each with its source" },
  phone: { src: "/screens/phone-2.webp", width: 1170, height: 2532, alt: "The review page on a phone" },
} as const;
