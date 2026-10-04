/**
 * "Import from Etsy": the words on the screen, in one place.
 *
 * Edit the steps here when Etsy moves a menu; nothing else needs to change.
 * Each card on the import screen shows its `steps` in order.
 */

export interface ImportCard {
  kind: "statement" | "ads";
  title: string;
  /** One sentence: what the file is and what it gives. */
  what: string;
  steps: string[];
  /** Shown under the steps. */
  note: string;
  button: string;
}

export const IMPORT_CARDS: ImportCard[] = [
  {
    kind: "statement",
    title: "Monthly statement",
    what: "Every sale, fee, tax, ad charge and refund Etsy posted in a month. It is the exact figure for the month's money.",
    steps: [
      "Open Shop Manager on Etsy.",
      "Go to Finances, then Monthly statements.",
      "Pick the month.",
      "Press Download CSV.",
      "Choose that file here.",
    ],
    note: "One file per month. Importing a month again replaces it.",
    button: "Choose the statement CSV",
  },
  {
    kind: "ads",
    title: "Etsy Ads report",
    what: "What your ads spent and brought in, day by day, for the whole shop.",
    steps: [
      "Open Shop Manager on Etsy.",
      "Go to Marketing, then Etsy Ads.",
      "Set the date range to the whole month.",
      "Download the report.",
      "Choose that file here.",
    ],
    note: "Etsy's report has no listing column, so ad spend is shown for the shop, not per listing.",
    button: "Choose the Ads report CSV",
  },
];

/** Under both cards. */
export const IMPORT_PRIVACY =
  "The files are read here and not kept. Only the totals and the per-order amounts needed to tie fees to listings are stored. Neither file contains buyer names or addresses, and none are stored.";

/** Tooltip on the two ads figures. */
export const ADS_LAG =
  "Etsy bills each day's clicks on the next day. So a month's statement includes the last day of the month before and leaves out its own last day, which is on the next statement.";

export const LABELS = {
  charged: "Charged by Etsy this month",
  spent: "Ad spend for clicks this month",
  refunds: "Refunds",
  revenue: "Revenue",
};

/** "95.28 refunded to buyers, of which 6.26 was sales tax that Etsy returned." */
export function refundsTooltip(refunded: string, taxReturned: string): string {
  return `${refunded} refunded to buyers, of which ${taxReturned} was sales tax that Etsy returned.`;
}

export const STATUS_WORDS: Record<string, string> = {
  match: "Matches",
  explained: "Differs, explained",
  unexplained: "Differs, not explained",
  statement_only: "Statement only",
};

export const MONTH_STATE_WORDS: Record<string, string> = {
  complete: "Statement and Ads report",
  "ads partial": "Statement; Ads report covers part of the month",
  "statement only": "Statement only",
  "ads only": "Ads report only",
  nothing: "Not imported",
};
