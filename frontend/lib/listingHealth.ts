/**
 * Listing health's words (docs/listing-health.md). The buckets and the problems come from the server
 * (backend/app/pipeline/listing_health.py), never from here: this file only names them and says whose rule each is.
 * A rule is Etsy's, with the words quoted and the page that states them (docs/guide-sources.md), or ours, and it says
 * so. Nothing on this page is a grade, and nothing predicts how a listing will do.
 */
import { READ } from "./marketing/sources.ts";
import type { Source } from "./marketing/content.ts";

export type Bucket = "fix_now" | "worth_improving" | "selling" | "too_new" | "nothing_to_fix" | "not_judged";
export type Show = "to_do" | "all" | Bucket;

export type HealthProblem = { id: string; kind: "etsy" | "advice"; level: "fix_now" | "worth_improving"; sentence: string; fixes: string[] };

export type HealthListing = {
  listing_id: number; title: string; url: string; image: string | null; tags: string[]; state: string; bucket: Bucket;
  problems: HealthProblem[]; suggestions: HealthProblem[]; note: string | null; brands: string[];
  recent_sales: number; past_sales: number; favourites: number; attributes_read: boolean;
  profile_id: string | null; profile_name: string | null; profile_unclear: boolean;
};

export type HealthPage = {
  connection_id: string | null; shop_name: string | null; reading: boolean; total: number; counts: Partial<Record<Bucket, number>>;
  sales_known: boolean; selling_days: number; new_days: number; show: Show; page: number; pages: number; listings: HealthListing[];
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

/** The three figures at the top, and the quieter ones beside them. Counts of listings, never a grade. */
export const BUCKETS: { bucket: Bucket; label: string; about: string; tone: "failed" | "attention" | "done" | "neutral" }[] = [
  { bucket: "fix_now", label: "Fix now", about: "Real problems: a brand name, something Etsy rejects, no main photo, a required attribute, a listing that stopped selling.", tone: "failed" },
  { bucket: "worth_improving", label: "Worth improving", about: "No sale in the selling window, and something we can fix with one click.", tone: "attention" },
  { bucket: "selling", label: "Selling", about: "A sale in the selling window. Left as it is.", tone: "done" },
  { bucket: "too_new", label: "Too new to judge", about: "Younger than the new-listing window, with no sale yet.", tone: "neutral" },
  { bucket: "nothing_to_fix", label: "Nothing to fix", about: "No sale, and nothing we can fix with one click.", tone: "neutral" },
  { bucket: "not_judged", label: "Sales not read yet", about: "Without your sales we cannot tell what sells, so no title or tag change is suggested.", tone: "neutral" },
];
export const MAIN_BUCKETS: Bucket[] = ["fix_now", "worth_improving", "selling"];
export const bucketOf = (bucket: Bucket) => BUCKETS.find((b) => b.bucket === bucket)!;

export const SELLING_NOTE = "Changing the title or tags of a selling listing can lower its search position.";
export const SAID_FIRST = "Listings that sell are left alone. Real problems come first, then listings that have not sold lately and that we can improve with one click. It does not predict how Etsy ranks your listing.";

type Rule = { name: string } & ({ kind: "etsy"; quote: string; source: Source } | { kind: "advice"; why: string });

/** Every problem the server can name. Etsy's are quoted from a page we read (docs/guide-sources.md). */
export const PROBLEMS: Record<string, Rule> = {
  brand: { name: "Brand or character name", kind: "advice", why: "We look for names on our own list in any form. The warning is the app's; the decision is yours." },
  title_rejected: { name: "Title Etsy does not accept", kind: "etsy", quote: "A listing's title can be up to 140 characters long.", source: READ.createListing },
  tags_rejected: { name: "Tags Etsy does not accept", kind: "etsy", quote: "Each tag can contain up to 20 characters. Tags can only include spaces, letters, numbers. You're able to use ' and - within words and phrases, but cannot start your tag with these characters.", source: READ.tags },
  main_photo: { name: "No main photo", kind: "etsy", quote: "Make your first listing photo the clearest, most realistic representation of the item buyers will receive.", source: READ.imageHelp },
  required_attribute: { name: "Required attribute empty", kind: "etsy", quote: "Fill out both attributes and tags for your listings to make sure potential buyers find and see your items on Etsy.", source: READ.attributes },
  stopped_selling: { name: "Stopped selling", kind: "etsy", quote: "If an item has been purchased or its listing has expired, you can start selling it again by renewing the listing.", source: READ.renew },
  stray_characters: { name: "Stray characters", kind: "advice", why: "Invisible characters or leftover code characters (such as }] at the end of a sentence) show up as junk to buyers." },
  tags_missing: { name: "Tags left unused", kind: "etsy", quote: "You can use up to 13 tags per listing.", source: READ.tags },
  attributes_missing: { name: "Attributes left empty", kind: "etsy", quote: "Attributes can also help buyers find your items more easily in search results.", source: READ.attributes },
  title_guidance: { name: "Title against the search guidance", kind: "etsy", quote: "Consider using less than 15 words when including the traits that make your item special.", source: READ.createListing },
  dated_tags: { name: "Tags for a holiday that has passed", kind: "advice", why: "A tag such as \"christmas 2024\" describes a season that is over. Only suggested for listings that are not selling." },
  seasonal: { name: "Seasonal", kind: "advice", why: "The season planner schedules seasonal listings before their holiday. Our advice, only for listings that are not selling." },
};

/** What each fix does. "edit" and "season_planner" are links; the others make a preview first. */
export const FIX_KINDS = [
  { kind: "tags", label: "Add missing tags", about: "From the tags on your profile's reference listing. Your tags stay." },
  { kind: "attributes", label: "Fill attributes", about: "From your profile's reference listing, in the same category. A filled one is never changed." },
  { kind: "fix_tags", label: "Fix tags Etsy rejects", about: "Only what Etsy rejects changes: a tag too long or with characters Etsy does not take. Uses no allowance." },
  { kind: "rewrite", label: "Rewrite title and tags", about: "Written by AI, from this listing's own text. Uses your allowance." },
  { kind: "clean_text", label: "Remove stray characters", about: "The same words without invisible or leftover code characters. Uses no allowance." },
] as const;
export const PREVIEW_KINDS = FIX_KINDS.map((f) => f.kind as string);

/** The previews a listing's own problems offer, in the order above. Nothing else is offered for it. */
export function fixesFor(listing: Pick<HealthListing, "problems">): string[] {
  const offered = new Set(listing.problems.flatMap((p) => p.fixes));
  return PREVIEW_KINDS.filter((k) => offered.has(k));
}

/** A Shop Manager link or the season planner, when a problem asks for one. */
export const links = (listing: Pick<HealthListing, "problems" | "suggestions">) => {
  const all = [...listing.problems, ...listing.suggestions].flatMap((p) => p.fixes);
  return { edit: all.includes("edit"), seasonPlanner: all.includes("season_planner") };
};

/** Sales and favourites, as facts about the listing: never a grade. */
export function factsLine(listing: Pick<HealthListing, "recent_sales" | "past_sales" | "favourites">, sellingDays: number): string {
  const parts = [];
  if (listing.recent_sales) parts.push(`${listing.recent_sales} sold in the last ${sellingDays} days`);
  else if (listing.past_sales) parts.push(`${listing.past_sales} sold in the past year`);
  else parts.push("no sales in the past year");
  if (listing.favourites) parts.push(`${listing.favourites} favourite${listing.favourites === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

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
  if (check.free) parts.push(`${check.free} already counted, so free`);
  if (check.counted) parts.push(`${check.counted} counted as listing${check.counted === 1 ? "" : "s"}`);
  const here = parts.length ? `For these: ${parts.join(", ")}.` : "";
  return check.fits ? here : `${here} You have ${check.remaining} left this period, so this will not run. Choose fewer.`;
}
