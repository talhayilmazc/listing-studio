"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { announceProfilesChanged, panelSettings } from "@/lib/shopSetup";
import type { ProfileSetup } from "@/lib/types";
import { Txt } from "./Txt";

const POLL_MS = 3000;

/**
 * "change" under a reference listing card: for each other shop, which of that shop's own
 * shipping profiles, return policies and processing profiles is used. A pick is the
 * account's: every reference listing using the same main-shop setting uses it in that shop
 * (pipeline/setting_map.py). Nothing is created or deleted on Etsy here.
 */
export function ShopSettingsPanel({ profileId, onChanged }: { profileId: string; onChanged: () => void }) {
  const [setup, setSetup] = useState<ProfileSetup | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSetup(await api.profileSetup(profileId));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [profileId]);

  useEffect(() => {
    load();
  }, [load]);

  const reading = setup?.shops.some((s) => s.link.status === "checking") ?? false;
  useEffect(() => {
    if (!reading) return;
    const t = setTimeout(load, POLL_MS);
    return () => clearTimeout(t);
  }, [reading, load, setup]);

  async function pick(shop: string, resource: string, id: number) {
    setBusy(true);
    setError(null);
    try {
      await api.pickInShop(profileId, shop, resource, [id]);
      await load();
      onChanged();
      announceProfilesChanged();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function readAgain(shop: string) {
    setBusy(true);
    setError(null);
    try {
      await api.checkShopAgain(profileId, shop);
      await load();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2 space-y-3 rounded-md bg-slate-50 p-2.5" data-testid="settings-panel">
      {setup === null ? (
        <p key="loading" className="text-slate-600">Loading…</p>
      ) : (
        setup.shops.map((shop) => {
          const rows = panelSettings(shop);
          const name = shop.shop_name ?? "this shop";
          return (
            <div key={shop.connection_id} className="space-y-1.5" data-testid="settings-panel-shop">
              <p translate="no" className="font-medium text-slate-800">{name}</p>
              {shop.link.status === "checking" ? (
                <p key="reading" translate="no" className="text-slate-600">preparing…</p>
              ) : rows.length === 0 || rows.every((r) => r.options.length === 0) ? (
                <button key="read" type="button" className="btn-secondary px-2.5 py-1 text-xs" disabled={busy}
                  onClick={() => readAgain(shop.connection_id)}>
                  <span>{`Read ${name}'s settings`}</span>
                </button>
              ) : (
                rows.map((r) => (
                  <label key={r.resource} className="grid gap-1 sm:grid-cols-[9rem_minmax(0,1fr)] sm:items-center sm:gap-3">
                    <span className="text-slate-700">{r.label}</span>
                    <select className="field w-full py-1.5 text-sm" value={r.value ?? ""} disabled={busy}
                      onChange={(e) => e.target.value && pick(shop.connection_id, r.resource, Number(e.target.value))}
                      aria-label={`${r.label} in ${name}`}>
                      {r.value === null && <option key="none" value="">Choose…</option>}
                      {r.options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                    </select>
                  </label>
                ))
              )}
            </div>
          );
        })
      )}
      {error && <p key="error" className="text-rose-700"><Txt>{error}</Txt></p>}
    </div>
  );
}
