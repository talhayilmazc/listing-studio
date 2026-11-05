"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { canFollow, goodNumber, setCount, shown, valuesFor, withValue, type AttributeProperty, type Attributes, type AttributeValue } from "@/lib/listingAttributes";

/**
 * Every attribute Etsy offers for the listing's category, with Etsy's values only (backend api/listing_attributes.py).
 * ``shop``: the values for that shop only, defaulting to the listing's own, with "Keep as it is". Each change is saved
 * at once; the server refuses anything Etsy would not take and says why.
 */
export function AttributesEditor({ contentId, shop = null, onChanged, open: startOpen = false }: {
  contentId: string;
  shop?: { id: string; name: string } | null;
  onChanged?: () => void;
  open?: boolean;
}) {
  const [data, setData] = useState<Attributes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.attributes(contentId, shop?.id ?? null));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [contentId, shop?.id]);
  useEffect(() => { void load(); }, [load]);

  async function save(body: { values?: Record<string, AttributeValue>; keep?: boolean }) {
    setBusy(true);
    setError(null);
    try {
      setData(await api.setAttributes(contentId, { connection_id: shop?.id ?? null, values: body.values ?? {}, keep: body.keep ?? false }));
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return error ? <p className="text-xs text-rose-700"><span translate="no">{error}</span></p> : null;
  const own = data.properties.some((p) => p.source === "shop");
  return (
    <details className="rounded-md border border-slate-200" open={startOpen} data-testid="attributes-editor">
      <summary className="tap cursor-pointer px-3 py-2 text-sm font-medium text-slate-900">
        <span translate="no">{`Attributes${shop ? ` in ${shop.name}` : ""} · ${setCount(data.properties)} of ${data.properties.length} set`}</span>
      </summary>
      <div className="space-y-3 border-t border-slate-200 p-3 text-sm">
        <p className="text-xs text-slate-600">
          <span>{shop ? `Values for ${shop.name} only. Anything you leave follows the listing.` : "Only Etsy's values for this category. What you set is kept when the listing is rewritten."}</span>
        </p>
        {data.note && <p key="note" className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900" data-testid="attributes-note"><span translate="no">{data.note}</span></p>}
        {data.waiting && <p key="waiting" className="text-xs text-slate-600">The attributes appear once the listing&apos;s profile has read its category from Etsy.</p>}
        {data.dropped.length > 0 && (
          <p key="dropped" className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900" data-testid="attributes-dropped">
            <span translate="no">{`Not in this category, so not sent: ${data.dropped.map((d) => `${d.name} (${d.values.join(", ")})`).join("; ")}.`}</span>
          </p>
        )}
        <ul className="grid gap-3 sm:grid-cols-2">
          {data.properties.map((p) => (
            <li key={p.name}>
              <Property prop={p} busy={busy} shop={!!shop} onSave={(value) => void save({ values: { [p.name]: value } })} />
            </li>
          ))}
        </ul>
        {shop && own && (
          <button key="keep" type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={() => void save({ keep: true })}
            data-testid="attributes-keep">Keep as it is</button>
        )}
        {error && <p key="error" role="alert" className="text-xs text-rose-700"><span translate="no">{error}</span></p>}
      </div>
    </details>
  );
}

function Property({ prop, busy, shop, onSave }: { prop: AttributeProperty; busy: boolean; shop: boolean; onSave: (value: AttributeValue) => void }) {
  const [unit, setUnit] = useState(prop.unit ?? prop.units[0] ?? "");
  const [number, setNumber] = useState(prop.kind === "number" ? prop.current[0] ?? "" : "");
  useEffect(() => { setUnit(prop.unit ?? prop.units[0] ?? ""); setNumber(prop.kind === "number" ? prop.current[0] ?? "" : ""); }, [prop]);
  const label = `${prop.name}${prop.required ? " (required by Etsy)" : ""}`;
  const choices = valuesFor(prop, prop.kind === "choice" && prop.units.length ? unit : null);
  return (
    <div className="flex flex-col gap-1" data-testid="attribute-row">
      <span className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-medium text-slate-800">{label}</span>
        <span className="text-xs text-slate-600" translate="no">{prop.source_words}</span>
      </span>
      {prop.kind === "number" ? (
        <span className="flex gap-2">
          <input className="field min-w-0 flex-1 py-1.5 text-sm" inputMode="decimal" value={number} aria-label={prop.name}
            onChange={(e) => setNumber(e.target.value)} />
          <select className="field py-1.5 text-sm" value={unit} aria-label={`${prop.name} unit`} onChange={(e) => setUnit(e.target.value)}>
            {prop.units.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
          <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || (number !== "" && !goodNumber(number))}
            onClick={() => onSave(number.trim() ? { values: [number.trim()], unit } : { values: [] })}>Save</button>
        </span>
      ) : prop.max_values > 1 ? (
        <span className="flex flex-col gap-1">
          <span className="flex flex-wrap gap-1" translate="no">
            {prop.current.map((v) => (
              <button key={v} type="button" className="tap rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-800" disabled={busy}
                aria-label={`Remove ${v}`} onClick={() => onSave({ values: prop.current.filter((x) => x !== v) })}>
                <span>{`${v} ×`}</span>
              </button>
            ))}
            {prop.current.length === 0 && <span key="none" className="text-xs text-slate-600">Not set</span>}
          </span>
          <select className="field py-1.5 text-sm" value="" disabled={busy || prop.current.length >= prop.max_values} aria-label={`Add a ${prop.name}`}
            onChange={(e) => e.target.value && onSave({ values: withValue(prop.current, e.target.value, prop.max_values) })}>
            <option value="">{prop.current.length >= prop.max_values ? `Up to ${prop.max_values}` : "Add…"}</option>
            {choices.filter((v) => !prop.current.includes(v)).map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </span>
      ) : (
        <span className="flex gap-2">
          {prop.units.length > 0 && (
            <select key="unit" className="field py-1.5 text-sm" value={unit} aria-label={`${prop.name} unit`} onChange={(e) => setUnit(e.target.value)}>
              {prop.units.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          )}
          <select className="field min-w-0 flex-1 py-1.5 text-sm" value={prop.current[0] ?? ""} disabled={busy} aria-label={prop.name}
            onChange={(e) => onSave(e.target.value ? { values: [e.target.value], unit: prop.units.length ? unit : null } : { values: [] })}>
            <option value="">Not set</option>
            {choices.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        </span>
      )}
      {canFollow(prop, shop) && (
        <button key="follow" type="button" className="tap self-start text-xs text-brand-700 underline" disabled={busy} onClick={() => onSave(null)}>
          {shop ? "Follow the listing" : prop.suggested.length ? `Use the suggestion (${prop.suggested.join(", ")})` : "Clear what I set"}
        </button>
      )}
      {!canFollow(prop, shop) && prop.current.length > 0 && <span key="shown" className="sr-only" translate="no">{shown(prop)}</span>}
    </div>
  );
}
