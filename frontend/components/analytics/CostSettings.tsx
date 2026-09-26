"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { CostSettings as Costs, Profile } from "@/lib/types";

type Rates = Omit<Costs, "defaults" | "product_cost_by_profile" | "product_cost_by_sku">;

const RATES: { key: keyof Rates; label: string; unit: "money" | "pct"; help: string }[] = [
  { key: "listing_fee", label: "Listing fee", unit: "money", help: "per item sold (each sale renews the listing)" },
  { key: "transaction_pct", label: "Transaction fee", unit: "pct", help: "of the sale price" },
  { key: "payment_pct", label: "Payment processing", unit: "pct", help: "of the sale price" },
  { key: "payment_fixed", label: "Payment processing, fixed", unit: "money", help: "per order line" },
  { key: "shipping_cost", label: "Shipping you pay", unit: "money", help: "per order line" },
  { key: "monthly_fixed", label: "Other fixed costs", unit: "money", help: "per month, for the whole shop" },
  { key: "product_cost", label: "Product cost", unit: "money", help: "per item, when no profile or SKU cost applies" },
];

/**
 * The seller's own fee rates and costs (v7 §C2). Etsy's rates change and differ
 * by country, so every one is editable; the defaults are Etsy's published US rates.
 */
export function CostSettings({ shopId, currency, onSaved }: { shopId: string | null; currency: string | null; onSaved: () => void }) {
  const [costs, setCosts] = useState<Costs | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [skus, setSkus] = useState<[string, string][]>([]);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    api
      .costs()
      .then((c) => {
        setCosts(c);
        setSkus(Object.entries(c.product_cost_by_sku));
      })
      .catch(() => setMessage({ ok: false, text: "Your costs could not be loaded." }));
  }, []);
  useEffect(() => {
    api.listProfiles(shopId).then(setProfiles).catch(() => setProfiles([]));
  }, [shopId]);

  if (!costs) return message ? <p className="text-sm text-rose-700">{message.text}</p> : <p className="text-sm text-slate-400">Loading…</p>;

  const set = (key: keyof Rates, v: string) => setCosts({ ...costs, [key]: v });
  const setProfileCost = (id: string, v: string) =>
    setCosts({ ...costs, product_cost_by_profile: { ...costs.product_cost_by_profile, [id]: v } });

  const save = async () => {
    setSaving(true);
    setMessage(null);
    const { defaults: _defaults, ...body } = costs;
    void _defaults;
    const bySku: Record<string, string> = {};
    for (const [k, v] of skus) if (k.trim()) bySku[k.trim()] = v;
    try {
      const saved = await api.saveCosts({ ...body, product_cost_by_sku: bySku });
      setCosts(saved);
      setSkus(Object.entries(saved.product_cost_by_sku));
      setMessage({ ok: true, text: "Saved. The figures use these from now on." });
      onSaved();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "Could not save." });
    } finally {
      setSaving(false);
    }
  };

  const resetRates = () => {
    if (!costs.defaults) return;
    const next = { ...costs };
    for (const k of ["listing_fee", "transaction_pct", "payment_pct", "payment_fixed"] as const) next[k] = costs.defaults[k];
    setCosts(next);
  };

  const ccy = currency ?? "your shop's currency";

  return (
    <div className="space-y-6">
      <section className="card p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-800">Fees and costs</h2>
          <button type="button" className="text-xs text-slate-500 underline" onClick={resetRates}>
            Reset the Etsy fees to Etsy&apos;s US rates
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">Amounts in {ccy}. Etsy changes its rates; keep them matching your own bill.</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {RATES.map((r) => (
            <label key={r.key} className="block">
              <span className="label">{r.label}</span>
              <div className="flex items-center gap-2">
                <input
                  inputMode="decimal"
                  className="field py-1.5 tabular-nums"
                  value={costs[r.key]}
                  onChange={(e) => set(r.key, e.target.value)}
                />
                <span className="w-6 text-sm text-slate-500">{r.unit === "pct" ? "%" : ""}</span>
              </div>
              <span className="mt-0.5 block text-xs text-slate-400">{r.help}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold text-slate-800">Product cost by profile</h2>
        <p className="mt-1 text-xs text-slate-500">
          Per item, for listings made from a profile (and the profile&apos;s own reference listing). Leave empty to use the
          default product cost.
        </p>
        {profiles.length === 0 ? (
          <p className="mt-3 text-sm text-slate-400">This shop has no profiles.</p>
        ) : (
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {profiles.map((p) => (
              <label key={p.id} className="flex items-center gap-3">
                <span className="min-w-0 flex-1 truncate text-sm text-slate-700" title={p.name}>
                  {p.name}
                </span>
                <input
                  inputMode="decimal"
                  className="field w-28 py-1.5 tabular-nums"
                  placeholder={costs.product_cost}
                  value={costs.product_cost_by_profile[p.id] ?? ""}
                  onChange={(e) => setProfileCost(p.id, e.target.value)}
                />
              </label>
            ))}
          </div>
        )}
      </section>

      <section className="card p-5">
        <h2 className="text-sm font-semibold text-slate-800">Product cost by SKU</h2>
        <p className="mt-1 text-xs text-slate-500">Wins over the profile and default cost when a listing has this SKU.</p>
        <div className="mt-3 space-y-2">
          {skus.map(([k, v], i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                className="field w-40 py-1.5"
                placeholder="SKU"
                value={k}
                onChange={(e) => setSkus(skus.map((s, j) => (j === i ? [e.target.value, s[1]] : s)))}
              />
              <input
                inputMode="decimal"
                className="field w-28 py-1.5 tabular-nums"
                placeholder="0.00"
                value={v}
                onChange={(e) => setSkus(skus.map((s, j) => (j === i ? [s[0], e.target.value] : s)))}
              />
              <button type="button" className="text-xs text-slate-500 underline" onClick={() => setSkus(skus.filter((_, j) => j !== i))}>
                remove
              </button>
            </div>
          ))}
          <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => setSkus([...skus, ["", ""]])}>
            Add a SKU
          </button>
        </div>
      </section>

      <div className="flex items-center gap-3">
        <button type="button" className="btn-primary" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        {message && <p className={`text-sm ${message.ok ? "text-emerald-700" : "text-rose-700"}`}>{message.text}</p>}
      </div>
    </div>
  );
}
