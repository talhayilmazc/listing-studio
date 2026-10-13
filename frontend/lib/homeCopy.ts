/**
 * Every word of the public home page, in one place.
 *
 * The page's components only lay this out, so the rules the home page must keep
 * are checked on the text itself (lib/homeCopy.test.ts):
 *   - each claim is something the product does today; no invented numbers,
 *     quotes, logos, ratings or user counts, and no other product is named;
 *   - "Etsy" is never in the page title, the hero heading or a section heading,
 *     and nothing says or implies that Etsy stands behind Listyro;
 *   - no promise of volume or of one listing copied across shops, and no
 *     promise of ranking.
 * A figure quoted here (how long uploads are kept) is compared with
 * lib/legal.ts by the same test.
 */

export type Visual =
  | { kind: "screen"; src: string; alt: string; panel?: boolean }
  | { kind: "template" }
  | { kind: "trademark" };

export const HOME = {
  title: "Listyro — your designs, listed properly, on your schedule",
  description:
    "For print-on-demand and apparel sellers. Listyro prepares complete draft listings from your own designs, modelled on a listing you already sell. You review, approve and schedule every one.",
  ogAlt: "Listyro: your own designs, listed properly, on your schedule. You approve every listing.",

  hero: {
    eyebrow: "For print-on-demand and apparel sellers",
    heading: "Your own designs, listed properly, on your schedule.",
    headingAccent: "You approve every listing.",
    body:
      "Upload your mockups and pick one of your existing listings as the template. Listyro prepares each new listing as a draft: the template's prices, variations, shipping and size charts, with a suggested title, 13 tags and description. Nothing goes live until you approve it or schedule it.",
    primary: { label: "Request an invite", href: "/request-invite" },
    secondary: { label: "See how it works", href: "#how-it-works" },
    note: "Invite-only early access. Free while we onboard sellers in small groups.",
  },

  /** The hero's small walkthrough (components/marketing/HeroDemo.tsx). Sample designs drawn by us. */
  demo: {
    label: "Sample data",
    folder: "summer-drop",
    captions: [
      "summer-drop/ · 6 designs, 15 mockups",
      "Grouping the mockups by SKU",
      "Looking at each design",
      "Suggesting titles, tags and descriptions",
      "Prices, variations and size charts from your template",
      "You review and approve each one",
      "Live when you publish, or at the time you set",
    ],
    pills: { draft: "Draft · review", reviewed: "Reviewed", scheduled: "Fri 5:00 PM" },
    more: "+11",
    replay: "Replay",
  },

  apart: {
    id: "features",
    eyebrow: "Features",
    heading: "What sets Listyro apart",
    intro:
      "Made for sellers who add new designs often and want every new listing to match the ones already in their shop. Screens marked Sample data are the real product, from a demo account with sample designs.",
    cards: [
      {
        title: "Your best listing as the template",
        body: "Pick a listing you already sell. Every new draft copies its price, variations, shipping and return profiles, attributes and production partners.",
        visual: { kind: "template" },
      },
      {
        title: "Publish on your schedule",
        body: "Approve now, publish later. Choose the time for each draft, in your own time zone, shop by shop.",
        visual: { kind: "screen", src: "/screens/feature-schedule.webp", alt: "The Scheduled page: a two-week calendar and one listing set to go live at 12:45 PM" },
      },
      {
        title: "You approve everything",
        body: "Nothing goes live without your approval or the schedule you set. Write titles and tags yourself, or let AI suggest them for you to edit.",
        visual: { kind: "screen", panel: true, src: "/screens/feature-approve.webp", alt: "A listing's approval tick, its draft with the Publish now button, and the time it is scheduled for" },
      },
      {
        title: "Search-ready text",
        body: "A title, all 13 tags and a description written to Etsy's own published search guidance: a short, clear title, every tag used, keywords early in the description. No promise of ranking.",
        visual: { kind: "screen", src: "/screens/feature-text-2.webp", alt: "A listing's title with its length, its 13 tags with their lengths, and an attribute chosen from Etsy's list" },
      },
      {
        title: "Smart uploads",
        body: "Photos are grouped into listings by the SKU in their file names, and you can drag them between groups. Size charts go in the exact position you choose, and Etsy's 20-image limit is handled before anything is sent.",
        visual: { kind: "screen", src: "/screens/feature-uploads.webp", alt: "Photos arranged into listing groups by SKU, each group with its cover photo marked" },
      },
      {
        title: "Trademark check before anything is drafted",
        body: "Titles, tags and descriptions are checked for brand and character names, and artwork that may show a recognisable character is flagged. On by default. It is a check, not legal advice.",
        visual: { kind: "trademark" },
      },
      {
        title: "Several shops from one login",
        body: "Run more than one shop? Connect each one and manage them from one login. Every shop keeps its own listings, groups and settings.",
        visual: { kind: "screen", panel: true, src: "/screens/feature-shops.webp", alt: "The review page's shop list: two shops, and for each listing whether it has a draft there" },
      },
      {
        title: "Profit, cost and ads reporting",
        body: "For your own shop: what a month earned after Etsy's fees, your ad spend and your product costs. Every figure says whether it is exact, calculated or estimated.",
        visual: { kind: "screen", panel: true, src: "/screens/feature-report.webp", alt: "A month's fees line by line, each figure marked exact, with last month and a year ago beside it" },
      },
    ] as { title: string; body: string; visual: Visual }[],
    screenLabel: "Sample data",
    drawingLabel: "Illustration",
  },

  /** The two drawings among the feature cards (not screenshots, and labelled so). */
  templateDrawing: {
    from: "Your listing",
    fromNote: "the template",
    to: "Every new draft",
    copied: ["Price and variations", "Shipping profile", "Return policy", "Attributes", "Production partners", "Size charts"],
    written: "Title, tags and description: suggested from the design, edited by you",
  },
  trademarkDrawing: {
    field: "Title",
    before: "Retro Camping Shirt,",
    flagged: "brand name",
    after: "Mountain Tee",
    finding: "Remove: brand name (title), character name tee (tag)",
    artwork: "The artwork may show a recognisable character.",
    state: "Not drafted until you change it",
  },

  built: {
    id: "built-with-sellers",
    eyebrow: "Built with sellers",
    heading: "Shaped by the sellers who use it",
    intro: "Much of Listyro comes from what early-access sellers ask for. Four of their requests, as they were added:",
    items: [
      { when: "October 2026", title: "Photos grouped by the SKU in their file names", body: "Upload a flat set of photos and each design's shots find each other." },
      { when: "October 2026", title: "Size chart positions", body: "Put each size chart exactly where you want it among a listing's photos." },
      { when: "October 2026", title: "Occasion, Holiday and Section on the review card", body: "Set them listing by listing, from Etsy's own lists for your category and your shop's own sections." },
      { when: "October 2026", title: "Write-it-yourself mode", body: "Write the title and tags yourself. Everything else still comes from your template, and no AI is used." },
    ],
    outro: "Have something you need? Tell us when you request an invite.",
  },

  how: {
    id: "how-it-works",
    eyebrow: "How it works",
    heading: "How it works, in four steps",
    steps: [
      { title: "Connect your shop", body: "Sign in on Etsy's own page and choose what Listyro may do. We never see your password, and you can disconnect at any time." },
      { title: "Pick a reference listing", body: "Choose a listing you already sell. It becomes the template for price, variations, shipping, returns, attributes and size charts." },
      { title: "Upload your designs", body: "Drop in your mockups. Photos are grouped into listings, resized and put in order, cover first." },
      { title: "Review, approve and schedule", body: "Read each listing, change anything, approve it. It is created in your shop as a draft and goes live when you publish it, or at the time you set." },
    ],
  },

  safe: {
    id: "safe",
    eyebrow: "Safe for your shop",
    heading: "Safe for your shop",
    intro: "An app that adds listings to your shop has to earn your trust. This is how Listyro works.",
    points: [
      { title: "Etsy's own API only", body: "Listyro works through Etsy's public API, with the permissions you grant on Etsy's sign-in page. No scraping and no browser bots." },
      { title: "You approve every listing", body: "Every listing is created as a draft. It goes live only when you publish it, or at a time you scheduled for it." },
      { title: "Your data is not sold or used to train AI", body: "We do not sell or share your data, and nothing goes to advertising platforms. A design image is sent to our AI provider, Anthropic, only to suggest your listing's text; under its commercial terms it is not used to train models. Nothing about your buyers is stored." },
      { title: "Disconnect any time", body: "Disconnecting a shop deletes its access tokens and the data read from it at once. You can delete your whole account in Settings." },
    ],
    links: [
      { label: "Privacy Policy", href: "/privacy" },
      { label: "Terms of Service", href: "/terms" },
    ],
    linksLead: "The detail is in the",
  },

  faq: {
    id: "faq",
    eyebrow: "Questions",
    heading: "Common questions",
    contactLead: "Something else?",
    contact: { label: "Get in touch", href: "/contact" },
    items: [
      { q: "Will anything go live without me?", a: "No. Every listing is created as a draft. A draft is published only when you press Publish, or at the time you set for it." },
      { q: "Is Listyro made by Etsy?", a: "No. Listyro is an independent product that works through Etsy's API. It is not made, endorsed or sponsored by Etsy." },
      { q: "What does it cost?", a: "Nothing during early access: no payment of any kind is taken, and there is no checkout on this site. Pricing applies only after launch and will be announced well before.", link: { label: "See pricing", href: "/pricing" } },
      { q: "What do I need to start?", a: "An Etsy shop you own, with at least one listing set up the way you like it. That listing becomes the template for new ones." },
      { q: "What happens to my designs?", a: "Uploads are working copies. Their files are deleted 3 days after a listing has a draft in every shop it is meant for, and after 30 days otherwise. To suggest a listing's text, the design image is sent to Anthropic; under its commercial terms it is not used to train its models." },
      { q: "Do you keep anything about my buyers?", a: "No. Buyer names, addresses, emails and messages are never stored. For your own profit report we keep order numbers, listing ids, amounts and dates." },
      { q: "Will my listings rank higher?", a: "Nobody outside Etsy can promise that, and we do not. The text is written to match what shoppers search for, following Etsy's own published guidance." },
      { q: "How do I stop using it?", a: "Disconnect a shop and its tokens and data are deleted at once. Delete your account in Settings and everything in it is removed, within 30 days at the latest." },
    ] as { q: string; a: string; link?: { label: string; href: string } }[],
  },

  closing: {
    heading: "Spend your time on designs,",
    headingAccent: "not listing forms.",
    body: "Listyro is invite-only while we onboard sellers in small groups. Tell us about your shop and we'll email you when a spot opens.",
    primary: { label: "Request an invite", href: "/request-invite" },
    secondary: { label: "Pricing", href: "/pricing" },
  },
} as const;

/** Every string on the page, for the checks. */
export function allCopy(node: unknown = HOME): string[] {
  if (typeof node === "string") return [node];
  if (Array.isArray(node)) return node.flatMap((n) => allCopy(n));
  if (node && typeof node === "object") return Object.values(node).flatMap((n) => allCopy(n));
  return [];
}

/** The headings a visitor reads first: Etsy's name is in none of them. */
export function headings(): string[] {
  const h = HOME;
  return [h.title, h.hero.heading, h.hero.headingAccent, h.apart.heading, h.built.heading, h.how.heading, h.safe.heading, h.faq.heading, h.closing.heading, h.closing.headingAccent];
}
