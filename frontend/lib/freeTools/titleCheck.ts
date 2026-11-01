/**
 * The title checker. Every answer comes from a rule the app already has: Etsy's limits from
 * lib/etsyText.ts, our advice from lib/listingRules.ts, brands from lib/brandSpot.ts. Nothing is sent
 * anywhere, and no AI is involved.
 */
import { spotBrands } from "../brandSpot.ts";
import { MAX_TITLE, titleParts } from "../etsyText.ts";
import { MAX_TITLE_PHRASES, MAX_TITLE_WORDS } from "../listingRuleData.ts";
import { commerceIn, garmentsIn, giftIn, itemFirst, phrases, repeatedWords, subjectiveIn, wordCount } from "../listingRules.ts";
import { READ } from "../marketing/sources.ts";
import { fail, note, pass, plural, quoted, shownChars, warn, type Answer, type Line, type Rule } from "./check.ts";

export const TITLE_RULES: Rule[] = [
  { id: "length", name: "140 characters at most", kind: "etsy", quote: "A listing's title can be up to 140 characters long.", source: READ.createListing },
  { id: "characters", name: "Allowed characters", kind: "etsy", quote: "valid title strings contain only letters, numbers, punctuation marks, mathematical symbols, whitespace characters, ™, ©, and ®.", source: READ.api },
  { id: "once", name: "%, :, & and + once each", kind: "etsy", quote: "You can only use the %, :, & and + characters once each.", source: READ.api },
  { id: "words", name: "Fewer than 15 words", kind: "etsy", quote: "Consider using less than 15 words when including the traits that make your item special.", source: READ.createListing },
  { id: "item_first", name: "The item comes first", kind: "advice", why: "A reader should know what the thing is from the first few words. Traits come after it." },
  { id: "repeats", name: "No repeated words", kind: "advice", why: "A second use of a word takes room from a word that says something new. Other phrasings can go in the tags." },
  { id: "subjective", name: "No opinion words", kind: "advice", why: "Words like \"cute\" say what you think, not what the item is. They read better in the description." },
  { id: "commerce", name: "No price or shipping wording", kind: "advice", why: "Sales and shipping change, and a title should still be true next month." },
  { id: "gift", name: "No gift phrases", kind: "advice", why: "\"Gift for her\" says who buys it, not what it is. It makes a good tag." },
  { id: "shorter", name: "Shorter is clearer", kind: "advice", why: `We keep a title to ${MAX_TITLE_PHRASES} comma-separated parts at most, with the garment named once. Extra keywords can go in the tags.` },
];

function answer(id: string, title: string): Answer {
  const parts = titleParts(title);
  switch (id) {
    case "length":
      return parts.length > MAX_TITLE ? fail(`Yours is ${parts.length} characters, ${parts.length - MAX_TITLE} over.`) : pass(`Yours is ${parts.length} characters.`);
    case "characters":
      return parts.bad.length ? fail(`These are not allowed: ${shownChars(parts.bad)}. Fix: ${parts.fix}.`) : pass("Every character is allowed.");
    case "once":
      return parts.twice.length ? fail(`Yours uses ${parts.twice.join(" ")} more than once.${parts.twice.includes("&") ? ' Write "and" for the other &.' : ""}`) : pass("None of them is used twice.");
    case "words": {
      const count = wordCount(title);
      return count > MAX_TITLE_WORDS ? warn(`Yours has ${count} words. This is a suggestion, not a limit.`) : pass(`Yours has ${plural(count, "word")}.`);
    }
    case "item_first": {
      if (itemFirst(title)) return pass("The first part of your title names the garment.");
      if (garmentsIn(title).length) return warn(`"${garmentsIn(title)[0]}" comes after the first comma. Try moving it to the first part.`);
      return note("We only recognise garment words here (shirt, tee, hoodie and the like). If yours is another item, check that it is named first.");
    }
    case "repeats": {
      const repeated = repeatedWords(title);
      return repeated.length ? warn(`Yours repeats ${quoted(repeated)}.`) : pass("Each word is used once.");
    }
    case "subjective": {
      const found = subjectiveIn(title);
      return found.length ? warn(`Yours uses ${quoted(found)}.`) : pass("None found from our list.");
    }
    case "commerce": {
      const found = commerceIn(title);
      return found.length ? warn(`Yours uses ${quoted(found)}.`) : pass("None found from our list.");
    }
    case "gift": {
      const found = giftIn(title);
      return found.length ? warn(`Yours uses ${quoted(found)}.`) : pass("None found from our list.");
    }
    case "shorter": {
      const count = phrases(title).length;
      const garments = garmentsIn(title);
      if (count > MAX_TITLE_PHRASES) return warn(`Yours has ${count} comma-separated parts.`);
      if (garments.length > 1) return warn(`Yours names the garment ${garments.length} times (${garments.join(", ")}).`);
      return pass(`Yours has ${plural(count, "part")}.`);
    }
    default:
      throw new Error(`no answer for the rule ${id}`);
  }
}

/** One line per rule, in order. An empty title has been checked against nothing. */
export function checkTitle(title: string): Line[] {
  if (!title.trim()) return TITLE_RULES.map((rule) => ({ ...rule, verdict: "idle", detail: "" }));
  return TITLE_RULES.map((rule) => ({ ...rule, ...answer(rule.id, title) }));
}

export const titleBrands = (title: string): string[] => spotBrands(title);

export const TITLE_EXAMPLE = "Cute Nurse Shirt, Perfect Nurse Gift for Her, Free Shipping";
