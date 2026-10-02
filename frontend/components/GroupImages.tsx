"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { makeCover, moveBefore, nudge, sameOrder } from "@/lib/order";
import { cropStyle } from "@/lib/crop";
import { CoverCropper } from "./CoverCropper";
import type { Asset, BatchDetail } from "@/lib/types";

/**
 * A listing group's images (docs/duzeltmeler-v6.md §E). The first is the cover.
 * Each change is saved as the images' rank and Etsy receives them in that order.
 *
 * Three ways to reorder, because dragging is a mouse gesture (HTML drag and
 * drop does not start from a touch) and the arrows over each image only appear
 * on hover: drag with a mouse; the arrows on hover; or tap an image and use
 * the buttons under the strip, which is what a phone gets.
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
  const [cropping, setCropping] = useState(false);
  // The image the buttons under the strip act on (tap to choose).
  const [picked, setPicked] = useState<string | null>(null);
  const incomingKey = incoming.join(",");
  useEffect(() => {
    setOrder(incomingKey ? incomingKey.split(",") : []);
  }, [incomingKey]);

  const byId = new Map(assets.map((a) => [a.id, a]));
  const coverAsset = byId.get(order[0]);
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
      <ul className="flex flex-wrap gap-2 pb-1" aria-label="Images, in listing order">
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
              onClick={() => setPicked((cur) => (cur === id ? null : id))}
              aria-current={picked === id ? "true" : undefined}
              className={
                "group relative h-24 w-24 shrink-0 cursor-grab overflow-hidden rounded border-2 bg-slate-100 active:cursor-grabbing " +
                (picked === id ? "border-slate-900 ring-2 ring-slate-900/30 " : cover ? "border-brand-500 " : "border-transparent ") +
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
                  {cover && a.cover_crop && a.width && a.height ? (
                    // The cover as Etsy will get it: the seller's square.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={api.assetImage(a.id, 224)}
                      alt={a.original_filename}
                      draggable={false}
                      decoding="async"
                      className="absolute max-w-none"
                      style={cropStyle(a.cover_crop, a.width, a.height, 92)}
                    />
                  ) : (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={api.assetImage(a.id, 224)}
                      alt={a.original_filename}
                      draggable={false}
                      decoding="async"
                      className="relative h-full w-full object-cover"
                    />
                  )}
                </>
              ) : (
                <span className="flex h-full items-center justify-center p-1 text-center text-[10px] text-rose-700">
                  failed
                </span>
              )}
              {cover && (
                <span key="span-132-14" className="absolute left-1 top-1 rounded bg-brand-600 px-1.5 py-0.5 text-[10px] font-medium text-white">
                  Cover
                </span>
              )}
              <span
                className="absolute inset-x-0 bottom-0 hidden items-center justify-between bg-white/90 px-0.5 py-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100 [@media(hover:hover)_and_(pointer:fine)]:flex"
                onClick={(e) => e.stopPropagation()}
              >
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
                  <button key="button-147-16"
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
      {/* The same three actions as buttons, for the image that was tapped. */}
      {order.length > 1 && (
        <div key="buttons" className="mt-1 flex flex-wrap items-center gap-2 text-xs">
          {picked && byId.get(picked) ? (
            <>
              <span className="text-slate-600">
                <span>Image </span>
                <span translate="no">{order.indexOf(picked) + 1}</span>
                <span> of </span>
                <span translate="no">{order.length}</span>
              </span>
              <button
                type="button"
                className="btn-secondary px-3 py-1.5 text-xs"
                onClick={() => save(nudge(order, picked, -1))}
                disabled={saving || order.indexOf(picked) === 0}
              >
                ◀ Earlier
              </button>
              <button
                type="button"
                className="btn-secondary px-3 py-1.5 text-xs"
                onClick={() => save(nudge(order, picked, 1))}
                disabled={saving || order.indexOf(picked) === order.length - 1}
              >
                Later ▶
              </button>
              <button
                type="button"
                className="btn-secondary px-3 py-1.5 text-xs"
                onClick={() => save(makeCover(order, picked))}
                disabled={saving || order.indexOf(picked) === 0 || !usable(picked)}
              >
                Make cover
              </button>
            </>
          ) : (
            <span className="text-slate-500">Tap an image to move it or make it the cover.</span>
          )}
        </div>
      )}
      {coverAsset && coverAsset.status === "processed" && coverAsset.width && coverAsset.height && (
        <p key="p-171-6" className="mt-1 text-xs">
          <button
            type="button"
            className="tap font-medium text-brand-700 underline hover:text-brand-800 max-sm:py-2"
            onClick={() => setCropping((v) => !v)}
            aria-expanded={cropping}
          >
            {cropping ? "Close the crop" : "Adjust cover crop"}
          </button>
          {coverAsset.cover_crop && !cropping && (
            <span key="span-181-10" className="ml-2 text-slate-500">cropped by you · this square is the listing&apos;s main photo on Etsy</span>
          )}
        </p>
      )}
      {cropping && coverAsset && (
        <CoverCropper
          key={coverAsset.id}
          asset={coverAsset}
          onCancel={() => setCropping(false)}
          onDone={async () => {
            setCropping(false);
            try {
              onSaved(await api.getBatch(batchId));
            } catch (e: any) {
              setError(e.message ?? String(e));
            }
          }}
        />
      )}
      <p className="mt-1 text-xs text-slate-400">
        {saving
          ? "Saving order…"
          : "The cover is the first photo on Etsy; drafts created from now on use this order. With a mouse you can also drag the images."}
      </p>
      {error && <p key="p-206-6" className="mt-1 text-xs text-rose-700">{error}</p>}
    </div>
  );
}
