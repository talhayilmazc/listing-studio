"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import {
  choiceKey, choiceOptions, confirmLabel, defaultChoice, shopChoices, toStart, totals, willUse,
  type Choice,
} from "@/lib/shopSetup";
import type { ProfileSetup, ShopSetup } from "@/lib/types";
import { ShopBadge } from "./ShopPicker";
import { Txt } from "./Txt";

/** One profile to set up in one or more shops. */
export interface SetupItem {
  profileId: string;
  profileName: string | null;
  shopIds: string[];
}

const POLL_MS = 3000;
/** Reading a shop's settings takes seconds; past this, say so instead of spinning. */
const GIVE_UP_MS = 3 * 60_000;

/**
 * Set a profile up in other shops, right where the seller is (the Profiles page,
 * a batch, the review page), for one shop or many in one dialog.
 *
 * Opening it starts the setup in every shop asked for (the same "Use in" the
 * Profiles page uses: each shop's own settings are read, and the ones with the
 * same name or the same terms are matched at once). Then, per shop, every
 * shop-specific setting shows what that shop will use: its matching setting, or,
 * where it has none, a copy of the main shop's, preselected. The seller can
 * change any of them; one confirm sends all the choices, and only then is any
 * copy created. Nothing is created in a shop without that confirm.
 */
export function ShopSetupDialog({
  items,
  onClose,
}: {
  items: SetupItem[];
  /** ``changed``: something was set up; the page reloads what depends on it. */
  onClose: (changed: boolean) => void;
}) {
  const [setups, setSetups] = useState<Record<string, ProfileSetup>>({});
  const [choices, setChoices] = useState<Record<string, Record<string, Choice>>>({});
  const [phase, setPhase] = useState<"reading" | "choosing" | "saving" | "done">("reading");
  const [error, setError] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const [changed, setChanged] = useState(false);
  const started = useRef(false);
  const since = useRef(Date.now());

  const wanted = useCallback((pid: string) => items.find((i) => i.profileId === pid)?.shopIds ?? [], [items]);
  const shopsOf = useCallback(
    (pid: string): ShopSetup[] => (setups[pid]?.shops ?? []).filter((s) => wanted(pid).includes(s.connection_id)),
    [setups, wanted],
  );
  const checking = items.some((i) => {
    const shops = shopsOf(i.profileId);
    return shops.length < i.shopIds.length || shops.some((s) => s.link.status === "checking");
  });

  const load = useCallback(async () => {
    const next: Record<string, ProfileSetup> = {};
    for (const item of items) next[item.profileId] = await api.profileSetup(item.profileId);
    setSetups(next);
    return next;
  }, [items]);

  // Open: start the setup in every shop asked for that is not set up yet (or failed),
  // read again the shops whose choices have expired.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const first = await load();
        let any = false;
        for (const item of items) {
          const { start, again } = toStart(first[item.profileId]?.shops ?? [], item.shopIds);
          if (start.length) {
            await api.useProfileIn(item.profileId, { scope: "shops", connection_ids: start });
            any = true;
          }
          for (const shop of again) {
            await api.checkShopAgain(item.profileId, shop);
            any = true;
          }
        }
        since.current = Date.now();
        if (any) await load();
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    })();
  }, [items, load]);

  // While a shop is being read (or a copy created), look again every few seconds.
  useEffect(() => {
    if (!started.current) return;
    if (!checking) {
      setPhase((p) => (p === "reading" ? "choosing" : p === "saving" ? "done" : p));
      return;
    }
    if (Date.now() - since.current > GIVE_UP_MS) {
      setSlow(true);
      return;
    }
    const t = setTimeout(() => load().catch((e) => setError(String(e.message ?? e))), POLL_MS);
    return () => clearTimeout(t);
  }, [checking, setups, load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && phase !== "saving" && onClose(changed);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, changed, phase]);

  const allShops = items.flatMap((i) => shopsOf(i.profileId));
  const sum = items.reduce(
    (acc, i) => {
      const t = totals(shopsOf(i.profileId), choices[i.profileId] ?? {});
      return { copies: acc.copies + t.copies, requests: acc.requests + t.requests, unresolved: acc.unresolved + t.unresolved,
               ready: acc.ready + t.ready, shops: acc.shops + t.shops };
    },
    { copies: 0, requests: 0, unresolved: 0, ready: 0, shops: 0 },
  );

  async function retry(pid: string, shopId: string) {
    setError(null);
    try {
      await api.useProfileIn(pid, { scope: "shops", connection_ids: [shopId] });
      since.current = Date.now();
      setSlow(false);
      setPhase("reading");
      await load();
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  async function confirm() {
    setPhase("saving");
    setError(null);
    try {
      for (const item of items) {
        const send = shopsOf(item.profileId).flatMap((s) => shopChoices(s, choices[item.profileId] ?? {}));
        if (send.length) await api.applySetup(item.profileId, send);
      }
      setChanged(true);
      since.current = Date.now();
      await load();
    } catch (e: any) {
      setError(String(e.message ?? e));
      setPhase("choosing");
    }
  }

  const title = items.length === 1
    ? `Set up ${items[0].profileName ?? "this profile"} in ${items[0].shopIds.length} shop${items[0].shopIds.length === 1 ? "" : "s"}`
    : `Set up ${items.length} profiles in your shops`;

  const body = (
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-slate-900/40 sm:items-center sm:p-6" data-testid="shop-setup-dialog">
      <div role="dialog" aria-modal="true" aria-labelledby="shop-setup-title"
        className="flex h-full w-full min-w-0 flex-col bg-white shadow-xl sm:h-auto sm:max-h-[90vh] sm:max-w-3xl sm:rounded-xl">
        <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <h2 id="shop-setup-title" translate="no" className="text-base font-medium text-slate-900">{title}</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Each shop keeps its own shipping, return and processing settings. A setting with the same name (or the same
              terms) in a shop is used automatically. Where a shop has none, a copy of your main shop&apos;s is
              preselected; it is created only when you confirm. You can change any choice.
            </p>
          </div>
          <button type="button" className="tap -mr-2 shrink-0 rounded p-2 text-slate-500" aria-label="Close"
            disabled={phase === "saving"} onClick={() => onClose(changed)}>✕</button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-5">
          {items.map((item) => (
            <section key={item.profileId} className="space-y-3" aria-label={item.profileName ?? "Profile"}>
              {items.length > 1 && <h3 key="name" translate="no" className="text-sm font-medium text-slate-800">{item.profileName}</h3>}
              {item.shopIds.map((shopId) => {
                const shop = shopsOf(item.profileId).find((s) => s.connection_id === shopId);
                return (
                  <ShopCard key={shopId} shop={shop} shopId={shopId}
                    choices={choices[item.profileId] ?? {}}
                    disabled={phase === "saving"}
                    onChoose={(key, value) => setChoices((c) => ({ ...c, [item.profileId]: { ...(c[item.profileId] ?? {}), [key]: value } }))}
                    onRetry={() => retry(item.profileId, shopId)} />
                );
              })}
            </section>
          ))}
          {slow && (
            <p key="slow" role="status" className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
              Reading the shops is taking longer than usual. You can close this; the setup carries on, and the shops show
              what they still need.
            </p>
          )}
        </div>

        <div className="space-y-2 border-t border-slate-100 px-4 py-3 sm:px-5">
          <p translate="no" className="text-xs text-slate-600" data-testid="setup-summary">
            {phase === "reading" || (checking && phase !== "saving")
              ? "Reading each shop's own settings…"
              : phase === "saving"
                ? "Setting up…"
                : phase === "done"
                  ? `${allShops.filter((s) => s.link.ready).length} of ${allShops.length} shops are set up.`
                  : `${sum.copies ? `${sum.copies} ${sum.copies === 1 ? "copy" : "copies"} to create (${sum.requests} Etsy request${sum.requests === 1 ? "" : "s"}). ` : "Nothing to create. "}` +
                    (sum.unresolved ? `${sum.unresolved} setting${sum.unresolved === 1 ? "" : "s"} still to choose; those shops wait until they are chosen.` : "")}
          </p>
          {error && <p key="error" className="text-xs text-rose-600">{error}</p>}
          <div className="flex flex-wrap items-center justify-end gap-2">
            {phase === "done" ? (
              <button key="continue" type="button" className="btn-primary" onClick={() => onClose(true)} data-testid="setup-continue">
                Continue
              </button>
            ) : (
              <>
                <button key="cancel" type="button" className="btn-secondary" disabled={phase === "saving"} onClick={() => onClose(changed)}>
                  Cancel
                </button>
                <button key="confirm" type="button" className="btn-primary" data-testid="setup-confirm"
                  disabled={phase !== "choosing" || checking || allShops.length === 0} onClick={confirm}>
                  <span translate="no">{confirmLabel(sum)}</span>
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
  return typeof document === "undefined" ? null : createPortal(body, document.body);
}

function ShopCard({
  shop, shopId, choices, disabled, onChoose, onRetry,
}: {
  shop: ShopSetup | undefined;
  shopId: string;
  choices: Record<string, Choice>;
  disabled: boolean;
  onChoose: (key: string, value: Choice) => void;
  onRetry: () => void;
}) {
  const name = shop?.shop_name ?? "this shop";
  const status = shop?.link.status;
  return (
    <div className="rounded-lg border border-slate-200 p-3 text-sm" data-testid="setup-shop">
      <div className="flex flex-wrap items-center gap-2">
        <ShopBadge name={shop?.shop_name ?? null} />
        {shop?.link.ready && status !== "checking" ? (
          <span key="ready" className="text-xs text-emerald-700">set up</span>
        ) : status === "checking" || !shop ? (
          <span key="reading" translate="no" className="text-xs text-slate-500">reading its settings…</span>
        ) : status === "error" ? (
          <span key="error" className="text-xs text-rose-700"><Txt>{shop.link.reason}</Txt></span>
        ) : null}
      </div>
      {status === "error" && (
        <button key="retry" type="button" className="tap mt-1 text-xs text-brand-700 underline" disabled={disabled} onClick={onRetry}>
          Try again
        </button>
      )}
      {shop && status !== "checking" && status !== "error" && shop.settings.length === 0 && (
        <p key="none" className="mt-1 text-xs text-slate-500">
          {shop.link.reason ?? "Nothing shop-specific to set: the profile's reference listing uses none."}
        </p>
      )}
      {shop && status !== "checking" && status !== "error" && shop.settings.length > 0 && (
        <ul key="settings" className="mt-2 space-y-2.5">
          {shop.settings.map((s) => {
            const key = choiceKey(shopId, s.resource);
            const value = choices[key] ?? defaultChoice(s);
            const options = choiceOptions(s);
            return (
              <li key={s.resource} className="grid gap-1 sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-start sm:gap-3">
                <span className="pt-2 text-xs font-medium text-slate-700 max-sm:pt-0">{s.label}</span>
                <div className="min-w-0 space-y-1">
                  <select className="field w-full py-1.5 text-sm" value={value} disabled={disabled}
                    onChange={(e) => onChoose(key, e.target.value)} aria-label={`${s.label} in ${name}`}>
                    {value === "" && <option key="choose" value="">Choose…</option>}
                    {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                  <p className={"text-xs " + (value === "" ? "text-amber-800" : "text-slate-500")} data-testid="setup-will-use">
                    <Txt>{willUse(s, value, name)}</Txt>
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
