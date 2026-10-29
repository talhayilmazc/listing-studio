// A batch's listings in more than one shop: their state per shop, and "Publish in another
// shop" (backend api/another_shop.py, pipeline/shop_listing.py). Pure: no React, no API, so
// the rules are tested on their own (lib/anotherShop.test.ts).

import type { BatchShops, ShopState } from "./types";

export const STATE_WORDS: Record<ShopState, string> = {
  not_sent: "not sent",
  drafted: "drafted",
  scheduled: "scheduled",
  published: "published",
};

/**
 * ``sku`` with its leading ``from`` replaced by ``to`` (any case), and whether it started
 * with ``from``. An empty ``from`` puts ``to`` in front. The server's copy is
 * pipeline/shop_listing.py::replace_prefix (the same cases are tested on both).
 */
export function replacePrefix(sku: string | null | undefined, from: string, to: string): [string, boolean] {
  const value = (sku ?? "").trim();
  const old = from.trim();
  const add = to.trim();
  if (!old) return [add + value, true];
  if (value.toLowerCase().startsWith(old.toLowerCase())) return [add + value.slice(old.length), true];
  return [value, false];
}

/** One shop's line on the batch page: "published 14" or "not sent". */
export function shopLine(shop: BatchShops["shops"][number]): string {
  if (shop.total === 0) return "nothing written yet";
  const parts = (["published", "scheduled", "drafted"] as const)
    .filter((k) => shop[k] > 0)
    .map((k) => `${shop[k]} ${STATE_WORDS[k]}`);
  if (shop.not_sent === shop.total) return "not sent";
  if (shop.not_sent > 0) parts.push(`${shop.not_sent} not sent`);
  return parts.join(" · ");
}

/** The shops worth a line: those with anything from this batch, and the shop in view. */
export function shopsShown(data: BatchShops | null, inView: string | null): BatchShops["shops"] {
  if (!data) return [];
  return data.shops.filter((s) => s.not_sent < s.total || s.shop_id === inView);
}

/** A listing's state in one shop (none listed: not sent). */
export function stateIn(data: BatchShops | null, contentId: string | null | undefined, shopId: string): ShopState {
  if (!data || !contentId) return "not_sent";
  return data.listings.find((l) => l.content_id === contentId)?.states[shopId] ?? "not_sent";
}

/**
 * Whether a listing's profile can no longer be changed where the page is looking: it was
 * sent to the shop in view (or, with every shop in view, to any shop). What is on Etsy was
 * built with its profile; the page offers "Publish in another shop" instead.
 */
export function profileLocked(data: BatchShops | null, contentId: string | null | undefined, inView: string | null): boolean {
  if (!data || !contentId) return false;
  const states = data.listings.find((l) => l.content_id === contentId)?.states ?? {};
  if (inView) return (states[inView] ?? "not_sent") !== "not_sent";
  return Object.values(states).some((s) => s !== "not_sent");
}

/**
 * Whether the review card edits the shop in view's own text: only when the listing is on Etsy
 * in another shop and not yet in this one (its text there is fixed; this shop may differ).
 * A listing in no shop yet edits its own text, for every shop. (Before 2026-10-29 any shop
 * picked in the menu made it that shop's text: typed text then showed as empty elsewhere.)
 */
export function editsShopText(data: BatchShops | null, contentId: string | null | undefined, inView: string | null): boolean {
  if (!data || !contentId || !inView) return false;
  const states = data.listings.find((l) => l.content_id === contentId)?.states ?? {};
  if ((states[inView] ?? "not_sent") !== "not_sent") return false;
  return Object.entries(states).some(([shop, s]) => shop !== inView && s !== "not_sent");
}

/** The listing's SKU in the shop in view (its own there, else the group's). */
export function skuIn(data: BatchShops | null, contentId: string | null | undefined, inView: string | null, fallback: string | null): string | null {
  if (!data || !contentId || !inView) return fallback;
  const row = data.listings.find((l) => l.content_id === contentId);
  return row && inView in row.skus ? row.skus[inView] ?? null : fallback;
}

/** The profile chosen for a listing in the shop in view only, else null. */
export function profileIn(data: BatchShops | null, contentId: string | null | undefined, inView: string | null): string | null {
  if (!data || !contentId || !inView) return null;
  return data.listings.find((l) => l.content_id === contentId)?.profiles[inView] ?? null;
}

// --- Text for the target shop (the dialog's text step, the review card per shop) --------------

// The effective text per shop is lib/review.ts::textFor (one helper; kept there so modules with
// no runtime imports can use it under the test runner).
export type { ShopText } from "./review";

const words = (text: string): string[] => text.split(",").map((w) => w.trim()).filter(Boolean);
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** "Add these tags": each tag not there yet (any case), at the end. More than Etsy's 13 is
 *  kept and said by the rule check, never dropped silently. */
export function addTagsTo(tags: string[], text: string): string[] {
  const seen = new Set(tags.map((t) => t.toLowerCase()));
  const out = [...tags];
  for (const t of words(text)) if (!seen.has(t.toLowerCase())) (out.push(t), seen.add(t.toLowerCase()));
  return out;
}

/** "Remove these tags" (any case). */
export function removeTagsFrom(tags: string[], text: string): string[] {
  const gone = new Set(words(text).map((t) => t.toLowerCase()));
  return tags.filter((t) => !gone.has(t.trim().toLowerCase()));
}

/** "Add these words" to the end of a title (a word already in it is not added again). */
export function addWordsTo(title: string, text: string): string {
  const have = title.toLowerCase();
  const extra = words(text).filter((w) => !new RegExp(`(^|[^\\p{L}\\p{N}])${escape(w.toLowerCase())}($|[^\\p{L}\\p{N}])`, "u").test(have));
  return extra.length ? `${title.trim()} ${extra.join(" ")}`.trim() : title;
}

/** "Remove these words" from a title: whole words, any case; spaces and commas tidied. */
export function removeWordsFrom(title: string, text: string): string {
  let out = title;
  for (const w of words(text)) out = out.replace(new RegExp(`(^|[^\\p{L}\\p{N}])${escape(w)}(?=$|[^\\p{L}\\p{N}])`, "giu"), "$1");
  return out.replace(/\s+,/g, ",").replace(/,(\s*,)+/g, ",").replace(/\s{2,}/g, " ").replace(/^[\s,]+|[\s,]+$/g, "");
}

/** Find and replace in a title (any case, every match); empty ``find`` changes nothing. */
export function replaceIn(title: string, find: string, replace: string): string {
  if (!find) return title;
  return title.replace(new RegExp(escape(find), "gi"), replace).replace(/\s{2,}/g, " ").trim();
}

// --- The batch page's shop workspace -------------------------------------------------------

type WorkspaceRow = import("./types").AnotherShopPreview["rows"][number];

/** The status chip's tone for a listing in the shop being worked on. */
export function stateTone(state: ShopState): "neutral" | "working" | "done" | "attention" {
  return state === "published" ? "done" : state === "scheduled" ? "working" : state === "drafted" ? "attention" : "neutral";
}

/** One colour per shop (by its place in the account's list), on the bar and on every row, so
 *  it is plain which shop the edits are for. Text on these is 7:1 or more. */
const SHOP_COLOURS = [
  "border-sky-300 bg-sky-50 text-sky-900",
  "border-violet-300 bg-violet-50 text-violet-900",
  "border-amber-300 bg-amber-50 text-amber-900",
  "border-emerald-300 bg-emerald-50 text-emerald-900",
  "border-rose-300 bg-rose-50 text-rose-900",
  "border-teal-300 bg-teal-50 text-teal-900",
];
export function shopColour(index: number): string {
  return SHOP_COLOURS[((index % SHOP_COLOURS.length) + SHOP_COLOURS.length) % SHOP_COLOURS.length];
}

/** A row can be selected for sending: it is not in that shop yet. */
export const selectable = (row: Pick<WorkspaceRow, "state">): boolean => row.state !== "there";

/** "Send N to <shop>": what goes (profiles used, SKUs, rows with their own text) and what will
 *  not, with why, from the server's preview of exactly those rows. */
export function sendSummary(preview: { rows: WorkspaceRow[]; refused: string[] }, chosen: string[]) {
  const rows = preview.rows.filter((r) => chosen.includes(r.content_id));
  const going = rows.filter((r) => r.state === "new" && r.errors.length === 0);
  const profiles = new Map<string, number>();
  for (const r of going) profiles.set(r.profile_name ?? "no profile", (profiles.get(r.profile_name ?? "no profile") ?? 0) + 1);
  const notGoing = rows
    .filter((r) => !going.includes(r))
    .map((r) => `${r.group_key || r.title.slice(0, 30)}: ${r.errors.length ? `Etsy would refuse its text (${r.errors[0]})` : r.reason ?? "cannot go"}`);
  return {
    going: going.length,
    profiles: [...profiles.entries()],
    skus: going.map((r) => r.sku ?? "no SKU"),
    ownText: going.filter((r) => r.text_own.length > 0).length,
    ownSku: going.filter((r) => r.sku_own).length,
    notGoing: [...notGoing, ...preview.refused],
  };
}
