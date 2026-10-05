"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Profile, ProfileSetup, ShopGroups, UseInScope } from "@/lib/types";
import { useShops } from "./ShopProvider";
import { Txt } from "./Txt";

/**
 * Where a profile is used (v8 §C): its main shop (the reference listing is
 * there) and every shop it is set up in, each with what is missing and why.
 * "Use in" sets it up in all shops, a group, or chosen shops in one go: exact
 * names and identical terms are linked at once; anything else waits here for
 * the seller, who confirms what to create (with its Etsy requests) or picks one
 * of that shop's own settings. Nothing is created without that confirmation.
 */
export function ProfileShops({ profile, onChange }: { profile: Profile; onChange: (p: Profile) => void }) {
  const { shops } = useShops();
  const [groups, setGroups] = useState<ShopGroups | null>(null);
  const [setup, setSetup] = useState<ProfileSetup | null>(null);
  const [open, setOpen] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [creating, setCreating] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const links = profile.links ?? [];
  const checking = links.some((l) => l.status === "checking");
  const others = (shops ?? []).filter((s) => !links.some((l) => l.connection_id === s.id));
  const needsWork = links.filter((l) => !l.main && !l.ready && l.status !== "checking");

  const loadSetup = useCallback(async () => {
    try {
      const s = await api.profileSetup(profile.id);
      setSetup(s);
      onChange(s.profile);
      return s;
    } catch (e: any) {
      setError(String(e.message ?? e));
      return null;
    }
  }, [profile.id, onChange]);

  // While a shop is being set up, look again every few seconds (it takes a minute at most).
  useEffect(() => {
    if (!checking) return;
    timer.current = setTimeout(loadSetup, 4000);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [checking, loadSetup, links]);

  useEffect(() => {
    if (open && groups === null) api.shopGroups().then(setGroups).catch(() => setGroups({ groups: [], ungrouped: [] }));
    if (open && needsWork.length > 0 && setup === null) loadSetup();
  }, [open, groups, needsWork.length, setup, loadSetup]);

  async function useIn(scope: UseInScope) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const res = await api.useProfileIn(profile.id, scope);
      onChange(res.profile);
      setChoosing(false);
      setPicked([]);
      setNote(res.started.length ? `Setting it up in ${res.started.length} shop${res.started.length === 1 ? "" : "s"}…` : "Already set up there.");
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const toCreate = (setup?.shops ?? []).flatMap((s) =>
    s.open.filter((o) => o.creatable && creating[`${s.connection_id}:${o.resource}`]).map((o) => ({ shop: s, item: o })),
  );
  const requests = toCreate.reduce((n, x) => n + x.item.requests, 0);

  async function confirmCreate() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.createInShops(
        profile.id,
        toCreate.map((x) => ({ connection_id: x.shop.connection_id, resource: x.item.resource })),
      );
      onChange(res.profile);
      setCreating({});
      setSetup(null);
      setNote(`Creating ${toCreate.length} setting${toCreate.length === 1 ? "" : "s"} (${res.requests} Etsy requests)…`);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function pick(shop: string, resource: string, value: string) {
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      onChange(await api.pickInShop(profile.id, shop, resource, [Number(value)]));
      await loadSetup();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function checkAgain(shop: string) {
    setBusy(true);
    try {
      onChange(await api.checkShopAgain(profile.id, shop));
      setSetup(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function stop(shop: string) {
    setBusy(true);
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
        <button type="button" className="tap text-brand-700 underline" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
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
          </li>
        ))}
      </ul>

      {open && (
        <div key="use-in" className="mt-3 space-y-3 border-t border-slate-100 pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-600">Use in</span>
            <button type="button" className="btn-secondary px-2 py-1 text-xs" disabled={busy} onClick={() => useIn({ scope: "all" })}>
              All shops
            </button>
            {(groups?.groups ?? []).map((g) => (
              <button key={g.id} type="button" className="btn-secondary px-2 py-1 text-xs" disabled={busy || g.shops.length === 0}
                onClick={() => useIn({ scope: "group", group_id: g.id })}>
                <span translate="no">{g.name}</span>
              </button>
            ))}
            <button type="button" className="tap text-brand-700 underline" onClick={() => setChoosing((c) => !c)} disabled={others.length === 0}>
              Chosen shops…
            </button>
          </div>
          {choosing && (
            <div key="choose" className="space-y-1">
              {others.map((s) => (
                <label key={s.id} className="flex min-h-[2.75rem] items-center gap-2 sm:min-h-0">
                  <input type="checkbox" className="h-4 w-4" checked={picked.includes(s.id)}
                    onChange={(e) => setPicked((cur) => (e.target.checked ? [...cur, s.id] : cur.filter((x) => x !== s.id)))} />
                  <span translate="no">{s.name}</span>
                </label>
              ))}
              <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={busy || picked.length === 0}
                onClick={() => useIn({ scope: "shops", connection_ids: picked })}>
                <span translate="no">{picked.length ? `Set up in ${picked.length}` : "Set up"}</span>
              </button>
            </div>
          )}
          <p className="text-slate-500">
            Settings with the same name (or the same terms) in a shop are linked at once. Anything else is listed below:
            create a copy there, or choose one of that shop&apos;s own. Nothing is created without your confirmation.
          </p>

          {(setup?.shops ?? []).filter((s) => s.open.length > 0 || s.choices_expired).map((s) => (
            <div key={s.connection_id} className="rounded border border-amber-200 bg-amber-50/60 p-2">
              <p translate="no" className="font-medium text-slate-800">{s.shop_name}</p>
              {s.choices_expired ? (
                <button key="again" type="button" className="tap mt-1 text-brand-700 underline" disabled={busy} onClick={() => checkAgain(s.connection_id)}>
                  <span>Check </span><span translate="no">{s.shop_name}</span><span> again</span>
                </button>
              ) : (
                <ul key="open" className="mt-1 space-y-2">
                  {s.open.map((o) => (
                    <li key={o.resource} className="space-y-1">
                      <p>
                        <span className="font-medium">{o.label}</span>
                        <span className="text-slate-500"><span>: </span><Txt>{o.reason}</Txt></span>
                      </p>
                      {o.creatable && (
                        <label key="create" className="flex min-h-[2.75rem] items-start gap-2 sm:min-h-0">
                          <input type="checkbox" className="mt-0.5 h-4 w-4"
                            checked={!!creating[`${s.connection_id}:${o.resource}`]}
                            onChange={(e) => setCreating((c) => ({ ...c, [`${s.connection_id}:${o.resource}`]: e.target.checked }))} />
                          <span>
                            <span>Create it in </span><span translate="no">{s.shop_name}</span><span>: </span>
                            <Txt>{o.create_summary}</Txt>
                            <span translate="no" className="text-slate-500">{` (${o.requests} Etsy request${o.requests === 1 ? "" : "s"})`}</span>
                          </span>
                        </label>
                      )}
                      {o.options.length > 0 && (
                        <select key="pick" className="field py-1 text-xs" defaultValue="" disabled={busy}
                          onChange={(e) => pick(s.connection_id, o.resource, e.target.value)}
                          aria-label={`${o.label} in ${s.shop_name ?? "this shop"}`}>
                          <option value="">{o.creatable ? "…or choose one of this shop's" : "Choose one of this shop's"}</option>
                          {o.options.map((opt) => <option key={opt.id} value={opt.id}>{opt.label}</option>)}
                        </select>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <button type="button" className="tap mt-1 text-slate-500 underline" disabled={busy} onClick={() => stop(s.connection_id)}>
                <span>Stop using it in </span><span translate="no">{s.shop_name}</span>
              </button>
            </div>
          ))}

          {toCreate.length > 0 && (
            <div key="confirm" className="flex flex-wrap items-center gap-2">
              <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={busy} onClick={confirmCreate}>
                <span translate="no">{`Create ${toCreate.length} in Etsy`}</span>
              </button>
              <span translate="no" className="text-slate-500">{`${requests} Etsy requests, counted against today's`}</span>
            </div>
          )}
        </div>
      )}
      {note && <p key="note" translate="no" className="mt-2 text-slate-500">{note}</p>}
      {error && <p key="error" className="mt-2 text-rose-600">{error}</p>}
    </div>
  );
}
