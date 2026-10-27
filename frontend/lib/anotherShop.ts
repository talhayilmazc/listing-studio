// A batch's listings in more than one shop: their state per shop, and "Publish in another
// shop" (backend api/another_shop.py, pipeline/shop_listing.py). Pure: no React, no API, so
// the rules are tested on their own (lib/anotherShop.test.ts).

import type { BatchShops, ShopState, SkuMode } from "./types";

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

/** Every listing's SKU in the target shop as the seller types (the live preview). */
export function previewSkus(
  rows: { content_id: string; sku_from: string | null }[],
  mode: SkuMode,
  from: string,
  to: string,
  each: Record<string, string>,
): Record<string, { sku: string; note: string | null }> {
  const out: Record<string, { sku: string; note: string | null }> = {};
  for (const r of rows) {
    const current = r.sku_from ?? "";
    if (mode === "prefix" && (from.trim() || to.trim())) {
      const [sku, matched] = replacePrefix(current, from, to);
      out[r.content_id] = matched ? { sku, note: null } : { sku: current, note: `does not start with ${from.trim()}: kept as it is` };
    } else if (mode === "each" && r.content_id in each) {
      out[r.content_id] = { sku: each[r.content_id].trim(), note: null };
    } else {
      out[r.content_id] = { sku: current, note: null };
    }
  }
  return out;
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
