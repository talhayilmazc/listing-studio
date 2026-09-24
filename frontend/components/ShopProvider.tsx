"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Shop, ShopsOut } from "@/lib/types";

/**
 * The account's connected shops and the one currently selected (v5 §E).
 *
 * Profiles, the shop's listings and the quota figures follow the selected shop;
 * batches and uploads do not (designs belong to the account, not to a shop).
 * The selection is remembered per browser as a convenience, and falls back to
 * the first shop when the remembered one is gone.
 */

const STORAGE_KEY = "listyro.shop";

interface ShopValue {
  /** null until the first answer. */
  shops: Shop[] | null;
  slots: ShopsOut["slots"] | null;
  selected: Shop | null;
  select: (id: string) => void;
  refresh: () => Promise<void>;
}

const ShopContext = createContext<ShopValue>({
  shops: null,
  slots: null,
  selected: null,
  select: () => {},
  refresh: async () => {},
});

export const useShops = () => useContext(ShopContext);

function remembered(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function ShopProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<ShopsOut | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await api.shops());
    } catch {
      setData({ shops: [], slots: { used: 0, limit: 0, app_used: 0, app_limit: 0, can_add: false } });
    }
  }, []);

  useEffect(() => {
    setSelectedId(remembered());
    refresh();
  }, [refresh]);

  const select = useCallback((id: string) => {
    setSelectedId(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Storage can be unavailable (private windows); the choice just is not remembered.
    }
  }, []);

  const value = useMemo<ShopValue>(() => {
    const shops = data?.shops ?? null;
    const selected = shops ? shops.find((s) => s.id === selectedId) ?? shops[0] ?? null : null;
    return { shops, slots: data?.slots ?? null, selected, select, refresh };
  }, [data, selectedId, select, refresh]);

  return <ShopContext.Provider value={value}>{children}</ShopContext.Provider>;
}
