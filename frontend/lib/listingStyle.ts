/**
 * The words for the two listing styles (backend pipeline/search_rules.py), in one
 * place: the profile card's choice (components/ListingStyle.tsx), the review card
 * (components/ReviewCard.tsx) and the Analytics comparison (components/analytics/
 * TitleStyles.tsx, lib/analyticsDefinitions.ts) all read them.
 *
 * They describe what the style does, never what it achieves: Etsy publishes no
 * figures on views, clicks, sales or ranking for any title style, so no wording
 * here claims one (lib/listingStyle.test.ts keeps it that way).
 */

export type ListingStyleKey = "search" | "classic";

export const STYLE_LABELS: Record<ListingStyleKey, string> = {
  search: "Short, clear title",
  classic: "Long keyword title",
};

/** Under each choice on the profile card. */
export const STYLE_HELP: Record<ListingStyleKey, string> = {
  search:
    "Follows Etsy's current title guidance: a short, clear title with the item and its key details first. Other keywords go to tags and the description.",
  classic: "110–140 character title made of keyword phrases, as before. A title over 140 characters is rewritten once, then flagged on its card, never cut.",
};

/** The rules the short style writes to, under the choice once it is picked. */
export const SEARCH_RULES_NOTE =
  "Fewer than 15 words, each used once; no opinion words (perfect, cute…), no price, sale or shipping wording, and holidays, occasions or recipients only when essential to the item. A title that still breaks a rule after one rewrite is kept as written and flagged on its card, never cut. Applies to listings written from now on.";

/** On a review card written in the short style. */
export const SEARCH_CARD_NOTE = STYLE_HELP.search;

/** Above a title the app wrote that still breaks Etsy's title guidance after one rewrite. */
export const TITLE_WARNING_HEAD = "This title does not follow Etsy's title guidance yet. It was kept as written, not cut:";
export const TITLE_WARNING_TAIL = "Edit it here; the warning goes once you change the title.";

/** The Analytics comparison names the styles by the same labels. */
export const COMPARISON_LABELS = { short: STYLE_LABELS.search, long: STYLE_LABELS.classic } as const;

/** Wording that claims an effect Etsy has published no data for. Never in the app's or the site's copy. */
export const PERFORMANCE_CLAIMS = [
  /more (views|clicks|sales|traffic|visits|buyers|orders)/i,
  /(boost|increase|improve)s? (your )?(views|clicks|sales|traffic|ranking|visibility)/i,
  /rank(s|ed)? higher/i,
  /higher (in )?(search|ranking)/i,
  /best (for|in) 202\d/i,
  /top of (etsy|search)/i,
  /get (found|seen) more/i,
  /guarantee[ds]?/i,
  // Turkish, should copy in it ever be added.
  /daha fazla (görüntülenme|tıklama|satış|ziyaret|sipariş)/i,
  /(sıralama|aramada)[^.]{0,20}(yüksel|üst sıra)/i,
  /en iyi[^.]{0,30}202\d|202\d[^.]{0,30}en iyi/i,
  /garantili/i,
];
