/**
 * Every word of the public home page, in one place.
 *
 * The page's components only lay this out, so the rules the home page must keep
 * are checked on the text itself (lib/homeCopy.test.ts):
 *   - each claim is something the product does today; no invented numbers,
 *     quotes, logos, ratings or user counts, and no other product is named;
 *   - "Etsy" may describe who the product is for, and nothing says or implies that
 *     Etsy runs, stands behind or works with Listyro. Listyro is the only product name;
 *   - no promise of volume or of one listing copied across shops, and no promise of ranking;
 *   - prices are not typed here: the teaser reads lib/plans.ts.
 * A figure quoted here (how long uploads are kept) is compared with
 * lib/legal.ts by the same test.
 */

export const HOME = {
  title: "Listyro: AI listing assistant for Etsy sellers",
  description:
    "From design files to scheduled Etsy listings. A listing assistant for print-on-demand sellers: Listyro makes the mockups, writes each listing from its design, and you approve every one.",

  hero: {
    eyebrow: "AI listing assistant for Etsy sellers",
    heading: "From design files to scheduled Etsy listings.",
    headingAccent: "You approve every one.",
    primary: { label: "Request an invite", href: "/request-invite" },
    secondary: { label: "See how it works", href: "#how-it-works" },
    note: "Invite-only early access. No payment is taken today.",
  },

  video: {
    label: "A 60-second demo video is coming soon. Until then, this is the product itself, with sample data.",
    title: "A 60-second demo of Listyro",
  },

  how: {
    id: "how-it-works",
    eyebrow: "How it works",
    heading: "Four steps, and you stay in charge",
    before: "First, connect your shop on Etsy's own sign-in page and pick a listing you already sell. New drafts copy its price, variations, shipping and size charts.",
    steps: [
      { title: "Upload your design files", body: "Drop in designs or a folder. Mockups are made on your own garment photos, and photos are grouped into listings by SKU." },
      { title: "AI reads each design and writes", body: "A title, 13 tags and a description for each one, in the style you chose. Or write them yourself." },
      { title: "Review and approve", body: "Read each listing and change anything. You are told before sending if it names a well-known brand." },
      { title: "Schedule it", body: "Choose your shops and the time each listing goes live. Nothing is published that you did not approve." },
    ],
  },

  apart: {
    id: "features",
    eyebrow: "What sets it apart",
    heading: "Three things that save the most time",
    items: [
      { title: "Automatic mockups", body: "Every design on every blank photo you choose, following the fabric's folds and shading. Your design is placed, never redrawn.", link: { label: "The mockup generator", href: "/features/mockup-generator" } },
      { title: "AI that reads the design", body: "The writer looks at the design itself, not just a file name, then follows your profile's style. You edit everything.", link: { label: "The AI listing writer", href: "/features/ai-listing-writer" } },
      { title: "Each shop's own SKU and text", body: "Running several shops? Each keeps its own profile, SKU, title, tags and description, and you see what goes where.", link: { label: "Multi-shop publishing", href: "/features/multi-shop-publishing" } },
    ],
    more: { label: "See all features", href: "/features" },
  },

  who: {
    id: "who-it-is-for",
    eyebrow: "Who it is for",
    heading: "Made for sellers who add designs often",
    items: [
      { title: "Print-on-demand sellers", body: "You have designs ready and a production partner, and the listing form is what slows you down." },
      { title: "Apparel shops with a set way of listing", body: "You want each new listing to match the ones you already sell." },
      { title: "Sellers with more than one shop", body: "You run several shops, each with its own range, and want them under one login." },
    ],
    notFor: "Not a fit if you sell mainly outside Etsy, or want research into other sellers' shops: Listyro works only with your own.",
    compare: { label: "Compare the alternatives", href: "/compare/alternatives" },
  },

  pricing: {
    id: "pricing",
    eyebrow: "Pricing",
    heading: "Plans after launch",
    link: { label: "See every plan", href: "/pricing" },
  },

  faq: {
    id: "faq",
    eyebrow: "Questions",
    heading: "Common questions",
    contactLead: "Something else?",
    contact: { label: "See the help center", href: "/help" },
    items: [
      { q: "Will anything go live without me?", a: "No. Every listing is created as a draft. A draft is published only when you press Publish, or at the time you set for it." },
      { q: "Is Listyro made by Etsy?", a: "No. Listyro is an independent product that works through Etsy's API. It is not made, endorsed or sponsored by Etsy." },
      { q: "What does it cost?", a: "Nothing during early access: no payment of any kind is taken, and there is no checkout on this site. The plans we intend to offer after launch may change before launch." },
      { q: "What do I need to start?", a: "An Etsy shop you own, with at least one listing set up the way you like it. That listing becomes the model for new ones." },
      { q: "What happens to my designs?", a: "Uploads are working copies. Their files are deleted 3 days after a listing has a draft in every shop it is meant for, and after 30 days otherwise. To write a listing's text, the design image is sent to Anthropic, with the title and tags of one of your own listings if you chose it as an example. Under its commercial terms they are not used to train its models." },
      { q: "Do you keep anything about my buyers?", a: "No. Buyer names, addresses, emails and messages are never stored. For your own profit report we keep order numbers, listing ids, amounts and dates." },
      { q: "Will my listings rank higher?", a: "Nobody outside Etsy can promise that, and we do not. The text follows Etsy's own published guidance." },
      { q: "How do I stop using it?", a: "Disconnect a shop and its tokens and the Etsy data we hold for it are deleted at once. Delete your account in Settings and everything in it is removed, within 30 days at the latest." },
    ],
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

/** The headings a visitor reads first. */
export function headings(): string[] {
  const h = HOME;
  return [h.title, h.hero.heading, h.hero.headingAccent, h.how.heading, h.apart.heading, h.who.heading, h.pricing.heading, h.faq.heading, h.closing.heading, h.closing.headingAccent];
}
