/**
 * The app's title and tag rules, for the free tools in the browser: the copy of the checks in
 * backend/app/pipeline/search_rules.py. The words and limits come from there (listingRuleData.ts is
 * written from it) and both sides read backend/tests/fixtures/free_tool_cases.json, so they agree.
 * What Etsy itself refuses is lib/etsyText.ts.
 */
import { COMMERCE, GARMENT_WORDS, GIFT, STOP_WORDS, SUBJECTIVE } from "./listingRuleData.ts";

const WORD_CHAR = "[\\p{L}\\p{N}_]";
const TSHIRT = new RegExp(`(?<!${WORD_CHAR})t[\\s-]?shirts?(?!${WORD_CHAR})`, "gu");
const STOP = new Set<string>(STOP_WORDS);
const GARMENTS = new Set<string>(GARMENT_WORDS);

/** Lowercase words, with every spelling of "t-shirt" as one word. */
export function words(text: string): string[] {
  const plain = text.toLowerCase().replace(TSHIRT, "tshirt").replace(/['’]/g, "");
  return plain.match(/[a-z0-9]+/g) ?? [];
}

/** Enough to see "nurses" and "nurse" as one word. */
export function stem(word: string): string {
  if (word.length > 4 && word.endsWith("ies")) return `${word.slice(0, -3)}y`;
  if (word.length > 3 && word.endsWith("s") && !word.endsWith("ss")) return word.slice(0, -1);
  return word;
}

export const keywords = (text: string): string[] => words(text).filter((w) => !STOP.has(w)).map(stem);

export const phrases = (title: string): string[] => title.split(",").map((p) => p.trim()).filter(Boolean);

function has(text: string, term: string): boolean {
  if (term.startsWith("%")) return text.toLowerCase().includes(term);
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?<!${WORD_CHAR})${escaped}(?!${WORD_CHAR})`, "iu").test(text);
}

export const subjectiveIn = (title: string): string[] => SUBJECTIVE.filter((term) => has(title, term));
export const commerceIn = (title: string): string[] => COMMERCE.filter((term) => has(title, term));
export const giftIn = (title: string): string[] => GIFT.filter((term) => has(title, term));
export const garmentsIn = (title: string): string[] => words(title).filter((w) => GARMENTS.has(w));

/** The words a title uses more than once (root words). */
export function repeatedWords(title: string): string[] {
  const seen = new Map<string, number>();
  for (const word of keywords(title)) seen.set(word, (seen.get(word) ?? 0) + 1);
  return [...seen].filter(([, n]) => n > 1).map(([word]) => word).sort();
}

/** Words as a buyer reads them: separated by spaces; a lone "&" or "-" is not one. */
export const wordCount = (title: string): number =>
  title.replace(/,/g, " ").split(/\s+/).filter((w) => /[A-Za-z0-9]/.test(w)).length;

/** The item is named in the first phrase (a garment word; the app knows no others). */
export function itemFirst(title: string): boolean {
  const first = phrases(title)[0];
  return first !== undefined && garmentsIn(first).length > 0;
}

/** Pairs of differently written tags made of the same root words. */
export function sameSearchTags(tags: string[]): [string, string][] {
  const pairs: [string, string][] = [];
  const byWords = new Map<string, string>();
  for (const tag of tags) {
    const key = [...new Set(keywords(tag))].sort().join(" ");
    if (!key) continue;
    const earlier = byWords.get(key);
    if (earlier !== undefined && earlier.trim().toLowerCase() !== tag.trim().toLowerCase()) pairs.push([earlier, tag]);
    if (earlier === undefined) byWords.set(key, tag);
  }
  return pairs;
}

/** The tags made only of the title's words. */
export function tagsOnlyInTitle(tags: string[], title: string): string[] {
  const inTitle = new Set(keywords(title));
  return tags.filter((tag) => {
    const own = keywords(tag);
    return own.length > 0 && own.every((w) => inTitle.has(w));
  });
}

/** How many tags are two words or more. */
export const phraseCount = (tags: string[]): number => tags.filter((tag) => tag.trim().split(/\s+/).length >= 2).length;
