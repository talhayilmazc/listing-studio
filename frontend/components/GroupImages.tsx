"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { makeCover, moveBefore, nudge, sameOrder } from "@/lib/order";
import type { Asset, BatchDetail } from "@/lib/types";

/**
 * A listing group's images (docs/duzeltmeler-v6.md §E). The first is the cover.
 * Drag to reorder, or use "Make cover" and the arrows; each change is saved as
 * the images' rank and Etsy receives them in that order.
 */
export function GroupImages({
  batchId,
  groupKey,
  assets,
  onSaved,
}: {
  batchId: string;
  groupKey: string;
  assets: Asset[];
  onSaved: (batch: BatchDetail) => void;
}) {
  const incoming = assets.map((a) => a.id);
  const [order, setOrder] = useState<string[]>(incoming);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const incomingKey = incoming.join(",");
  useEffect(() => {
    setOrder(incomingKey ? incomingKey.split(",") : []);
  }, [incomingKey]);

  const byId = new Map(assets.map((a) => [a.id, a]));
  const usable = (id: string) => byId.get(id)?.status === "processed";

  async function save(next: string[]) {
    if (sameOrder(next, order)) return;
    if (!usable(next[0])) {
      setError("An image that failed to process can’t be the cover.");
      return;
    }
    const before = order;
    setOrder(next); // show it at once; put it back if saving fails
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.orderGroup(batchId, groupKey, next));
    } catch (e: any) {
      setOrder(before);
      setError(e.message ?? String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-3">
      <ul className="flex gap-2 overflow-x-auto pb-1" aria-label="Images, in listing order">
        {order.map((id, i) => {
          const a = byId.get(id);
          if (!a) return null;
          const cover = i === 0;
          return (
            <li
              key={id}
              draggable={!saving}
              onDragStart={(e) => {
                setDragging(id);
                e.dataTransfer.effectAllowed = "move";
                e.dataTransfer.setData("text/plain", id); // Firefox starts no drag without data
              }}
              onDragEnd={() => {
                setDragging(null);
                setOver(null);
              }}
              onDragOver={(e) => {
                e.preventDefault();
                if (over !== id) setOver(id);
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragging) save(moveBefore(order, dragging, id));
                setDragging(null);
                setOver(null);
              }}
              className={
                "group relative h-24 w-24 shrink-0 cursor-grab overflow-hidden rounded border-2 bg-slate-100 active:cursor-grabbing " +
                (cover ? "border-brand-500 " : "border-transparent ") +
                (over === id && dragging && dragging !== id ? "ring-2 ring-brand-500/40 " : "") +
                (dragging === id ? "opacity-40" : "")
              }
              title={a.original_filename}
            >
              {a.status === "processed" ? (
                <>
                  {/* Skeleton under the image: a fast scroll shows it, never blank space. */}
                  <span className="absolute inset-0 animate-pulse bg-slate-200" aria-hidden />
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={api.assetImage(a.id, 224)}
                    alt={a.original_filename}
                    draggable={false}
                    decoding="async"
                    className="relative h-full w-full object-cover"
                  />
                </>
              ) : (
                <span className="flex h-full items-center justify-center p-1 text-center text-[10px] text-rose-700">
                  failed
                </span>
              )}
              {cover && (
                <span className="absolute left-1 top-1 rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
                  Cover
                </span>
              )}
              <span className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-white/90 px-0.5 py-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                <button
                  type="button"
                  className="px-1 text-xs text-slate-600 hover:text-slate-900 disabled:opacity-30"
                  onClick={() => save(nudge(order, id, -1))}
                  disabled={saving || i === 0}
                  aria-label={`Move ${a.original_filename} earlier`}
                >
                  ◀
                </button>
                {!cover && (
                  <button
                    type="button"
                    className="text-[10px] font-medium text-brand-700 hover:underline disabled:opacity-30"
                    onClick={() => save(makeCover(order, id))}
                    disabled={saving || !usable(id)}
                  >
                    Make cover
                  </button>
                )}
                <button
                  type="button"
                  className="px-1 text-xs text-slate-600 hover:text-slate-900 disabled:opacity-30"
                  onClick={() => save(nudge(order, id, 1))}
                  disabled={saving || i === order.length - 1}
                  aria-label={`Move ${a.original_filename} later`}
                >
                  ▶
                </button>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-1 text-xs text-slate-400">
        {saving
          ? "Saving order…"
          : "Drag to reorder. The cover is the first photo on Etsy; drafts created from now on use this order."}
      </p>
      {error && <p className="mt-1 text-xs text-rose-700">{error}</p>}
    </div>
  );
}
