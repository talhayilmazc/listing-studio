/**
 * The feature pages. Every statement here is something the product does today (CLAUDE.md has the
 * detail); what a feature does not do is said on the same page. No promise of ranking or sales.
 */
import type { Block } from "./content.ts";
import { SCREENS, type Screen } from "./screens.ts";

export type Feature = {
  slug: string;
  /** The short name, in menus and cards. */
  name: string;
  title: string;
  description: string;
  h1: string;
  lead: string;
  /** One line on the features page. */
  summary: string;
  screen: Screen;
  body: Block[];
  guide: { href: string; label: string };
};

export const FEATURES: Feature[] = [
  {
    slug: "ai-listing-writer",
    name: "AI listing writer",
    title: "AI listing writer that reads each design | Listyro",
    description: "An AI listing generator for Etsy sellers: it looks at each design and writes a title, 13 tags and a description for you to edit. Nothing is sent until you approve it.",
    h1: "An AI listing writer that reads your design",
    lead: "Listyro looks at each design and writes a title, 13 tags and a description in the style you chose. You read and change every word before anything is sent to your shop.",
    summary: "Reads each design and writes a title, 13 tags and a description for you to edit.",
    screen: SCREENS.write,
    guide: { href: "/guides/etsy-title-rules-2026", label: "Listing title rules for 2026" },
    body: [
      { h2: "What it writes" },
      { ul: [
        "A title in one of two styles, chosen per profile: a short, clear title with the item and its key details first, or a longer keyword title.",
        "All 13 tags, each at most 20 characters, with no two tags that say the same thing.",
        "One or two opening sentences made for this design, above the description of the listing you chose as a [profile](/features/profiles).",
        "Attributes such as colour, occasion and theme, but only values Etsy lists for that category. If a value is a guess, the field stays empty.",
      ] },
      { h2: "What it checks" },
      { p: "Each listing has a search-readiness checklist: title length, tag count and format, repeated tags, and whether the title's words appear early in the description. It is a check against Etsy's published guidance. It does not predict how Etsy ranks your listing, and it never stops you approving." },
      { h2: "The trademark warning", id: "trademark-warning" },
      { p: "Before you send anything, Listyro says when a listing's text names a well-known brand or character. The warning names the brand and says why it matters: [Etsy's help](https://help.etsy.com/hc/en-us/articles/360000344448-How-to-Report-Intellectual-Property-Infringement) says that if an owner's report complies with its policies, it will remove the reported content. We warn; you decide." },
      { ul: [
        "It catches a name written in other ways too: joined words, hashtags, hyphens, spaces inside the name and common letter swaps.",
        "With the trademark filter on (the default), a name written whole stops the listing and tells you what to remove. Other spellings get the warning.",
        "With the filter off, every name gets the warning and you can still send. Turning it off asks you to accept the risk first.",
        "If the artwork itself looks like a known character, the listing is flagged too, because a drawing can infringe whatever its title says.",
      ] },
      { note: "This is a check against a list we keep, not legal advice, and it will not catch everything. Our guide to [trademarks](/guides/trademarks-and-ip-on-etsy) explains what happens when a listing is reported." },
      { h2: "Prefer to write it yourself?" },
      { p: "Write the title and tags yourself for any listing. No AI is called, everything else still comes from the profile, and your text goes through the same rules as the AI's." },
      { h2: "What it does not do" },
      { ul: [
        "It does not promise rankings or sales. Nobody outside Etsy can.",
        "It has no keyword volumes and looks at no other seller's listings: it works from your design and your own shop.",
        "It does not publish. Every listing waits for you.",
      ] },
      { h2: "Where your design goes" },
      { p: "To write the text, the design image is sent to our AI provider, Anthropic. Under its commercial terms it is not used to train models. The [Privacy Policy](/privacy#ai) has the detail." },
    ],
  },
  {
    slug: "mockup-generator",
    name: "Mockup generator",
    title: "Mockup generator for your own garment photos | Listyro",
    description: "Put every design on your own blank garment photos in one run. A mockup generator for Etsy print-on-demand sellers: geometry and shading only, and your design is never redrawn.",
    h1: "A mockup generator that uses your own garment photos",
    lead: "Upload photos of the blank garments you sell, check the print area once, and every design you add goes onto every photo. Your design's pixels are placed, never redrawn.",
    summary: "Puts every design on your own blank garment photos, with the print area you confirmed.",
    screen: SCREENS.mockups,
    guide: { href: "/guides/mockups-buyers-trust", label: "Mockups buyers trust" },
    body: [
      { h2: "How it works" },
      { ol: [
        "Make a template: a named group of your blank photos, such as one T-shirt in ten colours. You confirm that each photo is yours or that you may use it.",
        "Listyro suggests a print area on each photo. You check it once and press Looks right. A photo nobody has checked is never used.",
        "Add designs: several files, a folder or a ZIP. Choose the templates and create the mockups.",
        "Use them for listings, or download them as a ZIP.",
      ] },
      { h2: "What makes the result look printed" },
      { ul: [
        "The design follows the photo's perspective, folds and creases, and takes the fabric's light and shadow.",
        "Hair and arms in front of a shirt stay in front. If that would hide too much of the design, the area asks you to check it.",
        "A photo can hold several garments, or a front and a back, each with its own area.",
        "Files named like CDM290-white and CDM290-dark are one design: each garment colour gets the right file.",
      ] },
      { h2: "How it is made" },
      { p: "Deterministic image processing on our own server. Open-source analysis models find the garment and the print area; none of them draws anything, and no photo leaves the server for this. The same files give the same result." },
      { h2: "Adjust any single mockup" },
      { p: "Move, scale or rotate a design on one photo, change its opacity, or recolour part of it for dark garments. Apply the change to that design on every photo, or to every design on that photo, and undo it if you change your mind. Your uploaded file never changes." },
      { h2: "What it does not do" },
      { ul: [
        "It has no library of stock mockups: you bring the photos.",
        "It does not import PSD files or make video mockups.",
        "It generates no images and no designs.",
        "Automatic print areas are a first suggestion. They are strongest on plain backgrounds and single garments, which is why every photo waits for your check.",
      ] },
      { note: "Mockups do not replace Etsy's photo rules. [Etsy's help](https://help.etsy.com/hc/en-us/articles/44297885802647-How-Do-Etsy-s-Listing-Image-Requirements-Apply-to-My-Listings) says \"Etsy requires at least one unedited photo for most items\"; see our guide to [mockups buyers trust](/guides/mockups-buyers-trust). Mockups do not count towards the plan's listings." },
    ],
  },
  {
    slug: "multi-shop-publishing",
    name: "Multi-shop publishing",
    title: "Manage several shops from one login | Listyro",
    description: "Connect each of your Etsy shops to one Listyro login. Every shop keeps its own profile, SKU, title, tags and description, and you see exactly what goes to which shop.",
    h1: "Several shops, each with its own SKU and text",
    lead: "Connect each shop you run and work from one login. Choose a shop, and you see each listing as it would go there: that shop's profile, SKU, title, tags and description.",
    summary: "One login for your shops, each with its own profile, SKU and text.",
    screen: SCREENS.shops,
    guide: { href: "/guides/running-more-than-one-etsy-shop", label: "Running more than one shop: what the rules say" },
    body: [
      { h2: "What each shop keeps", id: "per-shop-text" },
      { ul: [
        "Its own shipping profile, return policy and processing profile. When a listing goes to another of your shops, these are matched by name or terms, or copied once from your main shop, and you can change them.",
        "Its own section, title prefix and daily listing limit.",
        "For any listing: its own SKU, title, tags and description. A field you leave alone follows the listing.",
      ] },
      { h2: "Sending to a shop" },
      { ul: [
        "A shop workspace on each batch shows what is not sent, drafted, scheduled and published in that shop.",
        "A SKU already used in the shop is refused before anything is sent, with the list of clashes.",
        "Shop groups let you choose several shops at once. Nothing changes in a shop you did not choose.",
        "If Etsy refuses something in one shop, the others carry on and you see Etsy's reason.",
      ] },
      { h2: "How many shops" },
      { p: "Several shops; the number is set per account. Each shop is a separate Etsy account and connects through Etsy's own sign-in page." },
      { note: "Etsy's [Seller Policy](https://www.etsy.com/legal/sellers) prohibits creating duplicate shops or other actions \"for the purpose of shilling, manipulating search or circumventing Etsy's policies\". Read [our guide](/guides/running-more-than-one-etsy-shop) before you send one design to several shops. You are responsible for following Etsy's rules in each shop." },
    ],
  },
  {
    slug: "scheduling",
    name: "Scheduling",
    title: "Schedule the listings you have approved | Listyro",
    description: "Pick the time each approved listing goes live, in your own time zone, or lay out a calendar for a whole group of shops. Each listing is checked again before it publishes.",
    h1: "Schedule the listings you have approved",
    lead: "Choose when each approved listing goes live, in your own time zone. See every planned listing in one list, move it, pause it, or publish it now. Nothing you have not approved is ever published.",
    summary: "Pick when each approved listing goes live, and see every planned one in one list.",
    screen: SCREENS.schedule,
    guide: { href: "/guides/holiday-listing-calendar-pod", label: "A holiday listing calendar for POD sellers" },
    body: [
      { h2: "Two ways to plan" },
      { ul: [
        "One listing: approve it and pick its time.",
        "A group: choose shops, a start day, how many listings a day, a daily window and the minutes between them. You see the whole calendar, listing by listing, before you confirm it.",
      ] },
      { h2: "Change your mind freely" },
      { ul: [
        "Change a time in place, or drag a listing to another day.",
        "Select several: set a time, shift them, spread them out or move them to a day. You see old and new times before saving, and one Undo puts them back.",
        "Pause a group or everything. Pausing cancels nothing; on resume, anything past its time goes out.",
      ] },
      { h2: "Checked again at publish time" },
      { p: "When its time comes, each listing is checked again: is it still approved, is the shop still connected, do the checks still pass? If not, it is not published and the Scheduled page says why. One failure does not hold up the rest." },
      { p: "If you publish a draft yourself in Etsy's Shop Manager, or delete it there, Listyro sees that and takes it off the schedule." },
      { note: "Scheduling is included in some of the plans we intend to offer and an add-on in others; see [pricing](/pricing)." },
    ],
  },
  {
    slug: "profiles",
    name: "Profiles",
    title: "Copy listing settings from a listing you sell | Listyro",
    description: "Pick a listing you already sell. New Etsy drafts copy its category, price, variations, shipping, returns, size charts and production details, and are read back to check.",
    h1: "Every new draft starts from a listing you already sell",
    lead: "A profile is one of your own listings, used as the model for new ones. Its settings are copied to each new draft, so you only decide what is new: the design and the words.",
    summary: "New drafts copy their settings from a listing you already sell.",
    screen: SCREENS.profiles,
    guide: { href: "/guides/list-print-on-demand-shirts-in-bulk", label: "Listing print-on-demand shirts in bulk" },
    body: [
      { h2: "What is copied" },
      { ul: [
        "Category, price and variations, and each variation's SKU pattern.",
        "Shipping profile, return policy, processing profile, weight and package size.",
        "Who made it, when it was made, production partners and materials.",
        "Size charts, in the position you choose among the photos.",
        "Personalization settings, which you can change listing by listing.",
      ] },
      { p: "After each draft is made, Listyro reads it back from Etsy and compares what it set. If something did not take, you are told which field." },
      { h2: "Only your own listings" },
      { p: "A profile can only be made from a listing in a shop you connected. Listyro never reads another seller's listing." },
      { h2: "In your other shops" },
      { p: "A profile belongs to your account. Used in another of your shops, it takes that shop's own shipping, return and processing settings: the same ones if they exist, otherwise copied once. You can change them for that shop." },
      { h2: "What cannot be copied" },
      { p: "Etsy's \"How it's made\" answers (production method and tools) are not available to apps yet, so they cannot be sent. You can set them for many listings at once in Shop Manager." },
    ],
  },
  {
    slug: "profit-report",
    name: "Profit report",
    title: "Profit report: earnings, fees, ads and costs | Listyro",
    description: "A profit, cost and ads report for your own Etsy shop. Every figure says where it came from, and a figure we cannot back up is left blank with the reason.",
    h1: "A profit report for your own shop",
    lead: "See what a month earned after Etsy's fees, your ad spend and your product costs. Every figure says where it came from. A figure we cannot back up is left blank, with the reason, and is never shown as zero.",
    summary: "What a month earned after fees, ads and costs, with every figure's source.",
    screen: SCREENS.report,
    guide: { href: "/guides", label: "All our guides for Etsy sellers" },
    body: [
      { h2: "Where the figures come from" },
      { ul: [
        "Sales and Etsy's fees are read from your shop through Etsy's API and updated each night, with the last 30 days read again so refunds show up.",
        "Upload your monthly statement CSV and it becomes the top source for that month. Its lines add up to the file's total to the cent, and each difference from our own figure is explained.",
        "Upload your Etsy Ads report for daily ad spend. It is a shop-wide figure and is not spread over listings.",
        "You add your own product costs per profile.",
      ] },
      { h2: "What it keeps" },
      { p: "Order numbers, listing ids, amounts and dates. Buyer names, addresses, emails and messages are never read into it or stored. The files you upload are read in memory and not kept." },
      { h2: "How long" },
      { p: "It is kept as long as the [Privacy Policy](/privacy) says, and deleted at once when you disconnect the shop. You can also delete your sales history yourself; the report then starts from that day." },
      { h2: "What it is not" },
      { p: "It is your own profit, cost and ads report, not a copy of Etsy Stats. It shows listing views and favourites from Etsy, but Etsy's API has no search impressions, search terms or traffic sources, so neither do we. Ad suggestions point you to Shop Manager: Listyro never changes your ads." },
    ],
  },
];

export const FEATURES_PAGE = {
  title: "Features: listings, mockups, shops and scheduling | Listyro",
  description: "What Listyro does for Etsy print-on-demand sellers: AI-written listings, mockups on your own photos, several shops, scheduling, profiles and a profit report.",
  h1: "What Listyro does",
  lead: "Six tools for sellers who add new designs often. You can use any of them without the others, and nothing reaches your shop until you approve it.",
};
