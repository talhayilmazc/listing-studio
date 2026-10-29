/**
 * Which drafts on Etsy carry another SKU than the one their listing should have there.
 *
 * One read for the whole page (``GET /batches/{id}/sku-etsy``), shared by every SKU editor on
 * it and kept until a SKU is saved or sent: never a POST, never one request per row on each
 * render. (Each editor used to POST ``/content/{id}/sku/etsy`` when it appeared: 15 at once on
 * one page load. They wrote nothing, but every check must be a read.)
 */

export interface SkuDraft {
  connection_id: string;
  shop_name?: string | null;
  etsy_listing_id: number;
  current_sku?: string | null;
  /** The SKU it should have in that shop: what "Update it" writes. */
  sku?: string | null;
}

export interface SkuOnEtsy {
  listings: { content_id: string; shops: SkuDraft[] }[];
}

/** How long a batch's answer is reused without asking again (saving a SKU asks at once). */
export const KEEP_MS = 60_000;

type Listener = () => void;
const EMPTY: SkuDraft[] = [];

/** A cache of one answer per batch; ``fetch`` is the GET (injected, so tests need no network). */
export function skuStore(fetch: (batchId: string) => Promise<SkuOnEtsy>, now: () => number = Date.now) {
  const answers = new Map<string, { at: number; byContent: Map<string, SkuDraft[]> }>();
  const going = new Map<string, Promise<void>>();
  const listeners = new Set<Listener>();
  const tell = () => listeners.forEach((l) => l());

  function load(batchId: string): Promise<void> {
    const on = going.get(batchId);
    if (on) return on;
    const started = fetch(batchId)
      .then((res) => {
        answers.set(batchId, { at: now(), byContent: new Map(res.listings.map((l) => [l.content_id, l.shops])) });
      })
      .catch(() => {
        answers.set(batchId, { at: now(), byContent: new Map() });
      })
      .finally(() => {
        going.delete(batchId);
        tell();
      });
    going.set(batchId, started);
    return started;
  }

  return {
    /** The listing's drafts with another SKU, as last read (stable between reads; asks nothing). */
    drafts(batchId: string, contentId: string): SkuDraft[] {
      return answers.get(batchId)?.byContent.get(contentId) ?? EMPTY;
    },
    /** Asks once for the whole batch when the answer is missing or old; every editor shares it. */
    ensure(batchId: string): Promise<void> {
      const answer = answers.get(batchId);
      return !answer || now() - answer.at > KEEP_MS ? load(batchId) : Promise.resolve();
    },
    /** A SKU was saved or sent: asks again at once (one read for the whole page). */
    forget(batchId: string): Promise<void> {
      answers.delete(batchId);
      return load(batchId);
    },
    subscribe(listener: Listener): () => void {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** The confirmation before anything is sent: the shops, how many listings, and the SKUs. */
export function confirmText(drafts: SkuDraft[]): string {
  const shops = [...new Set(drafts.map((d) => d.shop_name || "a shop"))];
  const where = shops.length === 1 ? shops[0] : `${shops.slice(0, -1).join(", ")} and ${shops[shops.length - 1]}`;
  const changes = drafts.map((d) => `${d.shop_name || "a shop"}: ${d.current_sku || "no SKU"} → ${d.sku || "no SKU"}`).join("; ");
  const n = drafts.length;
  return `Change the SKU of ${n} listing${n === 1 ? "" : "s"} on Etsy in ${where} (${changes}). `
    + "Only the SKU changes; prices, quantities and variations stay.";
}
