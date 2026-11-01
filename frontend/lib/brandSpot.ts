/**
 * How a brand is recognised in a text: the copy of Blocklist.find and Blocklist.spot in
 * backend/app/compliance/trademarks.py, for the free tools in the browser. The list comes from there
 * (listingRuleData.ts is written from it) and both sides read
 * backend/tests/fixtures/free_tool_cases.json, so the tools warn about what the app warns about.
 * Like the app's warning this never stops anything, and a plain substring is never a name:
 * "marvelous" and "Blue Y Neck" are not brands.
 */
import { BRAND_WARNING, JOINED_AFTER_FROM, JOINED_BEFORE_FROM, LOOSE_FROM, ORDINARY_WORDS, TRADEMARKS } from "./listingRuleData.ts";

const JOIN = "[\\s\\-_.'’]*";
const INSIDE = "[\\s\\-_.'’#]{0,2}";
const PIECE = /[\s\-_.'’#]+/;
const CAMEL = /(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/g;
const SWAPS: Record<string, string> = { i: "i1", l: "l1", o: "o0", e: "e3", a: "a4", s: "s$" };
const SWAPS_LONG: Record<string, string> = { ...SWAPS, i: "i1l", l: "l1i" };
const ORDINARY = new Set<string>(ORDINARY_WORDS);

const fold = (text: string): string => text.normalize("NFKD").replace(/\p{Mn}/gu, "").toLowerCase();
const parts = (term: string): string[] => fold(term).match(/[a-z0-9]+/g) ?? [];
const squeezed = (term: string): string => parts(term).join("");
const escape = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

type Term = { term: string; whole: RegExp; spotter: RegExp; size: number };

function compile(term: string): Term | null {
  const pieces = parts(term);
  if (!pieces.length) return null;
  const letters = pieces.join("");
  const swaps = letters.length > LOOSE_FROM ? SWAPS_LONG : SWAPS;
  const spot = letters.length < LOOSE_FROM
    ? [...letters].map(escape).join("\\.?")
    : [...letters].map((c) => (swaps[c] ? `[${escape(swaps[c])}]` : escape(c))).join(INSIDE);
  const joined = pieces.map((w) => (w === "and" ? "(?:and|&)" : escape(w))).join(JOIN);
  return { term, whole: new RegExp(`(?<![a-z0-9])${joined}(?![a-z0-9])`), spotter: new RegExp(spot, "g"), size: letters.length };
}

let compiled: Term[] | null = null;
function terms(): Term[] {
  if (compiled) return compiled;
  const seen = new Set<string>();
  compiled = [];
  for (const raw of TRADEMARKS) {
    const term = raw.trim();
    if (!term || seen.has(fold(term))) continue;
    const entry = compile(term);
    if (entry) {
      seen.add(fold(term));
      compiled.push(entry);
    }
  }
  return compiled;
}

/** A word ends here (a swapped letter is part of a word too). */
function edge(text: string, at: number): boolean {
  return at < 0 || at >= text.length || !/[\p{L}\p{N}$]/u.test(text[at]);
}

function isOrdinaryWord(word: string): boolean {
  const plain = word.replace(/0/g, "o").replace(/3/g, "e").replace(/4/g, "a").replace(/\$/g, "s");
  return ORDINARY.has(plain.replace(/1/g, "l")) || ORDINARY.has(plain.replace(/1/g, "i"));
}

/** Whether what the pattern matched really is the name, by where its edges fall. */
function counts(text: string, start: number, end: number, size: number): boolean {
  const pieces = text.slice(start, end).split(PIECE);
  if (pieces.length > 1 && !(pieces.every((p) => p.length >= 2) || pieces.every((p) => p.length === 1))) return false;
  const left = edge(text, start - 1);
  const right = edge(text, end);
  if (left && right) return true;
  if (size < LOOSE_FROM || pieces.length > 1 || !(left || right)) return false;
  let from = start;
  let to = end;
  while (!edge(text, from - 1)) from -= 1;
  while (!edge(text, to)) to += 1;
  if (isOrdinaryWord(text.slice(from, to))) return false;
  if (left) return size >= JOINED_AFTER_FROM || text.slice(end, to) === "s";
  return size >= JOINED_BEFORE_FROM;
}

/** The listed names written whole. */
export function findBrands(text: string): string[] {
  if (!text) return [];
  const folded = fold(text);
  return terms().filter((t) => t.whole.test(folded)).map((t) => t.term);
}

/** The listed names in any form: whole, joined to other words, spaced out, with common letter swaps. */
export function spotBrands(text: string): string[] {
  if (!text) return [];
  const whole = findBrands(text);
  const found = new Set(whole);
  const split = text.replace(CAMEL, " ");
  for (const variant of new Set([fold(text), fold(split)])) {
    const spans: { start: number; end: number; term: string }[] = [];
    for (const t of terms()) {
      for (const m of variant.matchAll(t.spotter)) {
        const start = m.index;
        const end = start + m[0].length;
        if (counts(variant, start, end, t.size)) spans.push({ start, end, term: t.term });
      }
    }
    for (const span of spans) {
      // "disneyland" is Disneyland, not Disney as well.
      const inside = spans.some((o) => o.term !== span.term && o.start <= span.start && span.end <= o.end && o.end - o.start > span.end - span.start);
      if (!inside) found.add(span.term);
    }
  }
  // A name written whole inside a longer listed name was not written at all.
  const names = [...found];
  return names.filter((term) => {
    const own = squeezed(term);
    return whole.includes(term) || !names.some((other) => squeezed(other) !== own && squeezed(other).includes(own));
  });
}

/** The sentence the app says about a brand: written from trademarks.BRAND_WARNING. */
export const brandWarning = (brand: string): string => BRAND_WARNING.replace("{brand}", brand);

/** What a title and tags are warned about, each brand once: trademarks.brand_warnings with no filter in force. */
export const brandWarnings = (title: string, tags: string[]): string[] =>
  [...new Set([title, ...tags].flatMap((text) => spotBrands(text)))].map(brandWarning);
