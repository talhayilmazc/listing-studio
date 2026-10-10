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
