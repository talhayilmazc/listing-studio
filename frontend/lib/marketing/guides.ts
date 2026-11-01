/**
 * The guides. Original writing in plain English. A claim about Etsy's rules links, in the same block
 * or the paragraph that leads into a list, to a page that states it; READ is the list of such pages
 * and docs/guide-sources.md holds the words each claim rests on. Anything else is labelled as our own
 * advice. A page we could not read goes in FOR_THE_SELLER_TO_READ and supports no claim. No promise of
 * ranking or sales (lib/marketingSite.test.ts). Etsy changes its rules, so each guide carries the day
 * it was checked.
 */
import { allText, type Block, type Source } from "./content.ts";
import { READ } from "./sources.ts";

export { READ };

export type Guide = {
  slug: string;
  title: string;
  description: string;
  h1: string;
  lead: string;
  published: string;
  checked: string;
  feature: { href: string; label: string; line: string };
  /** Pages that state what this guide says they state. */
  sources: Source[];
  /** Etsy's own pages on the subject. The guide relies on them for nothing. */
  alsoRead: Source[];
  body: Block[];
};

const HANDBOOK = "https://www.etsy.com/seller-handbook/article";
export const FOR_THE_SELLER_TO_READ = {
  titles: { label: "Etsy Seller Handbook: New Guidance for Listing Titles", url: `${HANDBOOK}/1399426136697` },
  anatomy: { label: "Etsy Seller Handbook: The Anatomy of a Well-Crafted Etsy Listing", url: `${HANDBOOK}/1347574487014` },
  howSearchWorks: { label: "Etsy Seller Handbook: How Etsy Search Works", url: `${HANDBOOK}/375461474487` },
  keywords: { label: "Etsy Seller Handbook: Keywords 101", url: `${HANDBOOK}/382774281517` },
  bestPractices: { label: "Etsy Seller Handbook: 4 Policy Best Practices When Listing Items on Etsy", url: `${HANDBOOK}/1075628311049` },
  ipQuestions: { label: "Etsy Seller Handbook: 5 Common Questions About Intellectual Property on Etsy", url: `${HANDBOOK}/22451909389` },
  holidayChecklist: { label: "Etsy Seller Handbook: Seller Holiday Checklist", url: `${HANDBOOK}/1401252562091` },
  holidayPrepare: { label: "Etsy Seller Handbook: Prepare Your Shop for Holiday Success", url: `${HANDBOOK}/30953650830` },
  creativity: { label: "Etsy: Creativity Standards", url: "https://www.etsy.com/legal/creativity" },
  imageRules: { label: "Etsy: Listing Image Requirements", url: "https://www.etsy.com/legal/policy/listing-image-requirements/253962679005" },
  ipPolicy: { label: "Etsy: Intellectual Property Policy", url: "https://www.etsy.com/legal/ip/" },
  ipIssues: { label: "Etsy: Copyright and Intellectual Property Policy Issues", url: "https://www.etsy.com/legal/policy/copyright-and-intellectual-property/242644375651" },
} satisfies Record<string, Source>;

const S = READ;
const ALSO = FOR_THE_SELLER_TO_READ;
const L = (source: Source, words: string) => `[${words}](${source.url})`;

const CHECKED = "2026-11-01";

/*
 * The calendar's dates, split on the day the guide was checked. "Live by" is our own rule of thumb,
 * worked out here and not typed. When the guide is checked again, CHECKED moves and so does the split.
 */
const LIVE_WEEKS = 8;
const EVENTS: [string, string][] = [
  ["Halloween", "2026-10-31"],
  ["Thanksgiving (US)", "2026-11-26"],
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
const liveByDate = (iso: string) => new Date(Date.parse(`${iso}T00:00:00Z`) - LIVE_WEEKS * 7 * 86_400_000);
export function liveBy(iso: string): string {
  return short(liveByDate(iso));
}
const dayOf = (iso: string) => short(new Date(`${iso}T00:00:00Z`));
const liveByHasPassed = (iso: string) => liveByDate(iso).toISOString().slice(0, 10) < CHECKED;
export const CALENDAR_DATES = EVENTS;
export const CALENDAR_AS_OF = CHECKED;
const UPCOMING = EVENTS.filter(([, iso]) => iso >= CHECKED);
const CALENDAR_ROWS = UPCOMING.map(([name, iso]) => [name, dayOf(iso), liveByHasPassed(iso) ? "Passed: list now" : liveBy(iso)]);
const passedDays = EVENTS.filter(([, iso]) => iso < CHECKED).map(([name, iso]) => `${name} (${dayOf(iso)})`);
const passedMarks = UPCOMING.filter(([, iso]) => liveByHasPassed(iso)).map(([name, iso]) => `${name} (${liveBy(iso)})`);
const PASSED_NOTE = `This season, already passed: ${passedDays.join(", ")}, and the ${LIVE_WEEKS}-week marks for ${passedMarks.join(", ").replace(/, ([^,]*)$/, " and $1")}.`;

export const GUIDES: Guide[] = [
  {
    slug: "etsy-title-rules-2026",
    title: "Listing title rules for 2026: short, clear titles | Listyro",
    description: "What Etsy's help pages say about listing titles: 140 characters, fewer than 15 words, and which characters are refused. Then our own advice on writing a short title.",
    h1: "Listing title rules for 2026",
    lead: "For sellers on Etsy: what is a hard limit, what Etsy's help suggests, and our own advice on rewriting a keyword-stuffed title.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/ai-listing-writer", label: "the AI listing writer", line: "Listyro writes titles in this short style by default and checks them against these rules." },
    sources: [S.createListing, S.api, S.tags, S.attributes, S.search],
    alsoRead: [ALSO.titles, ALSO.anatomy, ALSO.howSearchWorks],
    body: [
      { note: "Want to check one now? Paste it into our free [title checker](/free-tools/title-checker)." },
      { h2: "What Etsy's help says" },
      { p: `${L(S.createListing, "How to Create a Listing")} says: "A listing's title can be up to 140 characters long. Consider using less than 15 words when including the traits that make your item special."` },
      { h2: "Which characters Etsy refuses" },
      { p: `Etsy's ${L(S.api, "API reference for creating a draft")} says valid titles contain only letters, numbers, punctuation marks, mathematical symbols, whitespace, ™, © and ®, and: "You can only use the %, :, & and + characters once each."` },
      { h2: "Our own advice" },
      { p: "This part is ours, not Etsy's wording. Etsy publishes title guidance in its Seller Handbook, linked at the end of this page. Where that differs from us, follow Etsy." },
      { ul: [
        "Start with what the item is, followed by its key traits: colour, material, style.",
        "Leave out subjective words such as \"perfect\" or \"beautiful\".",
        "Leave out gift phrases such as \"gift for her\", and put them in tags.",
        "Mention a holiday, occasion or recipient only when it is essential to what the item is.",
        "Do not repeat words.",
        "Leave out price, shipping and sale information.",
      ] },
      { h2: "A worked example" },
      { table: {
        caption: "One made-up listing, before and after",
        head: ["", "Title", "Why"],
        rows: [
          ["Before", "Mountain Shirt, Hiking Shirt, Perfect Gift for Him, Camping Tee, Mountain Lover Gift, Outdoors Shirt", "15 words, repeats \"mountain\" and \"shirt\", and uses a subjective word and gift phrases"],
          ["After", "Retro Mountain Graphic T-Shirt, Cotton Crewneck in Forest Green", "9 words: the item first, then material and colour"],
        ],
      } },
      { p: `"Hiking shirt", "camping tee" and "gift for him" are not lost. They move to tags. ${L(S.tags, "How to Use Tags")} says "Etsy matches tags with shoppers' searches to find relevant results", and that tags are "great for aspirational or buyer-minded search terms (for example, \"gift for her\")".` },
      { h2: "A recipient that defines the item" },
      { p: "Our view: \"Nurse Shirt\" is fine if the design is about nursing, because that is what the item is. \"Shirt for Nurses, Gift for Her\" is a gift phrase, and we would move it to tags." },
      { h2: "A title is not the only input" },
      { p: `${L(S.search, "How to Search for Items and Shops")} says "Search results are ordered by how relevant the items are to what you search for." ${L(S.attributes, "How to Use Attributes")} says "Attributes can also help buyers find your items more easily in search results." So tags and attributes count as well as the title.` },
      { note: "None of this is a promise about ranking. Nobody outside Etsy can make one." },
    ],
  },
  {
    slug: "list-print-on-demand-shirts-in-bulk",
    title: "How to list print-on-demand shirts in bulk | Listyro",
    description: "A step-by-step way to list many print-on-demand shirts on Etsy: one good model listing, SKU file names, mockups, one review per listing, and a schedule.",
    h1: "Listing print-on-demand shirts in bulk",
    lead: "Adding many designs to an Etsy shop is mostly the same work repeated. Here is a way to do that work once, while still looking at each listing before it goes live.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/profiles", label: "Profiles", line: "Listyro's profiles do step 1 for you: new drafts copy the settings of a listing you already sell." },
    sources: [S.partners, S.createListing, S.imageHelp],
    alsoRead: [ALSO.creativity, ALSO.anatomy],
    body: [
      { p: "This guide works with any tools, including a spreadsheet and Etsy's own Shop Manager. Where Listyro does a step for you, we say so." },
      { h2: "1. Build one listing properly" },
      { p: "Everything else copies from this one, so a mistake here is repeated in all of them. Get it right first: category, price, variations and sizes, shipping profile, return policy, processing time, materials, size chart." },
      { h2: "2. Say honestly who makes it" },
      { p: `${L(S.partners, "Working with Production Partners")} says: "We require sellers using production assistance to transparently share this information on the applicable listings." You add a partner in the listing's details. Set this on your model listing, so every copy carries it.` },
      { p: `The same page says "Production partners only apply to items that you have designed, or that you have sourced and the buyer has customized." Etsy's Creativity Standards, linked at the end, are the rules on how to describe what you sell. Check which applies to you.` },
      { h2: "3. Name your files by SKU" },
      { p: "Give each design a SKU and put it in the file names: BR5229-front.png, BR5229-back.png. Or use one folder per design. Then any tool, or you, can group the photos into listings without guessing." },
      { h2: "4. Make the mockups once" },
      { p: `Choose a small set of photos and use them for every design, so your shop looks consistent. ${L(S.createListing, "How to Create a Listing")} says "You can add up to 20 photos and 2 videos" to each listing. Read [mockups buyers trust](/guides/mockups-buyers-trust) first: ${L(S.imageHelp, "Etsy's help")} also says "Etsy requires at least one unedited photo for most items."` },
      { h2: "5. Write each listing for its design" },
      { p: "The settings repeat; the words should not. Each title should say what this design is, and each set of tags should fit it. See our [title rules](/guides/etsy-title-rules-2026) and [tag rules](/guides/etsy-tags-13-tags-20-characters). Check for [brand and character names](/guides/trademarks-and-ip-on-etsy) now, while it is cheap to change them." },
      { h2: "6. Look at each one" },
      { p: "Speed comes from the repeated work, not from skipping this. Read each title against its picture. One wrong size chart or one brand name left in a title across a large batch costs far more than the minutes saved." },
      { h2: "7. Spread them out" },
      { p: "Create drafts first, then publish over days rather than all at once. It gives you time to spot a mistake while it is still in only a few listings." },
      { h2: "8. Keep a record" },
      { p: "A SKU on every listing ties an order back to its design file and to the print file your production partner needs." },
      { note: "Listing faster does not change what a listing has to be, and we cannot tell you how any of it will sell." },
    ],
  },
  {
    slug: "etsy-tags-13-tags-20-characters",
    title: "13 tags, 20 characters: what to put in listing tags | Listyro",
    description: "Etsy gives you 13 tags of up to 20 characters. What Etsy's help says about them, which characters are refused, and our own advice on what to put in them.",
    h1: "Listing tags: 13 tags and 20 characters",
    lead: "Thirteen short phrases that tell Etsy's search what else your listing is. Here is what Etsy's help says, and what we would do with them.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/ai-listing-writer", label: "the AI listing writer", line: "Listyro writes 13 tags to these rules and shows each tag's length as you edit." },
    sources: [S.tags, S.api, S.attributes],
    alsoRead: [ALSO.keywords, ALSO.anatomy],
    body: [
      { note: "Want to check yours now? Paste them into our free [tag checker](/free-tools/tag-checker)." },
      { h2: "What Etsy's help says" },
      { p: `From ${L(S.tags, "How to Use Tags to Get Found in Search")}:` },
      { ul: [
        "\"You can use up to 13 tags per listing.\"",
        "\"Each tag can contain up to 20 characters.\"",
        "A tag can include spaces, letters and numbers. You can use ' and - within words and phrases, but a tag cannot start with them.",
        "\"A single tag can be a short descriptive phrase\".",
        "\"To increase your search visibility, use a diverse array of tags.\"",
        "Tags \"are helpful when there's no matching attribute and are great for aspirational or buyer-minded search terms (for example, \"gift for her\")\".",
      ] },
      { p: `${L(S.attributes, "How to Use Attributes")} says: "Fill out both attributes and tags for your listings".` },
      { h2: "Which characters Etsy refuses" },
      { p: `Etsy's ${L(S.api, "API reference for creating a draft")} says valid tags contain only letters, numbers, whitespace, -, ', ™, © and ®. Its pattern names the straight apostrophe and the hyphen. So a curly apostrophe (’) or a long dash (–), which text pasted from a word processor can contain, is not valid.` },
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
      { h2: "Mistakes we would avoid" },
      { p: "Also our own advice:" },
      { ul: [
        "Using fewer than 13 when you have more to say.",
        "Thirteen variations of one phrase. \"mountain shirt\" and \"shirts mountain\" add little.",
        "Repeating your category or an attribute you already filled in. Spend the tag on something new.",
        "Tags that only repeat the title. Keep those for when you have run out of different phrases.",
        "Brand or character names: see [trademarks](/guides/trademarks-and-ip-on-etsy).",
      ] },
      { note: "Good tags describe your listing. Nobody outside Etsy can tell you how a listing will rank, and we do not try." },
    ],
  },
  {
    slug: "holiday-listing-calendar-pod",
    title: "A holiday listing calendar for print-on-demand sellers | Listyro",
    description: "The main selling dates from November 2026 through 2027, with a planning rule for when to have print-on-demand listings live on Etsy, and how to think about shipping cutoffs.",
    h1: "A holiday listing calendar for POD sellers",
    lead: "The dates ahead, from November 2026 through 2027, a simple way to plan backwards, and the points people forget.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/scheduling", label: "Scheduling", line: "Listyro lets you approve listings now and choose the day each goes live, shop by shop." },
    sources: [S.processing, S.search],
    alsoRead: [ALSO.holidayChecklist, ALSO.holidayPrepare],
    body: [
      { h2: "The dates ahead" },
      { table: { caption: `Main selling dates from ${dayOf(UPCOMING[0][1])}, mostly US`, head: ["Event", "Date", "Live by"], rows: CALENDAR_ROWS } },
      { p: `"Live by" is our own rule of thumb: ${LIVE_WEEKS} weeks before the day. It is not an Etsy rule, and it does not promise sales. Move it earlier for gifts that take longer to arrive.` },
      { note: PASSED_NOTE },
      { p: "If a \"live by\" date has passed, it is usually still worth listing. Just check that orders can still arrive in time." },
      { h2: "What Etsy's help says about timing" },
      { p: `From ${L(S.processing, "How to Set Processing Times")}:` },
      { ul: [
        "\"Processing times help set expectations with buyers, and help you reliably ship your orders.\"",
        "\"You can make changes to your processing schedule at any time.\"",
        "\"If you edit a processing profile, all of the listings and variations associated with it will update as well.\"",
        "In the US and Canada you can choose which holidays you process orders on. A holiday you remove \"won't be counted as a processing day\".",
      ] },
      { p: `${L(S.search, "How to Search for Items and Shops")} lists Holiday among the filters shoppers can use. Etsy's sale events are its own to announce: its Seller Handbook articles are linked at the end.` },
      { h2: "Plan backwards" },
      { p: "Our suggestion, from the holiday itself:" },
      { ol: [
        "Find your production partner's last order date for that holiday, and add shipping time.",
        "Set your own last-order date a few days before that, and publish it in your shop announcement and your item descriptions.",
        "Set your \"live by\" date, leaving shoppers time to find the listing.",
        "Design and make mockups a few weeks before that.",
      ] },
      { h2: "Three things specific to print-on-demand" },
      { p: "Our own advice again:" },
      { ul: [
        "Your production partner's capacity is not yours to control. Peak-season printing and shipping can be slower, so ask rather than assume.",
        "A holiday word in the title is fine when the holiday is essential to the item, like a Christmas jumper. Otherwise we would use tags and, where your category offers them, the Occasion and Holiday fields. See our [title rules](/guides/etsy-title-rules-2026).",
        "Decide what happens after the day. A Valentine's design in March is stale: deactivate it or plan to renew it next year.",
      ] },
    ],
  },
  {
    slug: "running-more-than-one-etsy-shop",
    title: "Running more than one shop: what the rules say | Listyro",
    description: "Etsy allows more than one shop, with conditions. What its help page and Seller Policy say about duplicate shops, what that wording does not say, and our own advice.",
    h1: "Running more than one shop: what the rules say",
    lead: "Etsy welcomes sellers running several shops, and its Seller Policy prohibits duplicate shops for certain purposes. Here is what each page says, what it does not say, and what we would do.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/multi-shop-publishing", label: "Multi-shop publishing", line: "Listyro lets each of your shops keep its own profile, SKU and text. Whether a shop meets Etsy's rules remains your call." },
    sources: [S.secondShop, S.sellerPolicy, S.ipNotice],
    alsoRead: [ALSO.ipQuestions],
    body: [
      { h2: "You may run several shops" },
      { p: `Etsy's ${L(S.secondShop, "How to Open a Second Shop")} says: "You're welcome to run multiple shops at once on Etsy." It sets conditions:` },
      { ul: [
        "\"A separate Etsy account for each shop\" and \"A separate email address on file with each account.\"",
        "\"If you choose to run multiple shops on Etsy, you must list all the shops you run in the Public Profile section of each account.\"",
        "\"Each account must also comply with Etsy's Seller Policy.\"",
      ] },
      { h2: "What the Seller Policy prohibits" },
      { p: `Etsy's ${L(S.sellerPolicy, "Seller Policy")}, section 1c item 7 (page last updated 5 October 2026), prohibits creating duplicate shops or other actions "for the purpose of shilling, manipulating search or circumventing Etsy's policies".` },
      { h2: "What that wording does not say" },
      { p: "We are reading that one item, and this is our reading of it:" },
      { ul: [
        "It does not define a duplicate shop, or give a test for one.",
        "It does not set a number of shops.",
        "It does not say whether one design may appear in two shops.",
        "It does not say a shop is fine because you meant no harm. How the policy applies is Etsy's decision, not ours.",
      ] },
      { p: "If you are unsure about your own case, ask Etsy before you rely on it. We cannot tell you a shop is safe." },
      { h2: "What we would do" },
      { p: "This is our own advice, not Etsy's words:" },
      { ul: [
        "Give each shop its own text. In Listyro a listing can carry [its own title, tags and description for each shop](/features/multi-shop-publishing#per-shop-text).",
        "Do not use several shops to push the same item in search. That is the purpose the policy names.",
        "Give each shop a real reason to exist: its own range, audience or brand.",
        "Never use a shop to get round a problem in another. Do not relist in shop B something that was removed from shop A.",
      ] },
      { h2: "A problem with intellectual property" },
      { p: `Etsy's ${L(S.ipNotice, "page on infringement notices")} says "Repeat reports of infringement may result in the loss of account privileges." Read our guide to [trademarks](/guides/trademarks-and-ip-on-etsy) before you add a shop.` },
    ],
  },
  {
    slug: "mockups-buyers-trust",
    title: "Mockups buyers trust: what the photo rules say | Listyro",
    description: "What Etsy's help says about mockups for print-on-demand listings: an unedited photo, an accurate first image, authorised stock mockups, and how to stay accurate.",
    h1: "Mockups buyers trust",
    lead: "A mockup is a promise about what will arrive. Here is what Etsy's help says about images, what it leaves open, and how to keep yours honest.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/mockup-generator", label: "the mockup generator", line: "Listyro places your designs on your own garment photos by geometry and shading. Your design is never redrawn." },
    sources: [S.imageHelp, S.images, S.personalized, S.partners],
    alsoRead: [ALSO.imageRules, ALSO.bestPractices, S.sellerPolicy],
    body: [
      { h2: "What Etsy's help says" },
      { p: `From ${L(S.imageHelp, "How Do Etsy's Listing Image Requirements Apply to My Listings?")}:` },
      { ul: [
        "\"Etsy requires at least one unedited photo for most items.\"",
        "\"An unedited photo should show the actual item against a real background and must not be edited or changed in any way.\"",
        "\"Some exceptions may apply depending on the types of items you sell.\"",
        "\"For other images, you can use photo editing tools, including AI, as long as your images accurately represent what buyers will receive.\"",
        "\"Make your first listing photo the clearest, most realistic representation of the item buyers will receive.\"",
        "\"If a production partner prints your original design on a standard base product (like a t-shirt or mug), you may use a stock photo mockup, if the stock photo's owner has authorized its use.\"",
      ] },
      { p: `${L(S.images, "Requirements and Best Practices for Images")} adds: "If you use AI to edit any of your listing photos, note whether images were edited in your listing description."` },
      { p: `For personalized items, ${L(S.personalized, "How to Offer Personalized Listings")} says: "For personalized commercial items (such as engraved or monogrammed products), your primary image must show a finished, personalized item similar to the final product. Do not use blank product images or mockups with placeholder text (ex. \"Your Text Here\") as the main image."` },
      { h2: "What those pages leave open" },
      { p: "The pages above do not say whether a stock photo mockup can stand in for the unedited photo, or whether a mockup may be the first image. Etsy's other pages on photos are linked at the end." },
      { note: "Our advice: take the stricter reading. Order a sample of your best seller, photograph it, and use real photos where you can." },
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
      { p: `Using mockups does not change what you must share. ${L(S.partners, "Working with Production Partners")} says: "We require sellers using production assistance to transparently share this information on the applicable listings."` },
    ],
  },
  {
    slug: "trademarks-and-ip-on-etsy",
    title: "Trademarks for print-on-demand sellers: a plain guide | Listyro",
    description: "What happens to an Etsy listing after an intellectual property report, trademark versus copyright, counter notices, and how print-on-demand sellers can stay out of trouble.",
    h1: "Trademarks and intellectual property, for POD sellers",
    lead: "Print-on-demand makes it very easy to sell something you have no right to sell. Here is what Etsy's help says happens when a listing is reported, and how to avoid it.",
    published: "2026-11-01",
    checked: CHECKED,
    feature: { href: "/features/ai-listing-writer#trademark-warning", label: "the trademark warning", line: "Listyro warns you before sending when a listing names a well-known brand or character. You decide." },
    sources: [S.ipNotice, S.ipReport, S.counter, S.removed],
    alsoRead: [ALSO.ipPolicy, ALSO.ipQuestions, ALSO.ipIssues],
    body: [
      { note: "This is general information, not legal advice. If you have received a notice or are unsure about a design, speak to a lawyer." },
      { h2: "Two different rights" },
      { p: "In general terms, and in our own words:" },
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
      { p: `From ${L(S.ipReport, "How to Report Intellectual Property Infringement")} and ${L(S.ipNotice, "What to Do if You Receive a Notice")}:` },
      { ul: [
        "\"if the report complies with our policies, we will remove the reported content and contact the affected members.\"",
        "\"If any of your listings are removed due to an infringement claim, you'll receive an email with the contact information of the party who filed it.\"",
        "\"We encourage you to directly contact the party that provided the report to Etsy if you have questions about the infringement claim\".",
        "\"Etsy does not make decisions about which listings infringe with intellectual property, and can not speak on behalf of the intellectual property owner.\"",
        "\"Do not create additional listings for this content unless the issue is resolved with the other party.\"",
      ] },
      { h2: "Can you dispute it?" },
      { p: `From ${L(S.counter, "How to File a DMCA Counter Notice")}:` },
      { ul: [
        "\"Counter notices can be filed against reports of copyright infringement only.\" The same page says \"Etsy cannot accept counter notices for non-copyright claims.\"",
        "It includes \"a statement under penalty of perjury\" that the material was removed as the result of a mistake or misidentification, and it is shared with the party who reported you.",
        "Unless that party tells Etsy of legal action seeking a court order, \"you may relist the removed material 10 business days after Etsy processes your counter notice.\"",
      ] },
      { p: `${L(S.removed, "Why Was My Listing Removed?")} also says: "If your listing was removed and you believe this is a mistake, you may be able to appeal to restore it from the Policy violations page."` },
      { h2: "Repeat reports" },
      { p: `${L(S.ipNotice, "What to Do if You Receive a Notice")} says: "Repeat reports of infringement may result in the loss of account privileges." The pages we read give no number of reports, and we would not trust one that is passed around in forums.` },
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
  title: "Guides for print-on-demand sellers | Listyro",
  description: "Plain-English guides for print-on-demand sellers on Etsy: title and tag rules, listing in bulk, holiday planning, several shops, mockups and trademarks. Dated, with sources.",
  h1: "Guides for print-on-demand sellers",
  lead: "What Etsy's help pages say, in plain English, with links to the pages themselves. Each guide says when it was last checked, and which parts are our own advice.",
};

/** All the guides' text, for the checks. */
export const guideText = (guide: Guide) => allText([guide.title, guide.description, guide.h1, guide.lead, guide.body, guide.feature.line]);
