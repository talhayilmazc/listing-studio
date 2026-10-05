"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LINES } from "@/lib/analyticsDefinitions";
import type { ProductCostsView } from "@/lib/types";
import { ShopBadge } from "@/components/ShopPicker";
import { Term } from "./Month";

import { Txt } from "@/components/Txt";
/**
 * What each profile's item costs to make and what the provider charges to ship
 * it. The only source of product cost: a blank is "not entered", and until a
 * cost is entered the result stays "profit before product cost".
 */
type Draft = Record<string, { production: string; shipping: string }>;

export function ProductCosts({ shopId, currency, onSaved }: { shopId: string | null; currency: string | null; onSaved: () => void }) {
  const [view, setView] = useState<ProductCostsView | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const take = useCallback((v: ProductCostsView) => {
    setView(v);
    setDraft(Object.fromEntries(v.profiles.map((p) => [p.profile_id, { production: p.production ?? "", shipping: p.shipping ?? "" }])));
  }, []);

  useEffect(() => {
    setView(null);
    api.productCosts(shopId).then(take).catch((e) => setMessage({ ok: false, text: e instanceof Error ? e.message : "Could not load your product costs." }));
  }, [shopId, take]);

  async function save() {
    if (!view) return;
    setBusy(true);
    setMessage(null);
    try {
      const body = Object.fromEntries(
        view.profiles.map((p) => [p.profile_id, { production: draft[p.profile_id]?.production.trim() || null, shipping: draft[p.profile_id]?.shipping.trim() || null, sizes: p.sizes }]),
      );
      take(await api.saveProductCosts(shopId, body));
      setMessage({ ok: true, text: "Saved. The month's figures now use these costs." });
      onSaved();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "That could not be saved." });
    } finally {
      setBusy(false);
    }
  }

  if (!view) return <p className="text-sm text-slate-400">{message?.text ?? "Loading…"}</p>;
  const set = (id: string, key: "production" | "shipping", value: string) =>
    setDraft((d) => ({ ...d, [id]: { ...d[id], [key]: value } }));
  const unit = currency ?? "USD";
  const missing = view.profiles.filter((p) => !(draft[p.profile_id]?.production.trim() && draft[p.profile_id]?.shipping.trim())).length;

  return (
    <section className="card space-y-4 p-4 text-sm sm:p-5" aria-labelledby="costs-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="costs-title" className="font-medium text-slate-900">Product costs</h2>
        <ShopBadge name={view.shop_name} />
      </div>
      <p className="max-w-prose text-xs text-slate-500">
        <span>
          Enter, per item, what each profile costs you to make and what your provider charges you to ship it. Both are
          needed; enter 0 if one really is nothing. Leave a field blank if you don&apos;t know yet: a cost is never
          assumed, and until it is entered that profile&apos;s listings show their result before product cost.
        </span>
      </p>

      {view.profiles.length === 0 ? (
        <p key="none" className="text-slate-500">This shop has no profiles yet. Listings created with a profile take that profile&apos;s cost.</p>
      ) : (
        <ul key="list" className="divide-y divide-slate-100">
          <li className="hidden grid-cols-[minmax(0,1fr)_10rem_10rem] gap-3 pb-2 text-xs text-slate-500 sm:grid">
            <span>Profile</span>
            <Term def={LINES.product_cost} />
            <Term def={LINES.provider_shipping} />
          </li>
          {view.profiles.map((p) => (
            <li key={p.profile_id} className="grid grid-cols-2 gap-3 py-3 sm:grid-cols-[minmax(0,1fr)_10rem_10rem] sm:items-center">
              <p className="col-span-2 min-w-0 truncate font-medium text-slate-800 sm:col-span-1" translate="no">{p.name}</p>
              {(["production", "shipping"] as const).map((key) => (
                <label key={key} className="block">
                  <span className="mb-1 block text-xs text-slate-500 sm:sr-only">{key === "production" ? LINES.product_cost.label : LINES.provider_shipping.label}</span>
                  <span className="flex items-center gap-1.5">
                    <input
                      className="field w-full text-right tabular-nums"
                      inputMode="decimal"
                      placeholder="not entered"
                      value={draft[p.profile_id]?.[key] ?? ""}
                      onChange={(e) => set(p.profile_id, key, e.target.value)}
                      aria-label={`${p.name}: ${key === "production" ? "production cost per item" : "provider's shipping cost per item"}`}
                    />
                    <span className="text-xs text-slate-400" translate="no">{unit}</span>
                  </span>
                </label>
              ))}
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" onClick={save} disabled={busy || view.profiles.length === 0}>
          {busy ? "Saving…" : "Save product costs"}
        </button>
        {missing > 0 && view.profiles.length > 0 && (
          <span key="missing" className="text-xs text-amber-700">
            <span>{`${missing} ${missing === 1 ? "profile has" : "profiles have"} no complete cost yet.`}</span>
          </span>
        )}
        {message && <span key="message" role="status" className={`text-xs ${message.ok ? "text-emerald-700" : "text-rose-700"}`}>{message.text}</span>}
      </div>
      <p className="text-xs text-slate-400">
        <span><span>Listings that were not created with a profile have no product cost here; their result stays before product cost.</span>
        <Txt>{view.sizes_supported ? "" : " One cost per profile for now: a different cost per size needs the size of each sale, which is not read."}</Txt></span>
      </p>
    </section>
  );
}
