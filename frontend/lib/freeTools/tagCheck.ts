/**
 * The tag checker. Etsy's limits from lib/etsyText.ts, our advice from lib/listingRules.ts, brands from
 * lib/brandSpot.ts. Nothing is sent anywhere, and no AI is involved.
 */
import { spotBrands } from "../brandSpot.ts";
import { MAX_TAG, MAX_TAGS, tagParts, tagsStartingBadly } from "../etsyText.ts";
import { MIN_MULTI_WORD_TAGS } from "../listingRuleData.ts";
import { phraseCount, sameSearchTags, tagsOnlyInTitle } from "../listingRules.ts";
import { READ } from "../marketing/sources.ts";
import { fail, note, pass, plural, quoted, shownChars, warn, type Answer, type Line, type Rule } from "./check.ts";

export const TAG_RULES: Rule[] = [
  { id: "count", name: "13 tags at most", kind: "etsy", quote: "You can use up to 13 tags per listing.", source: READ.tags },
  { id: "length", name: "20 characters each", kind: "etsy", quote: "Each tag can contain up to 20 characters.", source: READ.tags },
  { id: "characters", name: "Allowed characters", kind: "etsy", quote: "valid tag strings contain only letters, numbers, whitespace characters, -, ', ™, ©, and ®.", source: READ.api },
  { id: "start", name: "No ' or - at the start", kind: "etsy", quote: "You're able to use ' and - within words and phrases, but cannot start your tag with these characters.", source: READ.tags },
  { id: "duplicates", name: "No duplicates", kind: "advice", why: "Two tags with the same words, in any order or with a plural, spend two places on what we treat as one search." },
  { id: "title_words", name: "Not only the title's words", kind: "advice", why: "A tag made only of words already in your title adds nothing new. Use the place for another way of describing the item." },
  { id: "phrases", name: "Phrases over single words", kind: "advice", why: `We aim for at least ${MIN_MULTI_WORD_TAGS} of the 13 to be two words or more, because people tend to search in phrases.` },
  { id: "all", name: "Use all 13", kind: "advice", why: "An empty place is a description you did not use. We fill all 13." },
];

/** One tag per line or per comma. A comma cannot be in a tag anyway. */
export const parseTags = (text: string): string[] => text.split(/[\n,]/).map((tag) => tag.trim().replace(/\s+/g, " ")).filter(Boolean);

function answer(id: string, tags: string[], title: string): Answer {
  const parts = tagParts(tags);
  switch (id) {
    case "count":
      return tags.length > MAX_TAGS ? fail(`You have ${tags.length}, ${tags.length - MAX_TAGS} over.`) : pass(`You have ${tags.length}.`);
    case "length":
      return parts.long.length ? fail(`Over ${MAX_TAG}: ${quoted(parts.long)}.`) : pass("All of them fit.");
    case "characters":
      return parts.bad.length ? fail(parts.bad.map(({ tag, chars, fix }) => `"${tag}" has ${shownChars(chars)}. Fix: ${fix}.`).join(" ")) : pass("Every character is allowed.");
    case "start": {
      // The app's finding, and like the app a note: Etsy's Help states it, the API schema does not.
      const starts = tagsStartingBadly(tags);
      return starts.length ? warn(`These start with one: ${quoted(starts)}.`) : pass("None of them does.");
    }
    case "duplicates": {
      const lower = tags.map((tag) => tag.toLowerCase());
      const exact = [...new Set(lower.filter((tag, i) => lower.indexOf(tag) !== i))];
      const same = sameSearchTags(tags).map(([a, b]) => `"${a}" and "${b}"`);
      if (!exact.length && !same.length) return pass("None found.");
      return warn([exact.length ? `Twice: ${quoted(exact)}.` : "", same.length ? `Same words: ${same.join(", ")}.` : ""].filter(Boolean).join(" "));
    }
    case "title_words": {
      if (!title.trim()) return note("Add your title above to check this.");
      const repeats = tagsOnlyInTitle(tags, title);
      return repeats.length ? warn(`Only your title's words: ${quoted(repeats)}.`) : pass("Each tag adds a word of its own.");
    }
    case "phrases": {
      const multi = phraseCount(tags);
      if (tags.length >= MIN_MULTI_WORD_TAGS && multi < MIN_MULTI_WORD_TAGS) return warn(`${multi} of your ${tags.length} are phrases.`);
      return pass(`${multi} of your ${plural(tags.length, "tag")} ${multi === 1 ? "is a phrase" : "are phrases"}.`);
    }
    case "all":
      return tags.length < MAX_TAGS ? warn(`You have room for ${MAX_TAGS - tags.length} more.`) : pass("All 13 are used.");
    default:
      throw new Error(`no answer for the rule ${id}`);
  }
}

/** One line per rule, in order. */
export function checkTags(tags: string[], title = ""): Line[] {
  if (!tags.length) return TAG_RULES.map((rule) => ({ ...rule, verdict: "idle", detail: "" }));
  return TAG_RULES.map((rule) => ({ ...rule, ...answer(rule.id, tags, title) }));
}

export const tagBrands = (tags: string[]): string[] => [...new Set(tags.flatMap((tag) => spotBrands(tag)))];

export const TAG_EXAMPLE = "nurse shirt, nurses shirts, shirt nurse, a very long tag about nursing life, cute, rn gift & more, er nurse tee";
export const TAG_EXAMPLE_TITLE = "Retro Nurse Shirt";
