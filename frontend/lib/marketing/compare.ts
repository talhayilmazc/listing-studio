/**
 * The compare pages. Facts about other products come only from their own public pages, read on the
 * "last checked" day and linked; anything those pages did not state says so rather than guessing.
 * What they do that Listyro does not is said plainly, and nothing here runs anyone down
 * (lib/marketingSite.test.ts). Listyro's own planned prices come from lib/plans.ts.
 */
import { PLANS, priceFact } from "../plans.ts";
import type { Source } from "./content.ts";

const CHECKED = "2026-11-01";
const NOT_STATED = "Not stated on the pages we read";

const first = PLANS[0];
const last = PLANS[PLANS.length - 1];

/** Listyro's side of every table. */
export const LISTYRO = {
  access: "Invite-only early access; no payment is taken today",
  price: `Planned after launch: ${priceFact(first)} to ${priceFact(last)} a month, not per shop`,
  marketplaces: "Etsy only",
  writing: "Reads the design and writes the title, 13 tags and description",
  mockups: "On your own blank garment photos; no stock library, PSD import or video",
  images: "None: it never draws or generates an image",
  trademark: "Warns before sending, on brand and character names; flags artwork",
  scheduling: "Each listing, or a group calendar across shops; in the larger planned plans, an add-on in the smaller",
  shops: "Several shops; the number is set per account",
  bulk: "No general editor for live listings",
  research: "None: it reads only your own shop",
  profit: "Profit, cost and ads report for your own shop",
  fulfilment: "None",
};

export type Compare = {
  slug: string;
  them: string;
  url: string;
  title: string;
  description: string;
  h1: string;
  lead: string;
  checked: string;
  rows: { label: string; listyro: string; them: string }[];
  theirs: string[];
  ours: string[];
  chooseThem: string;
  chooseUs: string;
  note?: string;
  sources: Source[];
};

export const COMPARES: Compare[] = [
  {
    slug: "listyro-vs-listadum",
    them: "Listadum",
    url: "https://listadum.com/",
    title: "Listyro vs Listadum: listing tools compared | Listyro",
    description: "A factual comparison of Listyro and Listadum for Etsy sellers: mockups, AI writing, bulk editing, keyword research, scheduling and price, with sources.",
    h1: "Listyro vs Listadum",
    lead: "Both work with Etsy through its API. Listadum is a broad shop and listing manager with research tools. Listyro is narrower: it takes new designs from files to approved listings.",
    checked: CHECKED,
    rows: [
      { label: "Access", listyro: LISTYRO.access, them: "Open sign-up, with a free plan \"forever\"" },
      { label: "Price", listyro: LISTYRO.price, them: "Free; Starter $15, Growth $30, Max $150 a month per shop ($10, $20, $100 a month billed yearly)" },
      { label: "Marketplaces", listyro: LISTYRO.marketplaces, them: "Etsy is the only marketplace named" },
      { label: "AI listing text", listyro: LISTYRO.writing, them: "AI Helper generates titles and descriptions" },
      { label: "Mockups", listyro: LISTYRO.mockups, them: "Not in its feature list; its footer links to a mockup generator on a separate site, artadum.com" },
      { label: "Image tools", listyro: LISTYRO.images, them: "AI Photo Editor, AI Photo Studio, Video Generator, Image Upscaler" },
      { label: "Scheduling", listyro: LISTYRO.scheduling, them: "Listing Scheduler, on the Growth plan" },
      { label: "Several shops", listyro: LISTYRO.shops, them: "Connect and switch between shops; prices are per shop" },
      { label: "Editing live listings in bulk", listyro: LISTYRO.bulk, them: "Bulk Edit, on Growth and Max, one shop at a time" },
      { label: "Keyword and market research", listyro: LISTYRO.research, them: "Keyword Explorer, Shop Explorer, Shop Tracker, Listing Explorer" },
      { label: "Profit", listyro: LISTYRO.profit, them: "Profit Tracking, on the Growth plan" },
      { label: "Trademark check", listyro: LISTYRO.trademark, them: NOT_STATED },
    ],
    theirs: [
      "A free plan with no time limit, and open sign-up today.",
      "Bulk editing of live listings, including photos, prices and tags.",
      "Keyword research, and research into other shops and listings.",
      "Pinterest and Instagram promotion, team access, and API access as an add-on.",
      "Monitoring of listing performance, and AI photo and video tools.",
    ],
    ours: [
      "Mockups on your own garment photos, as part of the same workflow as the listing.",
      "Text written by looking at the design itself, in the title style you chose per profile.",
      "A warning before sending when a listing names a well-known brand or character.",
      "A per-shop workspace: each shop's own profile, SKU, title, tags and description for one listing.",
    ],
    chooseThem: "you want one tool to manage and research a whole Etsy shop, you want bulk edits on live listings, or you want to start on a free plan today.",
    chooseUs: "you add new print-on-demand designs often, and want them taken from files to reviewed, scheduled listings, mockups included.",
    sources: [
      { label: "Listadum home page", url: "https://listadum.com/" },
      { label: "Listadum features", url: "https://www.listadum.com/features" },
      { label: "Listadum pricing", url: "https://listadum.com/pricing" },
      { label: "Listadum bulk edit", url: "https://www.listadum.com/features/bulk-edit" },
    ],
  },
  {
    slug: "listyro-vs-mydesigns",
    them: "MyDesigns",
    url: "https://mydesigns.io/",
    title: "Listyro vs MyDesigns: print-on-demand listing tools | Listyro",
    description: "A factual comparison of Listyro and MyDesigns for print-on-demand sellers: mockups, AI listing text, marketplaces, fulfilment and price, with sources.",
    h1: "Listyro vs MyDesigns",
    lead: "MyDesigns covers the whole print-on-demand business, from finding niches and generating designs to fulfilment, on several marketplaces. Listyro does one part, on Etsy only: your finished designs, to reviewed listings.",
    checked: CHECKED,
    rows: [
      { label: "Access", listyro: LISTYRO.access, them: "Open sign-up, with a free plan and no card needed" },
      { label: "Price", listyro: LISTYRO.price, them: "Free; Starter $24.99, Pro $49.99, Pro Plus $99.99 a month ($19.99, $39.99, $79.99 a month billed yearly)" },
      { label: "Marketplaces", listyro: LISTYRO.marketplaces, them: "Etsy, Shopify, WooCommerce, TikTok, and its own MyDesigns Store" },
      { label: "AI listing text", listyro: LISTYRO.writing, them: "Vision AI, a daily allowance on paid plans" },
      { label: "Mockups", listyro: LISTYRO.mockups, them: "Bulk product mockups, bulk video mockups, custom PSD mockups" },
      { label: "Image tools", listyro: LISTYRO.images, them: "Dream AI image generation, background removal, upscaling, a canvas editor" },
      { label: "Trademark check", listyro: LISTYRO.trademark, them: "Bulk Trademark Check is listed" },
      { label: "Scheduling", listyro: LISTYRO.scheduling, them: NOT_STATED },
      { label: "Several shops", listyro: LISTYRO.shops, them: "Per plan, on each platform: 1 on Free and Starter, 3 on Pro, unlimited on Pro Plus" },
      { label: "Market research", listyro: LISTYRO.research, them: "Scout AI" },
      { label: "Profit", listyro: LISTYRO.profit, them: "Store analytics listed; profit reporting is not stated on the pages we read" },
      { label: "Print fulfilment", listyro: LISTYRO.fulfilment, them: "Included, through its print partners" },
    ],
    theirs: [
      "Selling on Shopify, WooCommerce and TikTok as well as Etsy.",
      "Generating images and designs, and an editor to make them in.",
      "Print fulfilment, with its own catalogue.",
      "PSD and video mockups.",
      "A free plan, and open sign-up today.",
    ],
    ours: [
      "Mockups on your own photos, with a print area you confirm once for each photo.",
      "Each new listing copied from a listing you already sell, and read back from Etsy afterwards.",
      "Scheduling with per-shop limits, windows and spacing, checked again at publish time.",
      "A profit report built from Etsy's fee records and your monthly statements.",
    ],
    chooseThem: "you want one platform for designing, mockups, several marketplaces and fulfilment.",
    chooseUs: "you already have your designs, your production partner and an Etsy shop that works, and want new listings to match the ones you already sell.",
    sources: [
      { label: "MyDesigns home page", url: "https://mydesigns.io/" },
      { label: "MyDesigns pricing", url: "https://mydesigns.io/pricing" },
    ],
  },
  {
    slug: "listyro-vs-vela",
    them: "Vela",
    url: "https://getvela.com/",
    title: "Listyro vs Vela: listing tools compared | Listyro",
    description: "A factual comparison of Listyro and Vela for Etsy sellers: bulk editing, copying between channels, AI, scheduling, mockups and price, with sources.",
    h1: "Listyro vs Vela",
    lead: "Vela is a listing editor for several marketplaces, strong on editing and copying existing listings in bulk. Listyro starts earlier, on Etsy only: from your design files to new listings.",
    checked: CHECKED,
    rows: [
      { label: "Access", listyro: LISTYRO.access, them: "Open sign-up, with a one-week free trial and no free plan stated" },
      { label: "Price", listyro: LISTYRO.price, them: "From $29.95 a month per shop on Lite and $39.95 on Plus, rising with listing count; 20% off with several shops. Its Shopify App Store page shows $19.95 and $29.95" },
      { label: "Marketplaces", listyro: LISTYRO.marketplaces, them: "Etsy, Shopify, eBay and Faire" },
      { label: "AI listing text", listyro: LISTYRO.writing, them: "AI listing optimization, one at a time or in bulk, and AI copy" },
      { label: "Mockups", listyro: LISTYRO.mockups, them: "\"AI mockup generation\" is on its pricing page, shown as coming soon when we checked" },
      { label: "Image tools", listyro: LISTYRO.images, them: "Photo library and editor; AI background removal, background generation and more" },
      { label: "Scheduling", listyro: LISTYRO.scheduling, them: "Schedule publication and updates, up to thirty days ahead" },
      { label: "Several shops", listyro: LISTYRO.shops, them: "Connect as many shops as you like, priced per shop" },
      { label: "Editing live listings in bulk", listyro: LISTYRO.bulk, them: "Bulk edit of all product attributes, and CSV export and import" },
      { label: "Copying between shops", listyro: "Sends a listing to the shops you choose, each with its own profile, SKU and text", them: "Cross Channel Copy between connected shops, and merging listings" },
      { label: "Profit", listyro: LISTYRO.profit, them: NOT_STATED },
      { label: "Trademark check", listyro: LISTYRO.trademark, them: NOT_STATED },
    ],
    theirs: [
      "Etsy, Shopify, eBay and Faire from one tool.",
      "Bulk editing of live listings, and CSV import and export.",
      "Copying and merging listings across channels.",
      "An AI photo studio and listing scoring.",
      "A free trial, and open sign-up today.",
    ],
    ours: [
      "Mockups on your own garment photos.",
      "Text written by looking at the design, for new listings.",
      "A warning before sending when a listing names a well-known brand or character.",
      "A profit report built from Etsy's fee records and your monthly statements.",
    ],
    chooseThem: "you sell on more than Etsy, or your main job is editing and copying listings you already have.",
    chooseUs: "your main job is turning new designs into Etsy listings.",
    sources: [
      { label: "Vela pricing", url: "https://getvela.com/pricing" },
      { label: "Vela features", url: "https://getvela.com/features" },
      { label: "Vela on the Shopify App Store", url: "https://apps.shopify.com/vela" },
    ],
  },
];

export const ALTERNATIVES = {
  slug: "alternatives",
  title: "Listyro alternatives for print-on-demand sellers | Listyro",
  description: "Other ways to do what Listyro does: Etsy's own Shop Manager, Listadum, MyDesigns and Vela. When each one fits better, with sources and a last-checked date.",
  h1: "Listyro alternatives",
  lead: "Listyro is not for everyone, and it is invite-only today. Here are other ways to get listings done, and when each fits better.",
  checked: CHECKED,
  options: [
    { name: "Etsy's own Shop Manager", href: "https://www.etsy.com/your/shops/me/tools/listings", body: "Comes with your shop. If you add a few listings at a time, it may be all you need.", fits: "Few new listings, and one shop." },
    { name: "Listadum", href: "/compare/listyro-vs-listadum", body: "A shop and listing manager for Etsy with keyword and shop research, bulk editing and a free plan.", fits: "Managing and researching a whole shop." },
    { name: "MyDesigns", href: "/compare/listyro-vs-mydesigns", body: "A print-on-demand platform from design to fulfilment, on Etsy, Shopify, WooCommerce and TikTok.", fits: "One platform for the whole business, on several marketplaces." },
    { name: "Vela", href: "/compare/listyro-vs-vela", body: "A listing editor for Etsy, Shopify, eBay and Faire, with bulk editing and copying between channels.", fits: "Editing and copying existing listings across marketplaces." },
  ],
  listyro: "Listyro fits when you add new print-on-demand designs to Etsy often and want each listing written, checked, approved by you and scheduled. It does not fit if you sell mainly elsewhere, want research into other shops, or need something open to everyone today.",
};

export const COMPARE_PAGE = {
  title: "Compare Listyro with other listing tools | Listyro",
  description: "Factual comparisons of Listyro with Listadum, MyDesigns and Vela: what each does, what each costs and when each fits, from their own public pages.",
  h1: "Compare Listyro",
  lead: "Taken from each product's own public pages, with links and the day we checked. Prices and features change, so check their page before deciding.",
};

export { NOT_STATED };
