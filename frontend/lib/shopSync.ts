/**
 * When a shop's listing sync ends, every page showing that shop's listings updates by itself
 * (fix/onboarding-followups, Part I).
 *
 * Before: each page guessed. Profiles looked again at 5 and 15 seconds after "Sync shop listings" and
 * took the old copy for the synced one; a shop with no listings said "0 · syncing…" for good; Home
 * gave up after a few tries. Now the server says whether a sync runs and when one ended
 * (`GET /api/shop/sync`, backend/app/etsy/shop_sync.py). One watcher in the app frame
 * (components/ShopSyncWatcher.tsx) follows it while any shop syncs and announces each sync that
 * ended; the pages listen (`onShopSynced`) and read again. Nothing is kept in the browser.
 */
import type { ShopSyncState } from "./types";

/** A page saw a sync start (it pressed Sync, or the server said "syncing"): follow it now. */
export const SYNC_STARTED = "listyro:shop-sync-started";
/** A shop's sync ended; `detail` is the shop's id. */
export const SHOP_SYNCED = "listyro:shop-synced";

export const POLL_MS = 3000;
/** Stop following after this long, whatever the server says (a worker died; the mark runs out). */
export const FOLLOW_FOR_MS = 20 * 60 * 1000;

/** The shops whose sync ended between two reads: was syncing and no longer is, or ended again. */
export function finished(before: ShopSyncState[], now: ShopSyncState[]): string[] {
  const prev = new Map(before.map((s) => [s.connection_id, s]));
  return now
    .filter((s) => {
      const p = prev.get(s.connection_id);
      if (!p || s.syncing) return false;
      return p.syncing || (s.ended_at ?? null) !== (p.ended_at ?? null);
    })
    .map((s) => s.connection_id);
}

export const anySyncing = (states: ShopSyncState[]): boolean => states.some((s) => s.syncing);

/** Tell the watcher to follow (no-op outside the browser). */
export function followSync(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(SYNC_STARTED));
}

/** Call `read(shopId)` whenever a shop's sync ends; returns the unsubscribe. */
export function onShopSynced(read: (shopId: string) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = (e: Event) => read(String((e as CustomEvent<string>).detail));
  window.addEventListener(SHOP_SYNCED, handler);
  return () => window.removeEventListener(SHOP_SYNCED, handler);
}

export function announceSynced(shopId: string): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(SHOP_SYNCED, { detail: shopId }));
}
