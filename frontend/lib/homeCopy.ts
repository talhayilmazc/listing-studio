/**
 * Every word of the public home page, in one place: the decided design (docs/design/home-reference.dc.html,
 * site v3), its markup's text and its renderVals() data. The components only lay this out.
 *
 * Where the page differs from the reference, and why (each is in the v3 report):
 *   - the trademark notice is ours, word for word (lib/legal.ts), not the reference's older wording;
 *   - "Watch it in 60 seconds" is shown only when a video address is configured (HERO_VIDEO): there is none;
 *   - the shirts are real photos with the demo account's designs, made by our mockup engine
 *     (lib/marketing/heroImages.ts), and the shop cards name the colours those photos have;
 *   - the AI card counts the title's characters instead of typing a number;
 *   - "Names Etsy often removes" became "Trademarked names": we cannot show that Etsy often removes them;
 *   - "Hand-written listings ... are always free" became the launch plans' wording: today's allowance still
 *     counts a hand-written listing (core/allowance.py), until billing changes it;
 *   - while billing is off, the pricing section also says these are launch prices and nothing is charged.
 * The rules the text keeps are checked on the text itself (lib/homeCopy.test.ts, lib/siteDesign.test.ts).
 */
import { PRIMARY_CTA, START_HREF } from "./marketing/cta.ts";
import { EXTRA_PACK, PLANNED_NOTICE, TRIAL, YEARLY_LABEL, aboutPerListing } from "./plans.ts";
import { SHOTS } from "./marketing/heroImages.ts";
import { PROMISE, TESTIMONIALS } from "./marketing/testimonials.ts";

/** The 60-second video, when there is one. Empty: the link is not shown. */
export const HERO_VIDEO = "";

const AI_TITLE = "Retro Sunset Mountain Shirt, Garment Dyed Hiking Tee, Camping Gift";

export const HOME = {
  title: "Listyro: AI listing writer and mockups for print-on-demand sellers",
  description:
    "A listing assistant for print-on-demand sellers: upload designs, get Etsy listings ready for every shop you run, with mockups on your own shirt photos and 13 tags. You approve each one.",

  hero: {
    badge: "Built on Etsy's official API",
    heading: "Upload 50 designs. Get 50 listings, ready for every shop.",
    sub: "Listyro's AI puts each design on your own shirt photos, writes the title and 13 tags, and schedules it to every shop you run. You approve. It publishes.",
    primary: { label: `Start free with ${TRIAL.listings} listings`, href: START_HREF },
    video: { label: "Watch it in 60 seconds", href: HERO_VIDEO },
    reassure: ["No card needed", "Cancel anytime", "Nothing goes live without you"],
    window: {
      name: "Fall drop",
      count: "48 designs",
      ready: "48 of 48 ready",
      tiles: [
        { shot: SHOTS.tile_white_flat_mountain, alt: "Mockup: the mountain and sunset design on a white shirt" },
        { shot: SHOTS.tile_pink_heart, alt: "Mockup: the heart design on a pink shirt" },
        { shot: SHOTS.tile_navy_books, alt: "Mockup: the stacked books design on a navy shirt" },
        { shot: SHOTS.tile_orange_cup, alt: "Mockup: the coffee cup design on an orange shirt" },
        { shot: SHOTS.tile_black_paw, alt: "Mockup: the paw print design on a black shirt" },
        { shot: SHOTS.tile_mint_leaf, alt: "Mockup: the leaf design on a folded mint shirt" },
        { shot: SHOTS.tile_white_rack_cup, alt: "Mockup: the coffee cup design on a white shirt on a rack" },
        { shot: SHOTS.tile_black_mountain, alt: "Mockup: the mountain and sunset design on a black shirt" },
      ],
    },
    shops: {
      heading: "Going live in 3 shops",
      rows: [
        { name: "Pine & Print", when: "Tue 9:00", dot: "#7393B3" },
        { name: "Ink Lane Co.", when: "Tue 11:30", dot: "#E8707E" },
        { name: "Tee Street", when: "Wed 9:00", dot: "#1F8A4C" },
      ],
    },
    ai: {
      read: "AI read the design: mountain, sunset, retro",
      title: AI_TITLE,
      tags: ["hiking shirt", "camping tee", "mountain shirt", "retro sunset", "outdoor gift", "nature lover", "adventure tee", "gift for him", "hiker gift", "travel shirt", "vintage tee", "national park", "summer camp"],
      tagCount: "13 of 13 tags",
      characters: `${AI_TITLE.length} of 140 characters`,
    },
  },

  strip: ["Uses Etsy’s official sign-in", "Drafts first, you approve", "Your real shirt photos", "Your designs never train AI"],

  grind: {
    heading: "The slow part of print-on-demand, done for you.",
    sub: "Designing is the fun part. Listing is the grind. Listyro takes the grind.",
    byHandLabel: "By hand, for every single design",
    byHand: ["Open the mockup file for every color", "Export and rename every photo", "Write a title under 140 characters", "Think up 13 tags, 20 characters each", "Fill in attributes, SKU and shipping", "Do it all again for your next shop"],
    withLabel: "With Listyro",
    withUs: ["Upload your designs once", "Check and approve", "Pick when each shop goes live"],
    note: "Mockups, title, 13 tags, attributes, SKU and the schedule are waiting for your check.",
  },

  how: {
    id: "how",
    heading: "From a folder of designs to every shop in four steps.",
    steps: [
      { n: "1", title: "Drop in your designs", text: "One file or a folder of fifty." },
      { n: "2", title: "Mockups, every color", text: "On your own shirt photos, folds and all." },
      { n: "3", title: "Title and 13 tags", text: "AI reads the design and writes in your style." },
      { n: "4", title: "Every shop, on time", text: "Spread out at the hours you pick." },
    ],
    files: [
      { n: "sunset-mountain.png", c: "#F2D27A" },
      { n: "mama-est-2019.png", c: "#E8707E" },
      { n: "coffee-then-chaos.png", c: "#7393B3" },
      { n: "+ 47 more designs", c: "#D9DCE5" },
    ],
    colours: [
      { shot: SHOTS.colour_white_flat_mountain, alt: "The same design on a white shirt" },
      { shot: SHOTS.colour_navy_mountain, alt: "The same design on a navy shirt" },
      { shot: SHOTS.colour_black_mountain, alt: "The same design on a black shirt" },
      { shot: SHOTS.colour_pink_mountain, alt: "The same design on a pink shirt" },
      { shot: SHOTS.colour_mint_mountain, alt: "The same design on a mint shirt" },
      { shot: SHOTS.colour_orange_mountain, alt: "The same design on an orange shirt" },
    ],
    written: "Written from the design",
    writtenTitle: "Retro Sunset Mountain Shirt, Garment Dyed Hiking Tee",
    tagsShort: ["hiking shirt", "camping tee", "retro sunset", "outdoor gift", "nature lover", "gift for him", "+7"],
    days: [
      { n: "M", a: "#7393B3", b: "#FFFFFF", c: "#FFFFFF" },
      { n: "T", a: "#7393B3", b: "#E8707E", c: "#FFFFFF" },
      { n: "W", a: "#FFFFFF", b: "#E8707E", c: "#1F8A4C" },
      { n: "T", a: "#7393B3", b: "#FFFFFF", c: "#1F8A4C" },
      { n: "F", a: "#7393B3", b: "#E8707E", c: "#1F8A4C" },
      { n: "S", a: "#FFFFFF", b: "#E8707E", c: "#FFFFFF" },
      { n: "S", a: "#FFFFFF", b: "#FFFFFF", c: "#1F8A4C" },
    ],
    legend: [
      { name: "Pine & Print", dot: "#7393B3" },
      { name: "Ink Lane", dot: "#E8707E" },
      { name: "Tee Street", dot: "#1F8A4C" },
    ],
  },

  shops: {
    heading: "One design. Every shop gets its own version.",
    sub: "Different cover photo, title, SKU and attributes in each shop. Write once, change only what you want.",
    cards: [
      { shop: "Pine & Print", when: "Tue 9:00", dot: "#7CC4FF", shot: SHOTS.shop_white_flat_mountain, title: "Retro Sunset Mountain Shirt, Garment Dyed Hiking Tee", sku: "PCS-417", extra: "Cover: white", alt: "White shirt as the cover in Pine & Print" },
      { shop: "Ink Lane Co.", when: "Tue 11:30", dot: "#FF8FA3", shot: SHOTS.shop_navy_mountain, title: "Mountain Sunset Tee, Camping Shirt for Hikers", sku: "ILC-417", extra: "Cover: navy", alt: "Navy shirt as the cover in Ink Lane Co." },
      { shop: "Tee Street", when: "Wed 9:00", dot: "#4ADE80", shot: SHOTS.shop_mint_mountain, title: "Vintage Hiking T-Shirt, Retro Outdoor Gift", sku: "TSC-417", extra: "Holiday: Father’s Day", alt: "Mint shirt as the cover in Tee Street" },
    ],
  },

  safe: {
    id: "safe",
    heading: "Your shop stays in your hands.",
    sub: "Your shop is your income. Listyro is built to never surprise you.",
    items: [
      { title: "Drafts first", text: "Every listing arrives as a draft. You see it before anyone else does.", bg: "#FFF3D1", icon: "M5 4 H15 L19 8 V20 H5 Z M15 4 V8 H19 M8 13 H16 M8 16 H13" },
      { title: "You approve, then it goes", text: "Nothing publishes until you approve it or set the time yourself.", bg: "#EAF6F0", icon: "M4 12.5 L9.5 18 L20 6" },
      { title: "Brand name warnings", text: "Trademarked names are flagged before you send.", bg: "#FBEDEF", icon: "M12 3 L21 19 H3 Z M12 9 V13 M12 16 V16.2" },
      { title: "Your real photos", text: "Mockups use your own garment photos. Your design is never redrawn.", bg: "#EEF3FB", icon: "M3 7 H7 L9 4 H15 L17 7 H21 V19 H3 Z M12 10 A3.5 3.5 0 1 0 12 17 A3.5 3.5 0 1 0 12 10" },
      { title: "Your designs stay yours", text: "They are never used to train AI models.", bg: "#F1ECFF", icon: "M6 11 V8 A6 6 0 0 1 18 8 V11 M5 11 H19 V21 H5 Z" },
      { title: "Leave anytime", text: "Disconnect a shop and its access is gone at once.", bg: "#F5F6FA", icon: "M9 7 L4 12 L9 17 M4 12 H15 M15 4 H20 V20 H15" },
    ],
    // "A person answers, not a bot": the contact page is how.
    contact: { before: "Stuck on something? ", link: "Write to us", href: "/contact", after: ". A person answers, not a bot." },
  },

  voices: { label: "What sellers say", testimonials: TESTIMONIALS, promise: PROMISE },

  pricing: {
    id: "pricing",
    heading: aboutPerListing(),
    sub: "Less than the coffee you drink while writing one by hand. Cancel anytime.",
    monthly: "Monthly",
    yearly: YEARLY_LABEL,
    per: { month: "/mo", year: "/yr" },
    cta: PRIMARY_CTA,
    note: `In the launch plans, only AI-written listings count. Hand-written listings and sending to more shops do not. Need more one month? Add ${EXTRA_PACK.listings} for $${EXTRA_PACK.price}.`,
    notice: PLANNED_NOTICE,
    link: { label: "See every plan", href: "/pricing" },
  },

  faq: {
    heading: "Questions sellers ask first",
    items: [
      { q: "Is it safe for my shop?", a: "Listyro connects through Etsy’s official sign-in and API. Listings arrive as drafts, and nothing goes live until you approve it or schedule it." },
      { q: "Will the titles sound like a robot?", a: "The AI reads each design and follows a style you copy from your own best listing. You can change every word before it goes out." },
      { q: "Do I need Photoshop for mockups?", a: "No. Upload a photo of a blank shirt once. Listyro places every design on it, keeping the folds and shading." },
      { q: "I run more than one shop.", a: "That is what Listyro is built for. Each shop gets its own photos, SKU, text and attributes, from one upload." },
      { q: "What counts as a listing?", a: "In the launch plans, each listing the AI writes. Hand-written listings and sending a listing to another shop do not count." },
      { q: "Can I cancel?", a: "Anytime, from your account. No contract, no questions." },
    ],
  },

  closing: {
    heading: "Your next 100 listings are one upload away.",
    body: `Connect your shop and get ${TRIAL.listings} listings free. If it doesn't save you time, walk away. No card, nothing to cancel.`,
    primary: PRIMARY_CTA,
  },
};

/**
 * Words the owner's design uses that a general rule of the site's copy would refuse, allowed here and only
 * as written (8 November 2026): Etsy's "official" API and sign-in are facts allowed by the API terms (the
 * trademark notice in every footer says Etsy does not endorse us), and "Going live in 3 shops" is a sample
 * batch's state in the hero, not a limit. The tests strip exactly these before checking the rules.
 */
export const APPROVED_PHRASES = [/Etsy[’']s official (API|sign-in)( and API)?/g, /Going live in 3 shops/g];
export const withoutApproved = (text: string) => APPROVED_PHRASES.reduce((t, phrase) => t.replace(phrase, ""), text);

/** The page's headings: the h1 and every h2 and h3 it renders. */
export function headings(): string[] {
  return [
    HOME.hero.heading,
    HOME.grind.heading,
    HOME.how.heading,
    ...HOME.how.steps.map((s) => s.title),
    HOME.shops.heading,
    HOME.safe.heading,
    ...HOME.safe.items.map((i) => i.title),
    HOME.pricing.heading,
    HOME.faq.heading,
    ...HOME.faq.items.map((i) => i.q),
    HOME.closing.heading,
  ];
}

/** Every string the page shows, for the copy rules (the customers' own words are checked apart). */
export function allCopy(): string[] {
  const out: string[] = [];
  const walk = (value: unknown, key = "") => {
    if (key === "testimonials" || key === "shot" || key === "href") return;
    if (typeof value === "string") {
      if (!/^#[0-9A-F]{6}$/i.test(value) && !/^M[\d. ]/.test(value)) out.push(value);
    } else if (Array.isArray(value)) value.forEach((v) => walk(v));
    else if (value && typeof value === "object") for (const [k, v] of Object.entries(value)) walk(v, k);
  };
  walk(HOME);
  return out;
}
