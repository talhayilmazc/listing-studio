"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AttributeBulk, AttributeProperty } from "@/lib/listingAttributes";
import type { PhotoBulk } from "@/lib/shopPhotos";

type Preview = { sentence: string; rows: { content_id: string; label: string; before: string[]; after: string[]; changed: boolean; reason: string | null; note: string | null }[] };

/** The preview every bulk change shows before it is applied: who changes, who is skipped and why. */
function PreviewList({ preview, testId }: { preview: Preview; testId: string }) {
  return (
    <div className="space-y-1 text-xs" data-testid={testId}>
      <p className="font-medium text-slate-900"><span translate="no">{preview.sentence}</span></p>
      <ul className="space-y-0.5" translate="no">
        {preview.rows.map((r) => (
          <li key={r.content_id} className={r.reason ? "text-amber-900" : "text-slate-700"}>
            <span>{`${r.label}: `}</span>
            <span>{r.reason ? `skipped, ${r.reason}` : r.changed ? `${r.before.join(", ") || "not set"} → ${r.after.join(", ") || "not set"}` : "already so"}</span>
            {r.note ? <span>{` (${r.note})`}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** "Use photo number N as the cover in <shop>" and "Move the cover to the end in <shop>", previewed first. */
export function PhotoBulk({ batchId, shop, ids, onDone }: { batchId: string; shop: { id: string; name: string }; ids: string[]; onDone: (message: string) => void }) {
  const [number, setNumber] = useState("2");
  const [preview, setPreview] = useState<PhotoBulk | null>(null);
  const [body, setBody] = useState<{ action: "cover" | "cover_last"; number?: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ask = async (next: { action: "cover" | "cover_last"; number?: number }) => {
    setError(null);
    try {
      setPreview(await api.shopPhotosBulk(batchId, shop.id, { content_ids: ids, ...next, apply: false }));
      setBody(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="flex flex-col gap-1 sm:col-span-2" data-testid="bulk-photos">
      <span>{`Photos in ${shop.name}`}</span>
      <span className="flex flex-wrap items-center gap-2">
        <span>Use photo number</span>
        <input className="field w-16 py-1.5 text-sm" inputMode="numeric" value={number} onChange={(e) => setNumber(e.target.value.replace(/\D/g, ""))} aria-label="Photo number" />
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={!number} onClick={() => void ask({ action: "cover", number: Number(number) })}>
          <span translate="no">{`as the cover in ${shop.name}`}</span>
        </button>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => void ask({ action: "cover_last" })}>
          <span translate="no">{`Move the cover to the end in ${shop.name}`}</span>
        </button>
      </span>
      {preview && body && (
        <div key="preview" className="rounded-md border border-slate-200 p-2">
          <PreviewList preview={preview} testId="bulk-photos-preview" />
          <span className="mt-2 flex gap-2">
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={preview.changed === 0}
              onClick={async () => { const res = await api.shopPhotosBulk(batchId, shop.id, { content_ids: ids, ...body, apply: true }); setPreview(null); onDone(res.sentence); }}>
              <span translate="no">{`Apply to ${preview.changed}`}</span>
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setPreview(null)}>Cancel</button>
          </span>
        </div>
      )}
      {error && <p key="error" role="alert" className="text-rose-700"><span translate="no">{error}</span></p>}
    </div>
  );
}

/** "Set <attribute> to <value>" for the selected listings (for one shop with ``shop``), previewed first. The list of
 *  attributes and values is the first selected listing's category; listings whose category differs are skipped. */
export function AttributeBulkSet({ batchId, ids, shop = null, onDone }: {
  batchId: string;
  ids: string[];
  shop?: { id: string; name: string } | null;
  onDone: (message: string) => void;
}) {
  const [props, setProps] = useState<AttributeProperty[]>([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [unit, setUnit] = useState("");
  const [preview, setPreview] = useState<AttributeBulk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const first = ids[0];
  useEffect(() => {
    if (!first) return;
    api.attributes(first, shop?.id ?? null).then((a) => setProps(a.properties)).catch(() => setProps([]));
  }, [first, shop?.id]);
  const prop = props.find((p) => p.name === name);
  useEffect(() => { setValue(""); setUnit(prop?.units[0] ?? ""); setPreview(null); }, [name, prop?.units]);
  const body = () => ({ content_ids: ids, name, values: value ? [value] : [], unit: prop?.units.length ? unit : null, connection_id: shop?.id ?? null });
  const ask = async (apply: boolean) => {
    setError(null);
    try {
      const res = await api.attributesBulk(batchId, { ...body(), apply });
      if (apply) { setPreview(null); onDone(res.sentence); } else setPreview(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  if (!props.length) return null;
  return (
    <div className="flex flex-col gap-1 sm:col-span-2" data-testid="bulk-attributes">
      <span>{shop ? `Set an attribute in ${shop.name}` : "Set an attribute"}</span>
      <span className="flex flex-wrap items-center gap-2">
        <select className="field py-1.5 text-sm" value={name} onChange={(e) => setName(e.target.value)} aria-label="Attribute">
          <option value="">Attribute…</option>
          {props.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
        {prop?.kind === "choice" && (
          <select key="value" className="field py-1.5 text-sm" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Value">
            <option value="">Not set</option>
            {prop.values.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        )}
        {prop?.kind === "number" && (
          <input key="number" className="field w-24 py-1.5 text-sm" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Value" />
        )}
        {prop && prop.units.length > 0 && (
          <select key="unit" className="field py-1.5 text-sm" value={unit} onChange={(e) => setUnit(e.target.value)} aria-label="Unit">
            {prop.units.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        )}
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={!prop} onClick={() => void ask(false)} data-testid="bulk-attributes-preview-button">Preview</button>
      </span>
      {preview && (
        <div key="preview" className="rounded-md border border-slate-200 p-2">
          <PreviewList preview={preview} testId="bulk-attributes-preview" />
          <span className="mt-2 flex gap-2">
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={preview.changed === 0} onClick={() => void ask(true)}>
              <span translate="no">{`Apply to ${preview.changed}`}</span>
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setPreview(null)}>Cancel</button>
          </span>
        </div>
      )}
      {error && <p key="error" role="alert" className="text-rose-700"><span translate="no">{error}</span></p>}
    </div>
  );
}
