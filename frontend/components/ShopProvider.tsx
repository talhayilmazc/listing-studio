"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { ALL, resolveScope } from "@/lib/shopScope";
import type { Shop, ShopsOut } from "@/lib/types";

/**
 * The account's connected shops and the one currently selected (v5 §E).
 *
 * "All shops" (the default with several shops) is what Overview, Analytics,
 * Batches and Scheduled show: every shop's total and a row per shop. Picking
 * one shop in the switcher narrows them to it. Pages that work on one shop
 * (profiles, uploads, a shop's listings) use ``selected``: the shop picked last,
 * or the first. The choice is remembered per browser as a convenience.
 */

const STORAGE_KEY = "listyro.shop";
const SINGLE_KEY = "listyro.shop.single";

interface ShopValue {
  /** null until the first answer. */
  shops: Shop[] | null;
  slots: ShopsOut["slots"] | null;
  /** The operator's notice while new shop connections are paused, else null. */
  paused: string | null;
  /** One shop, for pages that work on one: the shop picked last, else the first. */
  selected: Shop | null;
  /** "All shops" is chosen in the switcher (only with several shops). */
  allShops: boolean;
  /** For pages that can total every shop: null = all shops, else the one picked. */
  scope: Shop | null;
  select: (id: string) => void;
  selectAll: () => void;
  refresh: () => Promise<void>;
  /** Why the shops could not be read, or null. The shops stay as last read (null at first):
   *  never an empty list, which would make every page treat a seller with shops as one without. */
  error: string | null;
}

const ShopContext = createContext<ShopValue>({
  shops: null,
  slots: null,
  paused: null,
  selected: null,
  allShops: false,
  scope: null,
  select: () => {},
  selectAll: () => {},
  refresh: async () => {},
  error: null,
});

export const useShops = () => useContext(ShopContext);

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable (private windows); the choice just is not remembered.
  }
}

export function ShopProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<ShopsOut | null>(null);
  const [remembered, setRemembered] = useState<string | null>(null);
  const [lastSingle, setLastSingle] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await api.shops());
      setError(null);
    } catch (e: any) {
      // Quality pass 2026-10: this used to set an empty list, so Overview offered "Connect a shop"
      // and the Shops page looked empty to a seller whose shops were only not read this time.
      setError(e?.message ?? String(e));
    }
  }, []);

  useEffect(() => {
    setRemembered(read(STORAGE_KEY));
    setLastSingle(read(SINGLE_KEY));
    refresh();
  }, [refresh]);

  const select = useCallback((id: string) => {
    setRemembered(id);
    setLastSingle(id);
    write(STORAGE_KEY, id);
    write(SINGLE_KEY, id);
  }, []);

  const selectAll = useCallback(() => {
    setRemembered(ALL);
    write(STORAGE_KEY, ALL);
  }, []);

  const value = useMemo<ShopValue>(() => {
    const shops = data?.shops ?? null;
    const { allShops, selected } = shops ? resolveScope(shops, remembered, lastSingle) : { allShops: false, selected: null };
    const paused = data?.connections_paused ? data.pause_notice ?? "New shop connections are paused." : null;
    return {
      shops, slots: data?.slots ?? null, paused, selected, allShops,
      scope: allShops ? null : selected, select, selectAll, refresh, error,
    };
  }, [data, remembered, lastSingle, select, selectAll, refresh, error]);

  return <ShopContext.Provider value={value}>{children}</ShopContext.Provider>;
}
