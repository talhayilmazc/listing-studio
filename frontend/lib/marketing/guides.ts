/**
 * The guides. Original writing in plain English. What Etsy says is linked to Etsy's own page; what
 * is our own advice says so. No promise of ranking or sales (lib/marketingSite.test.ts). Etsy
 * changes its rules, so each guide carries the day it was checked.
 */
import { allText, type Block, type Source } from "./content.ts";

export type Guide = {
  slug: string;
  title: string;
  description: string;
  h1: string;
  lead: string;
  published: string;
  checked: string;
  feature: { href: string; label: string; line: string };
  sources: Source[];
  body: Block[];
};

const HANDBOOK = "https://www.etsy.com/seller-handbook/article";
const S = {
  search: { label: "Etsy Seller Handbook: How Etsy Search Works (updated 26 August 2025)", url: `${HANDBOOK}/375461474487` },
  titles: { label: "Etsy Seller Handbook: New Guidance for Listing Titles (27 April 2026)", url: `${HANDBOOK}/1399426136697` },
  keywords: { label: "Etsy Seller Handbook: Keywords 101 (updated 26 August 2025)", url: `${HANDBOOK}/382774281517` },
  anatomy: { label: "Etsy Seller Handbook: Anatomy of a Well-Crafted Listing (26 August 2025)", url: `${HANDBOOK}/1347574487014` },
  bestPractices: { label: "Etsy Seller Handbook: 4 Policy Best Practices When Listing Items on Etsy", url: `${HANDBOOK}/1075628311049` },
  ipQuestions: { label: "Etsy Seller Handbook: 5 Common Questions About Intellectual Property on Etsy", url: `${HANDBOOK}/22451909389` },
  holidayChecklist: { label: "Etsy Seller Handbook: Seller Holiday Checklist", url: `${HANDBOOK}/1401252562091` },
  holidayPrepare: { label: "Etsy Seller Handbook: Prepare Your Shop for Holiday Success", url: `${HANDBOOK}/30953650830` },
  holidayMerch: { label: "Etsy Seller Handbook: Your Holiday Merchandising Guide (an older article)", url: `${HANDBOOK}/22818264711` },
  sellerPolicy: { label: "Etsy: Seller Policy", url: "https://www.etsy.com/legal/sellers/" },
  creativity: { label: "Etsy: Creativity Standards", url: "https://www.etsy.com/legal/creativity" },
  imageRules: { label: "Etsy: Listing Image Requirements", url: "https://www.etsy.com/legal/policy/listing-image-requirements/253962679005" },
  imageHelp: { label: "Etsy Help: How Do Etsy's Listing Image Requirements Apply to My Listings?", url: "https://help.etsy.com/hc/en-us/articles/44297885802647-How-Do-Etsy-s-Listing-Image-Requirements-Apply-to-My-Listings" },
  partners: { label: "Etsy Help: Working with Production Partners on Etsy", url: "https://help.etsy.com/hc/en-us/articles/360000336547-Working-with-Production-Partners-on-Etsy" },
  secondShop: { label: "Etsy Help: How to Open a Second Shop on Etsy", url: "https://help.etsy.com/hc/en-us/articles/360017604474-How-to-Open-a-Second-Shop-on-Etsy" },
  ipPolicy: { label: "Etsy: Intellectual Property Policy", url: "https://www.etsy.com/legal/ip/" },
  ipIssues: { label: "Etsy: Copyright and Intellectual Property Policy Issues", url: "https://www.etsy.com/legal/policy/copyright-and-intellectual-property/242644375651" },
  counter: { label: "Etsy: Submitting a DMCA Counter Notice", url: "https://www.etsy.com/legal/ip-counter/" },
} satisfies Record<string, Source>;

const CHECKED = "2026-11-01";

/* The calendar's dates. "Live by" is our own rule of thumb, worked out here and not typed. */
const LIVE_WEEKS = 8;
const EVENTS: [string, string][] = [
  ["Black Friday", "2026-11-27"],
  ["Christmas", "2026-12-25"],
  ["Valentine's Day", "2027-02-14"],
  ["St Patrick's Day", "2027-03-17"],
  ["Easter", "2027-03-28"],
  ["Mother's Day (US)", "2027-05-09"],
  ["Father's Day (US)", "2027-06-20"],
  ["Independence Day (US)", "2027-07-04"],
  ["Halloween", "2027-10-31"],
  ["Thanksgiving (US)", "2027-11-25"],
  ["Black Friday", "2027-11-26"],
  ["Christmas", "2027-12-25"],
];
const SHORT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const short = (date: Date) => SHORT.format(date);
export function liveBy(iso: string): string {
  return short(new Date(Date.parse(`${iso}T00:00:00Z`) - LIVE_WEEKS * 7 * 86_400_000));
}
const CALENDAR_ROWS = EVENTS.map(([name, iso]) => [name, short(new Date(`${iso}T00:00:00Z`)), liveBy(iso)]);
export const CALENDAR_DATES = EVENTS;

export const GUIDES: Guide[] = [
  {
    slug: "etsy-title-rules-2026",
    title: "Etsy title rules 2026: what Etsy's guidance says | Listyro",
    description: "What Etsy's current guidance says about listing titles: under 15 words, the item first, no gift phrases or repeated words, and which characters Etsy refuses.",
    h1: "Etsy title rules 2026",
    lead: "Etsy changed its title guidance in April 2026. Here is what it says, what is a hard limit and what is advice, and how to rewrite a keyword-stuffed title.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/ai-listing-writer", label: "the AI listing writer", line: "Listyro writes titles in this short style by default and checks them against these rules." },
    sources: [S.titles, S.anatomy, S.search],
    body: [
      { h2: "The short version" },
      { p: "Say what the item is first, then its most important objective details, in fewer than 15 words. Keywords that do not fit go in your tags, attributes and description. That comes from Etsy's [New Guidance for Listing Titles](https://www.etsy.com/seller-handbook/article/1399426136697) and [Anatomy of a Well-Crafted Listing](https://www.etsy.com/seller-handbook/article/1347574487014)." },
      { h2: "What Etsy's guidance asks for" },
      { ul: [
        "Keep it under 15 words.",
        "Start with what the item is, followed by its key traits: colour, material, style.",
        "Leave out subjective words such as \"perfect\" or \"beautiful\".",
        "Leave out gifting and aspirational phrases such as \"gift for her\".",
        "Mention holidays, occasions or recipients only when they are essential to what the item is.",
        "Do not repeat words.",
        "Leave out price, shipping and sale information.",
      ] },
      { note: "Etsy does not say that long titles are penalised. It says search now takes a more holistic view, and that keywords moved out of the title belong in tags, attributes and the description. It is guidance, not a promise about ranking." },
      { h2: "What is a hard limit" },
      { p: "These are different from the advice above: Etsy refuses a title that breaks them." },
      { ul: [
        "At most 140 characters.",
        "Only letters, numbers, punctuation, mathematical symbols, spaces and the symbols ™ © ®.",
        "The characters %, :, & and + at most once each.",
      ] },
      { h2: "A worked example" },
      { table: {
        caption: "One made-up listing, before and after",
        head: ["", "Title", "Why"],
        rows: [
          ["Before", "Mountain Shirt, Hiking Shirt, Perfect Gift for Him, Camping Tee, Mountain Lover Gift, Outdoors Shirt", "18 words, repeats \"mountain\" and \"shirt\", and uses a subjective word and gift phrases"],
          ["After", "Retro Mountain Graphic T-Shirt, Cotton Crewneck in Forest Green", "9 words: the item first, then material and colour"],
        ],
      } },
      { p: "\"Hiking shirt\", \"camping tee\" and \"gift for him\" are not lost. They move to tags, where Etsy still matches them." },
      { h2: "A recipient that defines the item" },
      { p: "\"Nurse Shirt\" is fine if the design is about nursing, because that is what the item is. \"Shirt for Nurses, Gift for Her\" is the gifting phrase Etsy asks you to leave out." },
      { h2: "How search uses the title" },
      { p: "Etsy's [How Etsy Search Works](https://www.etsy.com/seller-handbook/article/375461474487) says a search is matched against the title, tags, attributes, description, first photo and reviews, and then ranked on several things including listing quality and customer service. A title is one input among many." },
    ],
  },
  {
    slug: "list-print-on-demand-shirts-in-bulk",
    title: "How to list print-on-demand shirts on Etsy in bulk | Listyro",
    description: "A step-by-step way to list many print-on-demand shirts on Etsy: one good model listing, SKU file names, mockups, one review per listing, and a schedule.",
    h1: "Listing print-on-demand shirts on Etsy in bulk",
    lead: "Adding many designs is mostly the same work repeated. Here is a way to do that work once, while still looking at each listing before it goes live.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/profiles", label: "Profiles", line: "Listyro's profiles do step 1 for you: new drafts copy the settings of a listing you already sell." },
    sources: [S.creativity, S.partners, S.imageHelp, S.anatomy],
    body: [
      { p: "This guide works with any tools, including a spreadsheet and Etsy's own Shop Manager. Where Listyro does a step for you, we say so." },
      { h2: "1. Build one listing properly" },
      { p: "Everything else copies from this one, so a mistake here is repeated in all of them. Get it right first: category, price, variations and sizes, shipping profile, return policy, processing time, materials, size chart." },
      { h2: "2. Say honestly who makes it" },
      { p: "Etsy's [Creativity Standards](https://www.etsy.com/legal/creativity) give a seller's original design printed by a production partner as an example of \"Designed by\". Etsy requires sellers who use production assistance to [say so on the listing](https://help.etsy.com/hc/en-us/articles/360000336547-Working-with-Production-Partners-on-Etsy). Set this on your model listing, so every copy carries it." },
      { note: "Etsy treats a buyer-personalized T-shirt printed on demand differently: its Creativity Standards give that as an example of \"Sourced by\". Check which applies to what you sell." },
      { h2: "3. Name your files by SKU" },
      { p: "Give each design a SKU and put it in the file names: BR5229-front.png, BR5229-back.png. Or use one folder per design. Then any tool, or you, can group the photos into listings without guessing." },
      { h2: "4. Make the mockups once" },
      { p: "Choose a small set of photos and use them for every design, so your shop looks consistent. Etsy keeps at most 20 images on a listing. Read [mockups buyers trust](/guides/mockups-buyers-trust) first: Etsy has rules about what the first photo must show." },
      { h2: "5. Write each listing for its design" },
      { p: "The settings repeat; the words should not. Each title should say what this design is, and each set of tags should fit it. Follow the [title rules](/guides/etsy-title-rules-2026) and the [tag rules](/guides/etsy-tags-13-tags-20-characters). Check for [brand and character names](/guides/trademarks-and-ip-on-etsy) now, while it is cheap to change them." },
      { h2: "6. Look at each one" },
      { p: "Speed comes from the repeated work, not from skipping this. Read each title against its picture. One wrong size chart or one brand name left in a title across a large batch costs far more than the minutes saved." },
      { h2: "7. Spread them out" },
      { p: "Create drafts first, then publish over days rather than all at once. It gives you time to spot a mistake while it is still in only a few listings." },
      { h2: "8. Keep a record" },
      { p: "A SKU on every listing ties an order back to its design file and to the print file your production partner needs." },
      { note: "Listing faster does not change what a listing has to be. Etsy's rules apply to each listing, however it was made, and we cannot tell you how any of it will sell." },
    ],
  },
  {
    slug: "etsy-tags-13-tags-20-characters",
    title: "Etsy tags: 13 tags, 20 characters, and what to put in them | Listyro",
    description: "Etsy gives you 13 tags of up to 20 characters. What Etsy's guidance says to put in them, what to leave out, and which characters it refuses.",
    h1: "Etsy tags: 13 tags and 20 characters",
    lead: "Thirteen short phrases that tell Etsy's search what else your listing is. Here is how Etsy says to use them.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/ai-listing-writer", label: "the AI listing writer", line: "Listyro writes 13 tags to these rules and shows each tag's length as you edit." },
    sources: [S.keywords, S.anatomy],
    body: [
      { h2: "The limits" },
      { ul: [
        "At most 13 tags on a listing.",
        "At most 20 characters in a tag.",
        "Only letters, numbers, spaces, the characters - and ', and the symbols ™ © ®.",
      ] },
      { p: "A curly apostrophe (’) is not the straight one Etsy accepts, and a long dash (–) is not a hyphen. Text pasted from a word processor can contain both, and Etsy refuses them." },
      { h2: "What Etsy says to do" },
      { p: "From [Keywords 101](https://www.etsy.com/seller-handbook/article/382774281517) and [Anatomy of a Well-Crafted Listing](https://www.etsy.com/seller-handbook/article/1347574487014):" },
      { ul: [
        "Use all 13.",
        "Use multi-word, natural phrases, and vary them.",
        "Do not repeat a tag.",
        "Do not repeat your category or attribute values: Etsy already matches on those.",
        "Do not add singular and plural versions: Etsy matches root words.",
      ] },
      { h2: "What to put in them" },
      { p: "This is our own suggestion, not Etsy's. Think of what different shoppers would type for this item:" },
      { table: {
        caption: "An example set for a made-up retro mountain T-shirt",
        head: ["Kind of phrase", "Example tags"],
        rows: [
          ["What it is", "graphic tee, mountain t shirt"],
          ["Style", "retro hiking tee, vintage outdoors"],
          ["Who it is for", "hiker gift, camping dad shirt"],
          ["When", "fathers day hiker, summer camp tee"],
          ["Theme", "national park tee, nature lover"],
        ],
      } },
      { p: "Gift and occasion phrases are welcome here. Etsy asks you to keep them out of titles, so tags are where they go." },
      { h2: "Common mistakes" },
      { ul: [
        "Thirteen variations of one phrase. \"mountain shirt\" and \"shirts mountain\" are the same words to a search that matches root words.",
        "Tags that only repeat the title. Keep those for when you have run out of different phrases.",
        "Brand or character names. A tag can get a listing removed just as a title can: see [trademarks on Etsy](/guides/trademarks-and-ip-on-etsy).",
      ] },
      { note: "Good tags help Etsy understand your listing. Nobody outside Etsy can tell you how a listing will rank, and we do not try." },
    ],
  },
  {
    slug: "holiday-listing-calendar-pod",
    title: "A holiday listing calendar for print-on-demand sellers | Listyro",
    description: "Dates for the main selling holidays, with a planning rule for when to have print-on-demand listings live, and what to do about shipping cutoffs.",
    h1: "A holiday listing calendar for POD sellers",
    lead: "Holiday designs have a deadline that ordinary ones do not. Here are the dates, a simple way to plan backwards, and the points people forget.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/scheduling", label: "Scheduling", line: "Listyro lets you approve listings now and choose the day each goes live, shop by shop." },
    sources: [S.holidayChecklist, S.holidayPrepare, S.holidayMerch, S.titles],
    body: [
      { h2: "The calendar" },
      { p: `"Live by" is our own rule of thumb: ${LIVE_WEEKS} weeks before the day. It is not an Etsy rule, and it does not promise sales. Move it earlier for gifts that take longer to arrive.` },
      { table: { caption: "Main selling dates, mostly US", head: ["Event", "Date", "Live by"], rows: CALENDAR_ROWS } },
      { p: "If a \"live by\" date has passed, it is usually still worth listing. Just check that orders can still arrive in time." },
      { h2: "What Etsy suggests" },
      { ul: [
        "Plan early. An older [Etsy merchandising guide](https://www.etsy.com/seller-handbook/article/22818264711) suggests releasing seasonal lines 4 to 6 months ahead if you want a chance of media coverage. That is a long lead, for a specific aim.",
        "Mark the major holidays and shopping dates, and check your carrier's holiday deadlines ([Prepare Your Shop for Holiday Success](https://www.etsy.com/seller-handbook/article/30953650830)).",
        "Set clear expectations on shipping, turnaround times and returns.",
        "Adjust processing times in your shipping profiles, so buyers see realistic dates.",
        "Share your cutoff dates in your shop announcement, shop policies and item descriptions.",
      ] },
      { p: "Etsy's own sale events change each year, so check its [Seller Holiday Checklist](https://www.etsy.com/seller-handbook/article/1401252562091) for this year's dates." },
      { h2: "Plan backwards" },
      { p: "Our suggestion, from the holiday itself:" },
      { ol: [
        "Find your production partner's last order date for that holiday, and add shipping time.",
        "Set your own last-order date a few days before that, and publish it.",
        "Set your \"live by\" date, leaving shoppers time to find the listing.",
        "Design and make mockups a few weeks before that.",
      ] },
      { h2: "Three things specific to print-on-demand" },
      { ul: [
        "Your production partner's capacity is not yours to control. Peak-season printing and shipping can be slower, so ask rather than assume.",
        "A holiday word in the title is fine when the holiday is essential to the item, like a Christmas jumper. Otherwise Etsy's [title guidance](/guides/etsy-title-rules-2026) says to leave it out and use tags and the Occasion and Holiday fields.",
        "Decide what happens after the day. A Valentine's design in March is stale: deactivate it or plan to renew it next year.",
      ] },
    ],
  },
  {
    slug: "running-more-than-one-etsy-shop",
    title: "Running more than one Etsy shop: what Etsy allows | Listyro",
    description: "Etsy allows more than one shop, with conditions: a separate account for each, every shop named on your profile, and no duplicate shops. What that means in practice.",
    h1: "Running more than one Etsy shop: what Etsy allows",
    lead: "Etsy allows it, with conditions, and the one that matters most is not creating duplicate shops. Here is what Etsy says, and where its wording leaves you to judge.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/multi-shop-publishing", label: "Multi-shop publishing", line: "Listyro lets each of your shops keep its own profile, SKU and text. Whether a shop meets Etsy's rules remains your call." },
    sources: [S.secondShop, S.sellerPolicy, S.ipQuestions],
    body: [
      { h2: "Yes, you may" },
      { p: "Etsy's help article [How to Open a Second Shop on Etsy](https://help.etsy.com/hc/en-us/articles/360017604474-How-to-Open-a-Second-Shop-on-Etsy) welcomes sellers running several shops. It sets conditions:" },
      { ul: [
        "Each shop needs its own Etsy account and email address.",
        "You must list all the shops you run in the Public Profile section of each account.",
      ] },
      { h2: "What you must not do" },
      { p: "Etsy's [Seller Policy](https://www.etsy.com/legal/sellers/) says sellers must not create duplicate shops, or take other actions, for the purpose of shilling, manipulating search or getting around Etsy's policies." },
      { h2: "What counts as a duplicate?" },
      { p: "We did not find a test in Etsy's current policy that says exactly where the line is. An older Etsy community announcement described the rule as covering shops that sell the same types of items, while distinctly different businesses remain allowed. That announcement is old and is not the policy text, so treat it as a hint and not a rule." },
      { p: "If you are unsure about your own case, ask Etsy before you rely on it. We cannot tell you a shop is safe." },
      { h2: "Sensible ways to run them" },
      { p: "This is our own reading, not Etsy's words:" },
      { ul: [
        "Give each shop a real reason to exist: its own range, audience or brand.",
        "Be careful sending one design to several shops. Copies of the same listings across shops are what a duplicate shop looks like. Write each shop's text for that shop, at the least.",
        "Never use a shop to get round a problem in another. Do not relist in shop B something that was removed from shop A.",
      ] },
      { h2: "A problem in one shop can reach the others" },
      { p: "Etsy's [5 Common Questions About Intellectual Property](https://www.etsy.com/seller-handbook/article/22451909389) says that when it closes a shop for repeated infringement, it also closes all other shops the seller operates, and that opening new shops under new accounts afterwards costs all account privileges. Read our guide to [trademarks on Etsy](/guides/trademarks-and-ip-on-etsy) before you add a shop." },
    ],
  },
  {
    slug: "mockups-buyers-trust",
    title: "Etsy mockups buyers trust: what Etsy's photo rules say | Listyro",
    description: "What Etsy's image rules say about mockups for print-on-demand listings: at least one real photo, an accurate first image, authorised stock photos, and how to stay accurate.",
    h1: "Mockups buyers trust",
    lead: "A mockup is a promise about what will arrive. Here is what Etsy's rules say about them, where its pages differ, and how to keep yours honest.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/mockup-generator", label: "the mockup generator", line: "Listyro places your designs on your own garment photos by geometry and shading. Your design is never redrawn." },
    sources: [S.imageHelp, S.imageRules, S.bestPractices, S.sellerPolicy, S.partners],
    body: [
      { h2: "What Etsy's rules say" },
      { p: "Etsy's [help article on image requirements](https://help.etsy.com/hc/en-us/articles/44297885802647-How-Do-Etsy-s-Listing-Image-Requirements-Apply-to-My-Listings) says:" },
      { ul: [
        "Most items need at least one unedited photo. Other images may be edited, including with AI, as long as they accurately represent what the buyer will receive.",
        "The first photo should be the clearest, most realistic picture of the item buyers will receive.",
        "If a production partner prints your original design on a standard base product such as a T-shirt or mug, you may use a stock photo mockup, if the stock photo's owner has authorised its use.",
        "For a personalized item, the first image must show a finished, customized item, not a blank product or placeholder text.",
      ] },
      { h2: "Where Etsy's pages differ" },
      { p: "Etsy's pages do not all use the same words. The Handbook's [4 Policy Best Practices](https://www.etsy.com/seller-handbook/article/1075628311049) recommends that the first image show the physical item the buyer will receive, not a rendering or stock photo, and says additional images may contain mockups from production partners. The [Seller Policy](https://www.etsy.com/legal/sellers/) speaks of your own photographs, not stock photos or artistic renderings." },
      { note: "Our advice: take the stricter reading. Order a sample of your best seller, photograph it, and use real photos where you can. If your whole listing is mockups, read [Etsy's Listing Image Requirements](https://www.etsy.com/legal/policy/listing-image-requirements/253962679005) for your case first." },
      { h2: "Making a mockup honest" },
      { p: "This part is our own advice:" },
      { ul: [
        "Show the real garment. Use a photo of the blank you actually sell, or one your production partner supplies for it.",
        "Show the real size. A design that fills the chest in the picture and prints smaller is a return waiting to happen.",
        "Show the real colour. Print a test and compare. Keep your photos in their original colour profile, so the shade on screen stays true.",
        "Show only what you offer. If a colour is not in your variations, do not show it.",
        "Have the right to use the photo. For a bought or downloaded mockup, check the licence covers use in product listings.",
      ] },
      { h2: "Things that make a mockup look false" },
      { ul: [
        "A design floating flat over folds, with no shading.",
        "A print running over a collar, seam or pocket.",
        "A dark print that disappears on a dark shirt, or a light one on white.",
        "Hair or arms that should be in front of the print but are behind it.",
      ] },
      { h2: "Say who makes it" },
      { p: "Using mockups does not change what you must disclose. Etsy requires sellers using a production partner to [say so on the listing](https://help.etsy.com/hc/en-us/articles/360000336547-Working-with-Production-Partners-on-Etsy)." },
    ],
  },
  {
    slug: "trademarks-and-ip-on-etsy",
    title: "Trademarks on Etsy: a guide for print-on-demand sellers | Listyro",
    description: "What Etsy removes for intellectual property reasons and why, what happens after a report, trademark versus copyright, and how print-on-demand sellers can stay safe.",
    h1: "Trademarks and intellectual property on Etsy, for POD sellers",
    lead: "Print-on-demand makes it very easy to sell something you have no right to sell. Here is what Etsy removes, what happens when it does, and how to avoid it.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/ai-listing-writer#trademark-warning", label: "the trademark warning", line: "Listyro warns you before sending when a listing names a well-known brand or character. You decide." },
    sources: [S.ipPolicy, S.ipQuestions, S.ipIssues, S.counter],
    body: [
      { note: "This is general information from Etsy's published pages, not legal advice. If you have received a notice or are unsure about a design, speak to a lawyer." },
      { h2: "Two different rights" },
      { ul: [
        "A trademark protects a name or logo that identifies a business: a brand, a team, a product name. It can apply to words in your title and tags.",
        "Copyright protects a creative work: a character drawing, an illustration, a film still, lyrics. It can apply to your design even if your text names nothing.",
      ] },
      { p: "So a clean title does not make a design safe, and rewriting a brand name in a clever way does not make a title safe." },
      { h2: "What usually gets print-on-demand sellers into trouble" },
      { p: "This is our own list, not Etsy's:" },
      { ul: [
        "Cartoon and film characters, including fan art.",
        "Sports team names and logos, and league names.",
        "Fashion and sportswear brand names and logos.",
        "Theme parks and their characters.",
        "A brand name used only as a description, like \"Disney-style\" or \"Nike-inspired\".",
        "A name misspelt or joined up to avoid being noticed.",
      ] },
      { h2: "What happens when a listing is reported" },
      { p: "From Etsy's [Intellectual Property Policy](https://www.etsy.com/legal/ip/) and its [guidance for sellers who receive a report](https://www.etsy.com/legal/policy/copyright-and-intellectual-property/242644375651):" },
      { ol: [
        "An owner, or someone acting for them, reports your listing to Etsy.",
        "If the report meets Etsy's policy, Etsy deactivates the listing and emails you, with the reporter's contact details.",
        "Etsy says questions about why your content was reported should go directly to the reporter. Etsy cannot speak for them.",
        "Do not relist the item unless the matter is resolved with them.",
      ] },
      { h2: "Can you dispute it?" },
      { ul: [
        "For a US copyright report, Etsy accepts a [counter notice](https://www.etsy.com/legal/ip-counter/) through the link in its email. It is a sworn statement. If you misrepresent that material is not infringing, Etsy warns you may be liable for damages, including costs and legal fees. If the reporter does not tell Etsy of court action within 10 business days of Etsy receiving it, the listing may be reactivated.",
        "For a trademark report, Etsy's [Handbook](https://www.etsy.com/seller-handbook/article/22451909389) says your best option, if you believe it was a mistake, is to contact the party who reported it.",
      ] },
      { h2: "Repeat notices can cost you the shop" },
      { p: "Etsy's policy says it ends the selling privileges of members who receive repeat or multiple notices, in appropriate circumstances and at its discretion. Its Handbook says that closing a shop for repeated infringement also closes the seller's other shops. We found no published number of notices, and we would not trust one that is passed around in forums." },
      { h2: "How to stay safe" },
      { p: "Our own advice:" },
      { ul: [
        "Sell your own original designs. If you did not make it or license it, do not sell it.",
        "Search the name you want to use in a trademark register, such as the USPTO's, before building a range on it. A phrase can be registered too.",
        "Check bought design bundles: a licence from a seller does not help if that seller had no rights.",
        "Check your tags and description as well as the title.",
        "Keep your design files and licences, with dates.",
        "Treat a notice as serious, and do not relist.",
      ] },
      { h2: "How Listyro helps, and where it stops" },
      { p: "Before you send anything, Listyro tells you when a listing names a [well-known brand or character](/features/ai-listing-writer#trademark-warning), including joined-up and misspelt forms. It flags artwork that looks like a known character. You decide what to do." },
      { p: "It checks against a list we keep. It cannot tell you a design is safe, it does not search trademark registers, and a listing with no warning can still be reported." },
    ],
  },
];

export const GUIDES_PAGE = {
  title: "Guides for Etsy print-on-demand sellers | Listyro",
  description: "Plain-English guides for print-on-demand sellers on Etsy: title and tag rules, listing in bulk, holiday planning, several shops, mockups and trademarks. Dated, with sources.",
  h1: "Guides for Etsy sellers",
  lead: "What Etsy's published rules say, in plain English, with links to Etsy's own pages. Each guide says when it was last checked, and which parts are our own advice.",
};

/** All the guides' text, for the checks. */
export const guideText = (guide: Guide) => allText([guide.title, guide.description, guide.h1, guide.lead, guide.body, guide.feature.line]);
