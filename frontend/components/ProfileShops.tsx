"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { announceProfilesChanged, settingsLine, shopState, useInShops } from "@/lib/shopSetup";
import { startSetup } from "@/lib/startSetup";
import type { Profile, ShopGroups } from "@/lib/types";
import { ProfileShopTable } from "./ProfileShopTable";
import { ShopTargetPicker } from "./ShopTargetPicker";
import { useShops } from "./ShopProvider";
import { Txt } from "./Txt";

/**
 * Where a reference listing (profile) is used (v8 §C): its main shop (the reference listing
 * is there) and every other shop. It uses its main shop's shipping profile, return policy and
 * processing profile in every shop, with no question: "Use in" (or drafting in a shop, or
 * connecting one) matches or copies them there at once. Each shop shows "ready", "preparing…",
 * or one plain line when Etsy refused something. At the bottom one line says which settings
 * are used, with "change" to pick another of a shop's own (for every reference listing).
 */
export function ProfileShops({ profile, onChange }: { profile: Profile; onChange: (p: Profile) => void }) {
  const { shops } = useShops();
  const [groups, setGroups] = useState<ShopGroups | null>(null);
  const [open, setOpen] = useState(false);
  const [changing, setChanging] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const links = profile.links ?? [];
  const checking = links.some((l) => l.status === "checking");
  const candidates = useInShops(shops ?? [], links);
  const others = links.filter((l) => !l.main);
  const mainShop = links.find((l) => l.main)?.shop_name ?? profile.shop_name;

  const reload = useCallback(async () => {
    try {
      onChange((await api.profileSetup(profile.id)).profile);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [profile.id, onChange]);

  // While a shop is being prepared, look again every few seconds (it takes a minute at most).
  useEffect(() => {
    if (!checking) return;
    timer.current = setTimeout(reload, 4000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [checking, reload, links]);

  // A shop that finished preparing here may have set up other reference listings too (a copy
  // made, a choice changed): every card on the page reads itself again.
  const wasChecking = useRef(checking);
  useEffect(() => {
    if (wasChecking.current && !checking) announceProfilesChanged();
    wasChecking.current = checking;
  }, [checking]);

  useEffect(() => {
    if (open && groups === null) api.shopGroups().then(setGroups).catch(() => setGroups({ groups: [], ungrouped: [] }));
  }, [open, groups]);

  async function setUp(shopIds: string[]) {
    if (!shopIds.length) return;
    setBusy(true);
    setError(null);
    try {
      await startSetup([{ profileId: profile.id, profileName: profile.name, shopIds }]);
      setPicked([]);
      setOpen(false);
      await reload();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function stop(shop: string) {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.stopUsingInShop(profile.id, shop));
      announceProfilesChanged();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-slate-200 p-3 text-xs" data-testid="profile-shops">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-slate-700">Shops</span>
        <button type="button" className="tap text-brand-700 underline" onClick={() => setOpen((o) => !o)} aria-expanded={open}
          disabled={!profile.confirmed} title={profile.confirmed ? undefined : "Confirm the profile first"}>
          {open ? "Close" : "Use in…"}
        </button>
      </div>
      <ul className="mt-1.5 space-y-1">
        {links.map((l) => {
          const state = shopState(l);
          return (
            <li key={l.connection_id} className="flex flex-wrap items-baseline gap-x-2" data-testid="profile-shop">
              <span translate="no" className="font-medium text-slate-700">{l.shop_name}</span>
              {state.kind === "main" ? (
                <span key="main" className="rounded bg-slate-100 px-1 text-[10px] text-slate-600">main · reference listing</span>
              ) : state.kind === "ready" ? (
                <span key="ready" className="text-emerald-700">ready</span>
              ) : state.kind === "preparing" ? (
                <span key="preparing" translate="no" className="text-slate-600">preparing…</span>
              ) : (
                <span key="problem" className="basis-full text-amber-800" data-testid="profile-shop-problem"><Txt>{state.text}</Txt></span>
              )}
              {!l.main && l.status !== "checking" && (
                <button key="stop" type="button" className="tap text-slate-500 underline" disabled={busy} onClick={() => stop(l.connection_id)}>
                  stop using here
                </button>
              )}
            </li>
          );
        })}
      </ul>

      {open && (
        <div key="use-in" className="mt-3 space-y-3 border-t border-slate-100 pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-600">Use in</span>
            <button type="button" className="btn-secondary px-2 py-1 text-xs" disabled={busy || candidates.length === 0}
              onClick={() => setUp(candidates.map((c) => c.id))}>
              All shops
            </button>
            <span className="text-slate-500">or choose a group or shops:</span>
          </div>
          <ShopTargetPicker shops={candidates} groups={groups?.groups ?? []} value={picked} onChange={setPicked} label="Use in"
            allShops />
          <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={busy || picked.length === 0}
            onClick={() => setUp(picked)} data-testid="use-in-chosen">
            <span translate="no">{picked.length ? `Use in ${picked.length} shop${picked.length === 1 ? "" : "s"}` : "Use in"}</span>
          </button>
        </div>
      )}

      <p className="mt-2 border-t border-slate-100 pt-2 text-slate-600" data-testid="settings-line">
        <span translate="no">{settingsLine(mainShop)}</span>
        {links.length > 0 && (
          <span key="change">
            <span> · </span>
            <button type="button" className="tap text-brand-700 underline" onClick={() => setChanging((c) => !c)}
              aria-expanded={changing} data-testid="settings-change">
              {changing ? "close" : "change"}
            </button>
          </span>
        )}
      </p>
      {changing && links.length > 0 && (
        // B7: one row per shop: shipping, returns, processing, section, title prefix, a day.
        <ProfileShopTable key="table" profileId={profile.id} profileName={profile.name} onChanged={reload} onClose={() => setChanging(false)}
          howMade={{ production: profile.how_made_production ?? null, tools: profile.how_made_tools ?? null }} />
      )}
      {error && <p key="error" className="mt-2 text-rose-700">{error}</p>}
    </div>
  );
}
