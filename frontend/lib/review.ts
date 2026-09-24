import type { Content } from "./types";

/**
 * What the review screen's bulk actions can do right now, derived from the
 * listings as they currently are, never from what they were when the page
 * loaded (docs/duzeltmeler-v5.md §C). With several shops (§E), a listing can
 * have a draft in each; "Create drafts" has work where a chosen shop still
 * lacks one, "Publish all" wherever a draft is not yet live.
 */
export interface ReviewActions {
  approved: number;
  /** Approved listings still missing a draft in at least one target shop. */
  toDraft: number;
  /** Approved listings with at least one draft that is not live yet. */
  toPublish: number;
}

/**
 * `targets`: the shops chosen for "Create drafts"; omitted = each listing's own
 * shop (the shop its profile belongs to).
 */
export function reviewActions(items: Content[], targets?: string[]): ReviewActions {
  let approved = 0;
  let toDraft = 0;
  let toPublish = 0;
  for (const c of items) {
    if (!c.approved) continue;
    approved += 1;
    const drafted = new Set(c.publications.map((p) => p.connection_id));
    const wanted = targets ?? (c.connection_id ? [c.connection_id] : []);
    if (wanted.some((shop) => !drafted.has(shop))) toDraft += 1;
    if (c.publications.some((p) => p.state !== "active")) toPublish += 1;
  }
  return { approved, toDraft, toPublish };
}

/** Apply a change one card reports, without refetching the whole list. */
export function applyChange(items: Content[], change: Partial<Content> & { id: string }): Content[] {
  return items.map((c) => (c.id === change.id ? { ...c, ...change } : c));
}

/**
 * Card identity. It changes when any of the listing's drafts changes (a bulk
 * job created one, or published it), so that card remounts with the new
 * state, while cards being edited keep their unsaved text.
 */
export function cardKey(c: Content): string {
  const pubs = c.publications
    .map((p) => `${p.connection_id}=${p.etsy_listing_id}/${p.state}`)
    .sort()
    .join(",");
  return `${c.id}:${pubs}`;
}

/**
 * What the approved drafts still need in Shop Manager (settings Etsy's API cannot
 * make), grouped by setting with how many drafts need it: the reminder shown
 * before "Publish all".
 */
export function pendingManualSteps(
  items: Content[],
): { key: string; label: string; detail: string; drafts: number }[] {
  const byKey = new Map<string, { key: string; label: string; detail: string; drafts: number }>();
  for (const c of items) {
    if (!c.approved) continue;
    for (const p of c.publications) {
      if (p.state === "active") continue;
      for (const s of p.manual_steps ?? []) {
        const row = byKey.get(s.key) ?? { ...s, drafts: 0 };
        row.drafts += 1;
        byKey.set(s.key, row);
      }
    }
  }
  return Array.from(byKey.values());
}
