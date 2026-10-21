/**
 * Saving what is typed on a review card, as it is typed.
 *
 * Production (2026-10-21): a seller wrote listings by hand on the review page.
 * A card's text reached the server only through its own "Save changes" or its
 * own approve tick; "Approve all" at the top, a reload and leaving the page
 * all went ahead with whatever the server had, so typed text could be lost
 * without a word. Now:
 *
 * - a card saves by itself shortly after typing stops (`SAVE_AFTER_MS`);
 * - "Approve all", "Create drafts" and "Generate the empty ones" first wait for
 *   every card on the page to save (`flushAll`);
 * - leaving the page (another page in the app, a reload, closing the tab) sends
 *   what is unsaved with `keepalive`, and the browser asks first while a save is
 *   still on its way;
 * - a save sends only the fields that changed, with what the card last read for
 *   them (`base`): a field another tab or window changed since is refused by the
 *   server (409) instead of being overwritten, and the card says so.
 */
export const SAVE_AFTER_MS = 800;

export interface CardText {
  title: string;
  tags: string[];
  description: string;
}

export type TextPatch = Partial<CardText> & { base: Partial<CardText> };

/** What to send: the fields that differ from what the server last had, each with
 * that value as its base. None when nothing changed. */
export function patchFor(current: CardText, saved: CardText): TextPatch | null {
  const patch: TextPatch = { base: {} };
  let changed = false;
  if (current.title !== saved.title) {
    patch.title = current.title;
    patch.base.title = saved.title;
    changed = true;
  }
  if (JSON.stringify(current.tags) !== JSON.stringify(saved.tags)) {
    patch.tags = current.tags;
    patch.base.tags = saved.tags;
    changed = true;
  }
  if (current.description !== saved.description) {
    patch.description = current.description;
    patch.base.description = saved.description;
    changed = true;
  }
  return changed ? patch : null;
}

/**
 * After a save came back, what the card shows: per field, what was typed while the
 * save was on its way stays (it is saved next); otherwise the server's value, which
 * may differ from what was sent (a hand-written listing's description follows its
 * title). `before` is what the server had when the save was sent.
 */
export function afterSave(sent: TextPatch, server: CardText, onCard: CardText, before: CardText): CardText {
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  const pick = <K extends keyof CardText>(key: K): CardText[K] => {
    const typedSince = key in sent ? !same(onCard[key], sent[key]) : !same(onCard[key], before[key]);
    return typedSince ? onCard[key] : server[key];
  };
  return { title: pick("title"), tags: pick("tags"), description: pick("description") };
}

interface Entry {
  /** ``keepalive``: the page is being left; the request must outlive it. */
  flush: (keepalive?: boolean) => Promise<unknown>;
  unsaved: () => boolean;
}

const cards = new Map<string, Entry>();

/** A card on the page; returns the function that takes it off again. */
export function registerCard(id: string, entry: Entry): () => void {
  cards.set(id, entry);
  return () => {
    if (cards.get(id) === entry) cards.delete(id);
  };
}

/** Wait until every card on the page has saved what is typed on it. */
export async function flushAll(keepalive = false): Promise<void> {
  await Promise.all([...cards.values()].map((c) => c.flush(keepalive).catch(() => undefined)));
}

/** A card still has text the server does not have (typed, or on its way). */
export function anyUnsaved(): boolean {
  return [...cards.values()].some((c) => c.unsaved());
}
