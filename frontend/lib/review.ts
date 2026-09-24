import type { Content } from "./types";

/**
 * What the review screen's bulk actions can do right now, derived from the
 * listings as they currently are, never from what they were when the page
 * loaded (docs/duzeltmeler-v5.md §C).
 */
export interface ReviewActions {
  approved: number;
  /** Approved, no Etsy draft yet: "Create drafts for all" has work. */
  toDraft: number;
  /** Approved drafts on Etsy, not yet live: "Publish all" has work. */
  toPublish: number;
}

export function reviewActions(items: Content[]): ReviewActions {
  let approved = 0;
  let toDraft = 0;
  let toPublish = 0;
  for (const c of items) {
    if (!c.approved) continue;
    approved += 1;
    if (c.etsy_listing_id == null) toDraft += 1;
    else if (c.etsy_listing_state !== "active") toPublish += 1;
  }
  return { approved, toDraft, toPublish };
}

/** Apply a change one card reports, without refetching the whole list. */
export function applyChange(items: Content[], change: Partial<Content> & { id: string }): Content[] {
  return items.map((c) => (c.id === change.id ? { ...c, ...change } : c));
}

/**
 * Card identity. It changes when the listing's Etsy state changes (a bulk job
 * created the draft, or published it), so that card remounts with the new
 * state, while cards being edited keep their unsaved text.
 */
export function cardKey(c: Content): string {
  return `${c.id}:${c.etsy_listing_id ?? "none"}:${c.etsy_listing_state ?? ""}`;
}
