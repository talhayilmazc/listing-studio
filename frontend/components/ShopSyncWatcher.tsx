"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";
import { FOLLOW_FOR_MS, POLL_MS, SYNC_STARTED, announceSynced, anySyncing, finished } from "@/lib/shopSync";
import type { ShopSyncState } from "@/lib/types";

/**
 * Follows the account's shop syncs while any runs and tells every page when one ends (lib/shopSync.ts,
 * Part I). It looks once when the app opens and again whenever a page says a sync started; while
 * nothing syncs it asks nothing.
 */
export function ShopSyncWatcher() {
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let last: ShopSyncState[] = [];
    let until = 0;
    let busy = false;
    let again = false; // asked to follow while a look was under way: look once more after it

    const look = async () => {
      timer = undefined;
      busy = true;
      let now: ShopSyncState[];
      try {
        now = await api.shopSync();
      } catch {
        now = last; // offline for a moment: keep following
      } finally {
        busy = false;
      }
      if (!alive) return;
      for (const id of finished(last, now)) announceSynced(id);
      last = now;
      if (again) {
        again = false;
        look();
      } else if (anySyncing(now) && Date.now() < until) timer = setTimeout(look, POLL_MS);
    };
    const follow = () => {
      until = Date.now() + FOLLOW_FOR_MS;
      if (busy) again = true;
      else if (timer === undefined) look();
    };

    follow();
    window.addEventListener(SYNC_STARTED, follow);
    return () => {
      alive = false;
      if (timer !== undefined) clearTimeout(timer);
      window.removeEventListener(SYNC_STARTED, follow);
    };
  }, []);
  return null;
}
