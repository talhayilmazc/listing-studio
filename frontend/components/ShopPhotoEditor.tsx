"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { dropBefore, leaveOut, limitLine, makeCover, nudge, putBack, type ShopPhotos } from "@/lib/shopPhotos";

/**
 * "Photos in <shop>": the listing's photos in that shop's order (its first the cover there), and the ones left out
 * there (backend api/shop_photos.py). Drag on a desktop; on a phone or by keyboard, the buttons under each photo.
 * Nothing is saved until "Save order"; other shops keep theirs.
 */
export function ShopPhotoEditor({ batchId, shop, contentId, onSaved }: {
  batchId: string;
  shop: { id: string; name: string };
  contentId: string;
  onSaved?: () => void;
}) {
  const [data, setData] = useState<ShopPhotos | null>(null);
  const [order, setOrder] = useState<string[]>([]);
  const [out, setOut] = useState<string[]>([]);
  const [dragging, setDragging] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const show = useCallback((d: ShopPhotos) => { setData(d); setOrder(d.order); setOut(d.out); }, []);
  useEffect(() => {
    api.shopPhotos(batchId, shop.id, contentId).then(show).catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [batchId, shop.id, contentId, show]);

  async function save(body: { order?: string[]; out?: string[]; keep?: boolean }, done: string) {
    setBusy(true);
    setError(null);
    try {
      show(await api.setShopPhotos(batchId, shop.id, contentId, body));
      setSaved(done);
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return error ? <p className="text-xs text-rose-700"><span translate="no">{error}</span></p> : null;
  const names = Object.fromEntries(data.photos.map((p) => [p.id, p.filename]));
  const changed = order.join() !== data.order.join() || out.join() !== data.out.join();
  const tooMany = order.length > data.room;
  return (
    <div className="space-y-2 rounded-md border border-slate-200 p-3 text-sm" data-testid="shop-photo-editor">
      <p className="text-xs text-slate-600">
        <span translate="no">{data.own ? `${shop.name} has its own order. The first photo is the cover there.` : `Follows the listing's order. Change it for ${shop.name} only.`}</span>
      </p>
      {data.note && <p key="note" className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900" data-testid="shop-photo-note"><span translate="no">{data.note}</span></p>}
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {order.map((id, n) => (
          <li key={id} draggable onDragStart={() => setDragging(id)} onDragEnd={() => setDragging(null)}
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); if (dragging) setOrder(dropBefore(order, dragging, id)); setDragging(null); }}
            className={"rounded-md border p-1 " + (n === 0 ? "border-brand-500 ring-1 ring-brand-500" : "border-slate-200")} data-testid="shop-photo">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={api.assetImage(id, "small")} alt={names[id] ?? ""} loading="lazy" className="aspect-square w-full rounded object-cover" />
            <span className="mt-1 block truncate text-xs text-slate-700" title={names[id]} translate="no">{n === 0 ? `Cover · ${names[id] ?? ""}` : `${n + 1}. ${names[id] ?? ""}`}</span>
            <span className="mt-1 flex flex-wrap gap-1">
              <button type="button" className="tap text-xs text-brand-700 underline" disabled={n === 0} aria-label={`Move ${names[id]} earlier`}
                onClick={() => setOrder(nudge(order, id, -1))}>Earlier</button>
              <button type="button" className="tap text-xs text-brand-700 underline" disabled={n === order.length - 1} aria-label={`Move ${names[id]} later`}
                onClick={() => setOrder(nudge(order, id, 1))}>Later</button>
              {n > 0 && <button key="cover" type="button" className="tap text-xs text-brand-700 underline" onClick={() => setOrder(makeCover(order, id))}>Make cover</button>}
              {order.length > 1 && (
                <button key="out" type="button" className="tap text-xs text-rose-700 underline" aria-label={`Leave ${names[id]} out in ${shop.name}`}
                  onClick={() => { const next = leaveOut(order, out, id); setOrder(next.order); setOut(next.out); }}>Leave out</button>
              )}
            </span>
          </li>
        ))}
      </ol>
      {out.length > 0 && (
        <div key="out" className="text-xs text-slate-700" data-testid="shop-photo-out">
          <span className="font-medium">Left out here: </span>
          {out.map((id) => (
            <button key={id} type="button" className="tap mr-2 underline" onClick={() => { const next = putBack(order, out, id); setOrder(next.order); setOut(next.out); }}>
              <span translate="no">{`Put back ${names[id] ?? ""}`}</span>
            </button>
          ))}
        </div>
      )}
      <p className={"text-xs " + (tooMany ? "text-rose-700" : "text-slate-600")} translate="no">{limitLine(order.length, data.room, data.charts)}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || !changed || tooMany} data-testid="shop-photo-save"
          onClick={() => void save({ order, out }, `Saved for ${shop.name}.`)}>Save order</button>
        {data.own && (
          <button key="keep" type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} data-testid="shop-photo-keep"
            onClick={() => void save({ keep: true }, "Back to the listing's order.")}>Keep as it is</button>
        )}
      </div>
      {saved && !changed && <p key="saved" role="status" className="text-xs text-emerald-800"><span translate="no">{saved}</span></p>}
      {error && <p key="error" role="alert" className="text-xs text-rose-700"><span translate="no">{error}</span></p>}
    </div>
  );
}
