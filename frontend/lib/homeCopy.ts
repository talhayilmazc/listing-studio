/**
 * Every word of the public home page, in one place.
 *
 * The page's components only lay this out, so the rules the home page must keep
 * are checked on the text itself (lib/homeCopy.test.ts, lib/siteDesign.test.ts):
 *   - each claim is something the product does today; no invented numbers,
 *     quotes, logos, ratings or user counts, and no other product is named;
 *   - "Etsy" is not in a title, share title, JSON-LD name or headline, or the h1 (lib/marketing/brandRule.ts).
 *     Elsewhere it may describe who the product is for, and nothing says or implies that
 *     Etsy runs, stands behind or works with Listyro. Listyro is the only product name;
 *   - no promise of volume or of one listing copied across shops, and no promise of ranking;
 *   - prices are not typed here: the teaser reads lib/plans.ts; the buttons read lib/marketing/cta.ts;
 *   - plain seller words: sentence case, no em dashes, no filler words (docs/design-v2.md).
 * The hero's demonstration is the synthetic demo account's own sample data
 * (backend/scripts/demo_screens_seed.py: NW1042, its title, its tags, its shops), and its
 * pictures are made by the mockup engine (backend/scripts/hero_assets.py).
 */
import { PRIMARY_CTA } from "./marketing/cta.ts";

export const HOME = {
  title: "Listyro: AI listing writer and mockups for print-on-demand sellers",
  description:
    "A listing assistant for print-on-demand sellers: upload your designs and get finished Etsy listings, with mockups, for every shop you run. You approve every one.",

  hero: {
    heading: "Upload your designs. Get finished listings, with mockups, for every shop you run.",
    sub: "Stop writing titles and tags by hand, making mockups one at a time and copying listings shop by shop. Listyro does that work, and you approve every listing before it goes live.",
    primary: PRIMARY_CTA,
    secondary: { label: "See how it works", href: "#how-it-works" },
    note: "Invite-only early access. No payment is taken today.",
    demo: {
      label: "From one design file to scheduled listings, with sample data from our demo account",
      caption: "Sample data from our demo account. The mockups are made by Listyro.",
      file: "NW1042.png",
      fileAlt: "The design file: mountains under a sunset",
      mockups: "5 mockups",
      colours: ["White tee", "Sand tee", "Sage tee", "Pink tee", "Mustard tee"],
      listing: "Listing",
      title: "Retro Camping Shirt, Mountain Sunset, Vintage Outdoor Style",
      tags: ["hiking gift", "camp crew tee", "national park trip", "outdoor dad gift", "road trip tee", "nature lover gift", "summer camp shirt", "mountain lover", "adventure tee", "family camping", "trail runner gift", "70s retro tee", "graphic tshirt"],
      tagCount: "13 tags",
      shops: [
        { name: "Northwind Tees", time: "Tue 9:00 AM" },
        { name: "Juniper Print Co.", time: "Tue 9:05 AM" },
        { name: "Harbor Thread", time: "Tue 9:10 AM" },
      ],
      scheduled: "Scheduled",
    },
  },

  how: {
    id: "how-it-works",
    heading: "How it works",
    before: "First, connect your shop and pick a listing you already sell. New drafts copy its price, variations, shipping and size charts.",
    steps: [
      { title: "Upload your designs", body: "Drop in design files or a folder. Photos are grouped into listings by the SKU in the file name." },
      { title: "Listyro writes each listing", body: "It looks at each design and writes a title, 13 tags and a description in your profile's style. Or write them yourself." },
      { title: "Review and approve", body: "Read every listing and change anything. You are warned before sending if the text names a well-known brand." },
      { title: "Pick shops and times", body: "Choose the shops and when each listing goes live. Nothing goes live that you did not approve." },
    ],
  },

  why: {
    id: "why",
    heading: "Why sellers switch",
    items: [
      {
        title: "Mockups on your own garment photos",
        body: "Upload your blank photos once. Every design is printed on every colour, following the folds and shading. Your design is placed, never redrawn.",
        before: "Your blank photo",
        after: "With the design",
        link: { label: "How mockups work", href: "/features/mockup-generator" },
      },
      {
        title: "AI that reads each design",
        body: "The writer looks at the design itself, not just the file name, and follows the style of a listing you already sell. You can change every word.",
        design: "The design",
        text: "The start of its description",
        link: { label: "How the writer works", href: "/features/ai-listing-writer" },
      },
      {
        title: "Each shop gets its own listing",
        body: "Run more than one shop? Each one gets its own photos, SKU, text and attributes, and you see what goes where before anything is sent.",
        link: { label: "How shops work", href: "/features/multi-shop-publishing" },
      },
    ],
  },

  pricing: {
    id: "pricing",
    heading: "Pay for the listings Listyro writes",
    link: { label: "See every plan", href: "/pricing" },
  },

  faq: {
    id: "faq",
    heading: "Questions sellers ask",
    contactLead: "Something else?",
    contact: { label: "Search the help center", href: "/help" },
    items: [
      { q: "What counts as a listing?", a: "In the launch plans, a listing is one design written by AI, with all its photos. Rewriting the same design is free up to 3 times. Listings you write yourself, and sending a listing to more shops, do not count." },
      { q: "Does anything go live without me?", a: "No. Every listing starts as a draft. It goes live only when you press Publish, or at the time you set for it." },
      { q: "Can I use several shops?", a: "Yes. Connect each shop with its own Etsy sign-in and work on all of them under one login. Each keeps its own profile, SKU, photos and text." },
      { q: "Can I cancel?", a: "Yes. Nothing is charged during early access. You can disconnect a shop or delete your account in Settings at any time, and a disconnected shop's Etsy data is deleted at once." },
    ],
  },

  closing: {
    heading: "Spend your time on designs, not listing forms.",
    body: "Listyro is invite-only while we onboard sellers in small groups. Tell us about your shop and we'll email you when a spot opens.",
    primary: PRIMARY_CTA,
    secondary: { label: "See pricing", href: "/pricing" },
  },
} as const;

/** Every string on the page, for the checks. */
export function allCopy(node: unknown = HOME): string[] {
  if (typeof node === "string") return [node];
  if (Array.isArray(node)) return node.flatMap((n) => allCopy(n));
  if (node && typeof node === "object") return Object.values(node).flatMap((n) => allCopy(n));
  return [];
}

/** The headings a visitor reads first. */
export function headings(): string[] {
  const h = HOME;
  return [h.title, h.hero.heading, h.how.heading, h.why.heading, h.pricing.heading, h.faq.heading, h.closing.heading];
}
