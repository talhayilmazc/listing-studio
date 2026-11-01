/**
 * What one line of a checker's answer looks like. A rule is either Etsy's, with the words quoted and
 * the page that states them, or ours, and it says so. Nothing here predicts how a listing will do.
 */
import type { Source } from "../marketing/content.ts";
import { READ } from "../marketing/sources.ts";

export type Verdict = "idle" | "pass" | "fail" | "warn" | "note";

export type Rule = { id: string; name: string } & ({ kind: "etsy"; quote: string; source: Source } | { kind: "advice"; why: string });

export type Answer = { verdict: Verdict; detail: string };
export type Line = Rule & Answer;

export const VERDICT_WORDS: Record<Verdict, string> = { idle: "Not checked", pass: "Fine", fail: "Fix this", warn: "Worth a look", note: "Check by eye" };
export const KIND_WORDS = { etsy: "Etsy's rule", advice: "Our advice" } as const;

export const pass = (detail: string): Answer => ({ verdict: "pass", detail });
export const fail = (detail: string): Answer => ({ verdict: "fail", detail });
export const warn = (detail: string): Answer => ({ verdict: "warn", detail });
export const note = (detail: string): Answer => ({ verdict: "note", detail });

export const quoted = (items: readonly string[]): string => items.map((item) => `"${item}"`).join(", ");
/** What was found, without a word that a longer finding already says. */
export const longest = (items: readonly string[]): string[] => items.filter((item) => !items.some((other) => other !== item && other.includes(item)));
/** The characters a rule refuses, once each; one nobody can see is named. */
export const shownChars = (chars: string[]): string => [...new Set(chars.map((ch) => (/\s/.test(ch) ? "a tab or line break" : ch)))].join(" ");
export const plural = (n: number, one: string): string => `${n} ${one}${n === 1 ? "" : "s"}`;

/** The sum of a result, for the line above the list. */
export function summary(lines: Line[]): string {
  const fixes = lines.filter((l) => l.verdict === "fail").length;
  const looks = lines.filter((l) => l.verdict === "warn").length;
  if (!fixes && !looks) return "Nothing to change on these rules.";
  return [fixes ? `${plural(fixes, "thing")} to fix` : "", looks ? `${plural(looks, "thing")} worth a look` : ""].filter(Boolean).join(", ") + ".";
}

/*
 * The brand warning. It finds what the app finds (lib/brandSpot.ts), but on the public site a claim
 * about Etsy rests on a page that states it, so these are Etsy's own words, not the app's sentence.
 */
export const BRAND_SOURCES = [READ.ipReport, READ.ipNotice];
export const BRAND_RULE = `If a report complies with Etsy's policies, its Help says "we will remove the reported content", and "Repeat reports of infringement may result in the loss of account privileges."`;
export const brandLine = (brand: string): string => `${brand} is a trademark, on the list of names the Listyro app warns about.`;
export const BRAND_LIMITS = "The list is ours and short, and this is not legal advice. No warning here does not mean a name is free to use.";
