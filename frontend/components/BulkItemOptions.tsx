"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ItemOptions } from "@/lib/types";

const KEEP = "__keep__";
const NONE = "__none__";

/**
 * "Set for selected" on the review page: one Occasion, Holiday and Section for
 * every ticked listing. The lists come from the first ticked listing's category
 * and shops; a listing whose category or shops do not have the value is left as
 * it is, and the result says which and why.
 */
export function BulkItemOptions({
  batchId,
  selected,
  onSelectAll,
  total,
  onDone,
}: {
  batchId: string;
  selected: string[];
  onSelectAll: (all: boolean) => void;
  total: number;
  onDone: () => void;
}) {
  const [sample, setSample] = useState<ItemOptions | null>(null);
  const [occasion, setOccasion] = useState(KEEP);
  const [holiday, setHoliday] = useState(KEEP);
  const [shop, setShop] = useState("");
  const [section, setSection] = useState(KEEP);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  // "Set SKU for selected": one SKU, or each listing's own with a prefix / suffix.
  const [skuAll, setSkuAll] = useState("");
  const [prefix, setPrefix] = useState("");
  const [suffix, setSuffix] = useState("");

  async function applySku() {
    setBusy(true);
    setResult(null);
    try {
      const res = await api.setSkuForSelected(batchId, {
        content_ids: selected,
        ...(skuAll.trim() ? { sku: skuAll.trim() } : {}),
        ...(prefix ? { prefix } : {}),
        ...(suffix ? { suffix } : {}),
      });
      const warned = res.results.flatMap((r) => r.warnings);
      const onEtsy = res.results.filter((r) => r.etsy.length > 0).length;
      setResult(
        `SKU set for ${res.updated} listing${res.updated === 1 ? "" : "s"}.` +
          (res.skipped.length ? ` Left as they were: ${[...new Set(res.skipped.map((s) => s.reason))].join("; ")}.` : "") +
          (warned.length ? ` ${warned[0]}${warned.length > 1 ? ` (+${warned.length - 1} more)` : ""}` : "") +
          (onEtsy ? ` ${onEtsy === 1 ? "1 listing already has drafts" : `${onEtsy} listings already have drafts`} on Etsy: use “Update SKU on Etsy” on ${onEtsy === 1 ? "its card" : "their cards"}.` : ""),
      );
      onDone();
    } catch (e: any) {
      setResult(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }
  const first = selected[0];

  useEffect(() => {
    setSample(null);
    if (!first) return;
    api.itemOptions(first).then(setSample).catch(() => setSample(null));
  }, [first]);
  const sectionsOf = sample?.shops.find((s) => s.connection_id === shop)?.sections ?? null;

  async function apply() {
    setBusy(true);
    setResult(null);
    try {
      const body: Parameters<typeof api.setItemOptionsForSelected>[1] = { content_ids: selected };
      if (occasion !== KEEP) body.occasion = occasion === NONE ? [] : [occasion];
      if (holiday !== KEEP) body.holiday = holiday === NONE ? [] : [holiday];
      if (shop && section !== KEEP) {
        body.section_connection_id = shop;
        body.section_id = section === NONE ? null : Number(section);
      }
      const res = await api.setItemOptionsForSelected(batchId, body);
      setResult(
        `Set for ${res.updated} listing${res.updated === 1 ? "" : "s"}.` +
          (res.skipped.length ? ` Left as they were: ${res.skipped.map((s) => s.reason).filter((v, i, a) => a.indexOf(v) === i).join("; ")}` : ""),
      );
      onDone();
    } catch (e: any) {
      setResult(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const nothing = occasion === KEEP && holiday === KEEP && !(shop && section !== KEEP);
  return (
    <div className="card flex flex-wrap items-center gap-x-3 gap-y-2 p-3 text-sm" data-testid="bulk-options">
      <label className="flex min-h-[44px] cursor-pointer items-center gap-2 text-slate-700">
        <input type="checkbox" className="h-4 w-4" checked={selected.length === total && total > 0}
          onChange={(e) => onSelectAll(e.target.checked)} aria-label="Select every listing" />
        <span translate="no">{selected.length ? `${selected.length} selected` : "Select listings"}</span>
      </label>
      {selected.length > 0 && (
        <>
          <span key="label" className="font-medium text-slate-800">Set for selected:</span>
          {sample?.occasion && (
            <select key="occ" className="field w-auto py-1.5 text-sm" value={occasion} onChange={(e) => setOccasion(e.target.value)} aria-label="Occasion for the selected listings">
              <option value={KEEP}>Occasion: leave</option>
              <option value={NONE}>Occasion: none</option>
              {sample.occasion.values.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          )}
          {sample?.holiday && (
            <select key="hol" className="field w-auto py-1.5 text-sm" value={holiday} onChange={(e) => setHoliday(e.target.value)} aria-label="Holiday for the selected listings">
              <option value={KEEP}>Holiday: leave</option>
              <option value={NONE}>Holiday: none</option>
              {sample.holiday.values.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          )}
          {sample && sample.shops.length > 0 && (
            <select key="shop" className="field w-auto py-1.5 text-sm" value={shop} onChange={(e) => { setShop(e.target.value); setSection(KEEP); }} aria-label="Shop for the section">
              <option value="">Section in…</option>
              {sample.shops.map((s) => <option key={s.connection_id} value={s.connection_id}>{s.shop_name ?? "shop"}</option>)}
            </select>
          )}
          {shop && sectionsOf && (
            <select key="sec" className="field w-auto py-1.5 text-sm" value={section} onChange={(e) => setSection(e.target.value)} aria-label="Section for the selected listings">
              <option value={KEEP}>Section: leave</option>
              <option value={NONE}>No section</option>
              {sectionsOf.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
          )}
          <button key="apply" type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={busy || nothing} onClick={apply}>
            {busy ? "Setting…" : "Set for selected"}
          </button>
          <span key="sku-row" className="flex w-full flex-wrap items-center gap-2 border-t border-slate-100 pt-2">
            <span className="font-medium text-slate-800">SKU for selected:</span>
            <input className="field w-28 py-1 font-mono text-sm" placeholder="Prefix" value={prefix} onChange={(e) => setPrefix(e.target.value)} aria-label="SKU prefix" maxLength={40} />
            <input className="field w-32 py-1 font-mono text-sm" placeholder="Same SKU" value={skuAll} onChange={(e) => setSkuAll(e.target.value)} aria-label="One SKU for all (empty: each keeps its own)" maxLength={40} />
            <input className="field w-28 py-1 font-mono text-sm" placeholder="Suffix, e.g. -CC" value={suffix} onChange={(e) => setSuffix(e.target.value)} aria-label="SKU suffix" maxLength={40} />
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || !(skuAll.trim() || prefix || suffix)} onClick={applySku}>
              Set SKU for selected
            </button>
          </span>
        </>
      )}
      {result && (
        <p key="result" role="status" translate="no" className="w-full text-xs text-slate-600">
          {result}
        </p>
      )}
    </div>
  );
}
