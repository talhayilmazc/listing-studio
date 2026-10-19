"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useInShops } from "@/lib/shopSetup";
import type { Profile, ShopGroups } from "@/lib/types";
import { ShopSetupDialog, type SetupItem } from "./ShopSetupDialog";
import { ShopTargetPicker } from "./ShopTargetPicker";
import { useShops } from "./ShopProvider";
import { Txt } from "./Txt";

/**
 * Where a profile is used (v8 §C): its main shop (the reference listing is
 * there) and every shop it is set up in, each with what is missing and why.
 * "Use in" sets it up in all shops, in a group, or in the shops chosen: the setup
 * dialog reads each shop's own settings, matches the ones with the same name or
 * the same terms, preselects a copy where a shop has none, and creates nothing
 * until the seller confirms.
 */
export function ProfileShops({ profile, onChange }: { profile: Profile; onChange: (p: Profile) => void }) {
  const { shops } = useShops();
  const [groups, setGroups] = useState<ShopGroups | null>(null);
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [dialog, setDialog] = useState<SetupItem[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const links = profile.links ?? [];
  const checking = links.some((l) => l.status === "checking");
  const candidates = useInShops(shops ?? [], links);
  const needsWork = links.filter((l) => !l.main && !l.ready && l.status !== "checking");

  const reload = useCallback(async () => {
    try {
      onChange((await api.profileSetup(profile.id)).profile);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [profile.id, onChange]);

  // While a shop is being set up, look again every few seconds (it takes a minute at most).
  useEffect(() => {
    if (!checking || dialog) return;
    timer.current = setTimeout(reload, 4000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [checking, reload, links, dialog]);

  useEffect(() => {
    if (open && groups === null) api.shopGroups().then(setGroups).catch(() => setGroups({ groups: [], ungrouped: [] }));
  }, [open, groups]);

  const setUp = (shopIds: string[]) => {
    if (shopIds.length) setDialog([{ profileId: profile.id, profileName: profile.name, shopIds }]);
  };

  async function stop(shop: string) {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.stopUsingInShop(profile.id, shop));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-slate-200 p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-slate-700">Shops</span>
        <button type="button" className="tap text-brand-700 underline" onClick={() => setOpen((o) => !o)} aria-expanded={open}
          disabled={!profile.confirmed} title={profile.confirmed ? undefined : "Confirm the profile first"}>
          {open ? "Close" : "Use in…"}
        </button>
      </div>
      <ul className="mt-1.5 space-y-1">
        {links.map((l) => (
          <li key={l.connection_id} className="flex flex-wrap items-baseline gap-x-2">
            <span translate="no" className="font-medium text-slate-700">{l.shop_name}</span>
            {l.main ? (
              <span key="main" className="rounded bg-slate-100 px-1 text-[10px] text-slate-500">main · reference listing</span>
            ) : l.ready ? (
              <span key="ready" className="text-emerald-700">set up</span>
            ) : l.status === "checking" ? (
              <span key="checking" translate="no" className="text-slate-500">setting up…</span>
            ) : (
              <span key="missing" className="text-amber-800"><Txt>{l.reason}</Txt></span>
            )}
            {!l.main && l.status !== "checking" && (
              <button key="stop" type="button" className="tap text-slate-400 underline" disabled={busy} onClick={() => stop(l.connection_id)}>
                stop using here
              </button>
            )}
          </li>
        ))}
      </ul>
      {needsWork.length > 0 && (
        <button key="finish" type="button" className="btn-secondary mt-2 px-2 py-1 text-xs" disabled={busy || !profile.confirmed}
          onClick={() => setUp(needsWork.map((l) => l.connection_id))} data-testid="finish-setup">
          <span translate="no">{`Finish setup in ${needsWork.length} shop${needsWork.length === 1 ? "" : "s"}`}</span>
        </button>
      )}

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
          <ShopTargetPicker shops={candidates} groups={groups?.groups ?? []} value={picked} onChange={setPicked} label="Use in" />
          <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={busy || picked.length === 0}
            onClick={() => setUp(picked)} data-testid="use-in-chosen">
            <span translate="no">{picked.length ? `Set up in ${picked.length} shop${picked.length === 1 ? "" : "s"}` : "Set up"}</span>
          </button>
          <p className="text-slate-500">
            Each shop keeps its own shipping, return and processing settings: the ones with the same name (or the same
            terms) are used automatically; where a shop has none, a copy of this profile&apos;s main shop&apos;s is
            offered. Nothing is created without your confirmation.
          </p>
        </div>
      )}
      {error && <p key="error" className="mt-2 text-rose-600">{error}</p>}
      {dialog && (
        <ShopSetupDialog key="dialog" items={dialog} onClose={() => {
          setDialog(null);
          setPicked([]);
          reload();
        }} />
      )}
    </div>
  );
}
