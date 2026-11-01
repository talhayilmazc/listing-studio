/**
 * The free tools' words: one place, read by their pages, the Free tools page, the registry and the tests.
 * The rule for "Etsy" is the site's (lib/marketing/brandRule.ts): never in a title, a share title, a
 * JSON-LD name or an h1; in a description or the text it only says who the tool is for.
 */
import { IDEAS_SLUG } from "./routes.mjs";

export type Tool = {
  slug: string;
  name: string;
  title: string;
  description: string;
  h1: string;
  lead: string;
  /** On the Free tools page. */
  summary: string;
  /** What the page explains under the tool. */
  explain: string[];
  /** The related guide, and any other page worth reading. */
  guide: { href: string; label: string };
  also?: { href: string; label: string };
};

/** The one call to action, quiet on purpose. */
export const TOOL_CTA = { label: "Do this for 100 listings at once with Listyro", href: "/request-invite" };

export const TITLE_TOOL: Tool = {
  slug: "title-checker",
  name: "Title checker",
  title: "Free listing title checker: length, characters, words | Listyro",
  description: "Paste a title and check it against Etsy's stated limits and our own title advice, line by line, with the source of each rule. Runs in your browser, no account needed.",
  h1: "Listing title checker",
  lead: "Paste a title. Each line says whether it is Etsy's rule, quoted with its page, or our own advice. What you type stays in your browser.",
  summary: "Length, characters, word count and our title advice, line by line, with a brand-name warning.",
  explain: [
    "Etsy's rules here are quoted from its own pages, and the link on each line opens that page. A line marked \"Our advice\" is how we write titles in the Listyro app; it is not a rule of Etsy's, and you are free to disagree with it.",
    "The checker looks at the text only. It cannot tell how a listing will do in search, and it does not try to.",
  ],
  guide: { href: "/guides/etsy-title-rules-2026", label: "Read the title rules guide" },
  also: { href: "/guides/trademarks-and-ip-on-etsy", label: "Read the trademarks guide" },
};

export const TAG_TOOL: Tool = {
  slug: "tag-checker",
  name: "Tag checker",
  title: "Free listing tag checker: 13 tags, 20 characters | Listyro",
  description: "Paste your tags and check them against Etsy's limits and our own advice: count, length, allowed characters, duplicates and title words. Runs in your browser, no account needed.",
  h1: "Listing tag checker",
  lead: "Paste your tags, separated by commas or one per line. Each line says whether it is Etsy's rule, quoted with its page, or our own advice. What you type stays in your browser.",
  summary: "Count, length, allowed characters, duplicates, title words and phrases, with a brand-name warning.",
  explain: [
    "Etsy's limits here are quoted from its Help page and its API reference, and the link on each line opens the page. Lines marked \"Our advice\" are how we choose tags in the Listyro app, not rules of Etsy's.",
    "Add your title if you want tags that only repeat its words pointed out. Nothing you type is sent anywhere.",
  ],
  guide: { href: "/guides/etsy-tags-13-tags-20-characters", label: "Read the tags guide" },
  also: { href: "/guides/trademarks-and-ip-on-etsy", label: "Read the trademarks guide" },
};

export const FEE_TOOL: Tool = {
  slug: "fee-calculator",
  name: "Fee calculator",
  title: "Fee calculator for print-on-demand sellers | Listyro",
  description: "Work out what a print-on-demand sale on Etsy leaves after the listing, transaction, payment processing and Offsite Ads fees, your base cost and shipping.",
  h1: "Fee calculator for print-on-demand sellers",
  lead: "Enter your price and costs and see what one sale leaves: fees line by line, with profit and margin. Each fee shows its rate and the page it came from.",
  summary: "Listing, transaction, payment processing and Offsite Ads fees, your base cost and shipping: profit and margin for one sale.",
  explain: [
    "It works out one sale of one item. It runs in your browser, and nothing you enter is sent anywhere.",
    "For a month's real figures, read from your own shop rather than estimated, the Listyro app has a profit report.",
  ],
  guide: { href: "/features/profit-report", label: "See the profit report" },
  also: { href: "/guides/list-print-on-demand-shirts-in-bulk", label: "Read the print-on-demand listing guide" },
};

export const FEE_COMING_SOON = {
  status: "Coming soon",
  heading: "Coming soon",
  body: "We are checking every fee against Etsy's own pages before this goes up, because a wrong rate here would give you a wrong profit. It will cover the listing fee, the transaction fee, payment processing by country, the regulatory operating fee where one applies, Offsite Ads, shipping and your print-on-demand base cost.",
};

export const IDEAS_TOOL: Tool = {
  slug: IDEAS_SLUG,
  name: "Title and tag ideas",
  title: "Listing title and tag ideas from a description | Listyro",
  description: "Describe your item in a sentence and get title and tag ideas written by AI, checked against the same rules as our title and tag checkers. A few a day, no account needed.",
  h1: "Title and tag ideas",
  lead: "Describe your item in a sentence or two and get a few title and tag ideas to start from. They are checked against the rules of our title and tag checkers before you see them.",
  summary: "Describe your item and get a few title and tag ideas, written by AI and checked against the same rules.",
  explain: [
    "This one is not in your browser alone: what you type goes to our server and to Anthropic, the AI provider, to write the ideas. It is not kept. The Privacy Policy has the details.",
    "These are starting points. Read them against your own item before you use them: the AI has not seen your design.",
  ],
  guide: { href: "/guides/etsy-title-rules-2026", label: "Read the title rules guide" },
  also: { href: "/guides/etsy-tags-13-tags-20-characters", label: "Read the tags guide" },
};

/** The tools open to everybody and cached like any public page. */
export const TOOLS: Tool[] = [TITLE_TOOL, TAG_TOOL, FEE_TOOL];
