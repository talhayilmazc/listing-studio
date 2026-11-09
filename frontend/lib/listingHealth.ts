/**
 * Listing health's words: what each check is and whose rule it is. The verdicts come from the server
 * (backend/app/pipeline/listing_health.py), never from here. A rule is either Etsy's, with the words quoted
 * and the page that states them (docs/guide-sources.md), or ours, and it says so. Nothing here predicts how
 * a listing will do: Etsy publishes no such figure.
 */
import type { Rule } from "./freeTools/check.ts";
import { TAG_RULES } from "./freeTools/tagCheck.ts";
import { TITLE_RULES } from "./freeTools/titleCheck.ts";
import { READ } from "./marketing/sources.ts";

export type Verdict = "pass" | "fail" | "warn" | "note";

export type HealthCheck = { id: string; kind: "etsy" | "advice"; verdict: Verdict; detail: string };

export type HealthListing = {
  listing_id: number; title: string; url: string; image: string | null; tags: string[]; score: number; to_fix: number;
  checks: HealthCheck[]; brands: string[]; attributes_read: boolean;
  profile_id: string | null; profile_name: string | null; profile_unclear: boolean;
};

export type HealthPage = {
  connection_id: string | null; shop_name: string | null; reading: boolean; total: number; average: number | null;
  needs_work: number; page: number; pages: number; listings: HealthListing[];
  max_per_approval: number; undo_days: number; rewrite_rule: string;
};

export type FixAttribute = { property_id: number; property_name?: string; value_ids?: number[]; values?: string[] };
export type FixSide = { title?: string; tags?: string[]; attributes?: FixAttribute[] };
export type FixState = "writing" | "proposed" | "could_not_write" | "approved" | "applied" | "failed" | "changed" | "undoing" | "undone" | "undo_failed";
export type Fix = {
  id: string; connection_id: string; listing_id: number; url: string; state: FixState; note: string | null; kinds: string[];
  before: FixSide; after: FixSide; counted: boolean; created_at: string; applied_at: string | null; undo_until: string | null; can_undo: boolean;
};
export type PreviewAnswer = { fixes: Fix[]; skipped: { listing_id: number; title: string; reason: string }[]; still_reading: number[] };
export type RewriteCheck = { rule: string; free: number; counted: number; remaining: number; fits: boolean };

/** Not one of ours to apply on its own, so the buttons never run it without saying the rule first. */
export const FIX_KINDS = [
  { kind: "tags", label: "Add missing tags", about: "From the tags on your profile's reference listing. Your tags stay." },
  { kind: "attributes", label: "Fill attributes", about: "From your profile's reference listing, in the same category. A filled one is never changed." },
  { kind: "rewrite", label: "Rewrite title and tags", about: "Written by AI, from this listing's own text and the rules above. Uses your allowance." },
  { kind: "clean_text", label: "Remove stray characters", about: "The same words without invisible or leftover code characters. You see the cleaned text before anything changes. Uses no allowance." },
] as const;

const fromTools = (rules: Rule[], prefix: string, ids: Record<string, string>): Record<string, Rule> =>
  Object.fromEntries(rules.map((rule) => [ids[rule.id] ?? `${prefix}${rule.id}`, rule]));

export const RULES: Record<string, Rule> = {
  ...fromTools(TITLE_RULES, "title_", { item_first: "item_first", repeats: "repeats", subjective: "subjective", commerce: "commerce", gift: "gift", shorter: "shorter" }),
  ...fromTools(TAG_RULES, "tag_", { duplicates: "tag_duplicates", title_words: "tag_title_words", phrases: "tag_phrases", all: "tag_all" }),
  attributes: { id: "attributes", name: "Attributes filled", kind: "etsy", quote: "Fill out both attributes and tags for your listings to make sure potential buyers find and see your items on Etsy.", source: READ.attributes },
  materials: { id: "materials", name: "Materials listed", kind: "advice", why: "A buyer of clothes wants to know what it is made of. We read the listing's own field and never make one up." },
  photos: { id: "photos", name: "At least 5 photos", kind: "advice", why: "Our rule of thumb: several views of the item answer questions before they are asked." },
  description: { id: "description", name: "A description with some length", kind: "advice", why: "Our rule of thumb is at least 160 characters, enough to say what it is, what it is made of and how it fits." },
  section: { id: "section", name: "In a shop section", kind: "advice", why: "Sections help a buyer browse your shop." },
  stray_characters: { id: "stray_characters", name: "Fix now: no stray characters", kind: "advice", why: "Invisible characters or leftover code characters (such as }] at the end of a sentence) show up as junk to buyers. Remove stray characters fixes them after you see the cleaned text." },
  personalization: { id: "personalization", name: "Personalization asks for what the title promises", kind: "advice", why: "If the title says personalized, the listing should ask the buyer what to put on it." },
  brand: { id: "brand", name: "No brand or character names", kind: "advice", why: "We look for names on our own list in any form. The warning is the app's; the decision is yours." },
};

export const VERDICT_WORDS: Record<Verdict, string> = { pass: "Fine", fail: "Fix this", warn: "Worth a look", note: "Your call" };
export const VERDICT_TONE = { pass: "done", fail: "failed", warn: "attention", note: "neutral" } as const;
export const KIND_WORDS = { etsy: "Etsy's rule", advice: "Our advice" } as const;

export const SAID_FIRST = "A tidy-up list against Etsy's published rules and our own advice. It does not predict how Etsy ranks your listing.";

/** The worst of what the listing needs, for the seller's eye. */
export const scoreTone = (score: number): "done" | "attention" | "failed" => (score >= 90 ? "done" : score >= 70 ? "attention" : "failed");

export const toLookAt = (listing: HealthListing): HealthCheck[] => listing.checks.filter((c) => c.verdict === "fail" || c.verdict === "warn");

/** Selecting more than one approval may hold keeps the first ones, and says so. */
export function withinCap(ids: number[], cap: number): { ids: number[]; left: number } {
  const unique = [...new Set(ids)];
  return { ids: unique.slice(0, cap), left: Math.max(0, unique.length - cap) };
}

export type DiffLine = { what: string; removed: string[]; added: string[]; kept: string[] };

const value = (a: FixAttribute | undefined): string => (a ? (a.values ?? []).join(", ") || (a.value_ids ?? []).join(", ") : "");

/** What a fix changes, for the seller to read before they approve it: each side as Etsy would hold it. */
export function diffOf(fix: Pick<Fix, "before" | "after">): DiffLine[] {
  const out: DiffLine[] = [];
  const { before, after } = fix;
  if (after.title !== undefined && after.title !== before.title) {
    out.push({ what: "Title", removed: before.title ? [before.title] : [], added: [after.title], kept: [] });
  }
  if (after.tags) {
    const was = before.tags ?? [];
    const lower = new Set(was.map((t) => t.toLowerCase()));
    const now = new Set(after.tags.map((t) => t.toLowerCase()));
    const added = after.tags.filter((t) => !lower.has(t.toLowerCase()));
    const removed = was.filter((t) => !now.has(t.toLowerCase()));
    if (added.length || removed.length) out.push({ what: "Tags", removed, added, kept: was.filter((t) => now.has(t.toLowerCase())) });
  }
  for (const attribute of after.attributes ?? []) {
    const old = (before.attributes ?? []).find((a) => a.property_id === attribute.property_id);
    out.push({ what: attribute.property_name ?? `Attribute ${attribute.property_id}`, removed: value(old) ? [value(old)] : [], added: [value(attribute)], kept: [] });
  }
  return out;
}

export const FIX_WORDS: Record<FixState, { word: string; tone: "neutral" | "working" | "done" | "attention" | "failed" }> = {
  writing: { word: "Writing", tone: "working" },
  proposed: { word: "Waiting for you", tone: "attention" },
  could_not_write: { word: "Not written", tone: "failed" },
  approved: { word: "Changing", tone: "working" },
  applied: { word: "Changed on Etsy", tone: "done" },
  failed: { word: "Needs a look", tone: "failed" },
  changed: { word: "Left as it is", tone: "attention" },
  undoing: { word: "Putting back", tone: "working" },
  undone: { word: "Put back", tone: "neutral" },
  undo_failed: { word: "Not put back", tone: "failed" },
};

export const isWorking = (fix: Pick<Fix, "state">): boolean => fix.state === "writing" || fix.state === "approved" || fix.state === "undoing";

export function approvalSentence(count: number, undoDays: number): string {
  if (count === 0) return "Tick the changes you want. Nothing has gone to Etsy.";
  return `This changes ${count} live listing${count === 1 ? "" : "s"} on Etsy straight away. Each is read back afterwards, and Undo writes the previous version back for ${undoDays} days.`;
}

export function rewriteSentence(check: RewriteCheck): string {
  const parts = [];
  if (check.free) parts.push(`${check.free} free`);
  if (check.counted) parts.push(`${check.counted} counted as listing${check.counted === 1 ? "" : "s"} generated`);
  const here = parts.length ? `For these: ${parts.join(", ")}.` : "";
  return check.fits ? here : `${here} You have ${check.remaining} left this period, so this will not run. Choose fewer.`;
}
