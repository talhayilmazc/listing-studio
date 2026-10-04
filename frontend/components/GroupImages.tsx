"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { makeCover, moveBefore, nudge, sameOrder } from "@/lib/order";
import { cropStyle } from "@/lib/crop";
import { CoverCropper } from "./CoverCropper";
import type { Asset, BatchDetail, ImageDeleteResult } from "@/lib/types";

import { Txt } from "@/components/Txt";
/**
 * The group's image files were deleted (they are kept for a limited time after
 * a listing is published, because the server's disk is small). The kept cover
 * is shown; there is nothing left to reorder, crop or delete.
 */
function RemovedImages({ assets, listingsOnEtsy }: { assets: Asset[]; listingsOnEtsy: number }) {
  const kept = assets.find((a) => a.has_thumbnail);
  return (
    <div className="flex items-start gap-3 rounded-lg border border-slate-200 bg-stone-50 p-3">
      {kept ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img key="kept" src={api.assetImage(kept.id, 224)} alt={kept.original_filename} className="h-16 w-16 shrink-0 rounded object-cover" loading="lazy" decoding="async" />
      ) : (
        <span key="none" className="flex h-16 w-16 shrink-0 items-center justify-center rounded bg-slate-200 text-[10px] text-slate-500">removed</span>
      )}
      <p className="min-w-0 text-xs text-slate-600">
        <span className="font-medium text-slate-900">Image files removed. </span>
        <span>
          This listing&apos;s <span translate="no">{assets.length}</span> image files were deleted to free space: they are kept for a
          limited time after a listing is published. Its text and its record here are unchanged
        </span>
        {listingsOnEtsy > 0 ? <span key="etsy">, and the listing on Etsy is not affected</span> : null}
        <span>. To create another draft or change the images, upload the design again.</span>
      </p>
    </div>
  );
}

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
  listingsOnEtsy = 0,
  hasContent = false,
  onDeleted,
}: {
  batchId: string;
  groupKey: string;
  assets: Asset[];
  onSaved: (batch: BatchDetail) => void;
  /** Drafts or live listings already made from this group. */
  listingsOnEtsy?: number;
  /** A title, tags and description are written for this group. */
  hasContent?: boolean;
  /** An image was deleted (the page says so when the group went with it). */
  onDeleted?: (result: ImageDeleteResult) => void;
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
  // The image waiting for the seller to confirm deleting it.
  const [deleting, setDeleting] = useState<string | null>(null);

  async function remove(id: string) {
    setSaving(true);
    setError(null);
    try {
      const result = await api.deleteImage(id);
      setDeleting(null);
      setPicked(null);
      setCropping(false);
      onSaved(result.batch);
      onDeleted?.(result);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setSaving(false);
    }
  }
  const incomingKey = incoming.join(",");
  useEffect(() => {
    setOrder(incomingKey ? incomingKey.split(",") : []);
  }, [incomingKey]);

  const byId = new Map(assets.map((a) => [a.id, a]));
  const coverAsset = byId.get(order[0]);
  if (assets.some((a) => a.files_removed)) return <RemovedImages assets={assets} listingsOnEtsy={listingsOnEtsy} />;
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
              <button
                type="button"
                className="absolute right-1 top-1 hidden h-6 w-6 items-center justify-center rounded bg-white/90 text-slate-600 opacity-0 shadow-sm transition-opacity hover:text-rose-700 focus:opacity-100 group-hover:opacity-100 [@media(hover:hover)_and_(pointer:fine)]:flex"
                onClick={(e) => {
                  e.stopPropagation();
                  setPicked(id);
                  setDeleting(id);
                }}
                disabled={saving}
                aria-label={`Delete ${a.original_filename}`}
                title="Delete this image"
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                  <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
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
      {order.length === 1 && byId.get(order[0]) && !deleting && (
        <div key="only" className="mt-1 text-xs">
          <button
            type="button"
            className="btn-secondary border-rose-200 px-3 py-1.5 text-xs text-rose-700 hover:border-rose-300 hover:bg-rose-50"
            onClick={() => setDeleting(order[0])}
            disabled={saving}
          >
            Delete image
          </button>
        </div>
      )}
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
              <button
                type="button"
                className="btn-secondary border-rose-200 px-3 py-1.5 text-xs text-rose-700 hover:border-rose-300 hover:bg-rose-50"
                onClick={() => setDeleting(picked)}
                disabled={saving}
              >
                Delete image
              </button>
            </>
          ) : (
            <span className="text-slate-500">Tap an image to move it or make it the cover.</span>
          )}
        </div>
      )}
      {deleting && byId.get(deleting) && (
        <div key="confirm" role="alertdialog" aria-label="Delete image" className="mt-2 space-y-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs text-rose-900">
          <p className="font-medium">
            <span>Delete </span>
            <span translate="no">{byId.get(deleting)!.original_filename}</span>
            <span>? It cannot be undone.</span>
          </p>
          {order.length === 1 ? (
            <p>
              <span>It is this group&apos;s last image, so the group is removed too</span>
              <span>{hasContent ? ", with the title, tags and description written for it." : "."}</span>
            </p>
          ) : order[0] === deleting ? (
            <p>
              It is the cover. The next image becomes the cover, and the cover crop you saved for this one is dropped.
            </p>
          ) : null}
          {listingsOnEtsy > 0 && (
            <p key="etsy" className="rounded border border-rose-200 bg-white px-2 py-1.5 text-slate-700">
              <strong>This does not remove the photo from Etsy.</strong>
              <span>
                <span>{" "}<span>This group already has </span><span>{listingsOnEtsy === 1 ? "a listing" : `${listingsOnEtsy} listings`}</span><span> on Etsy, which
                keep</span><Txt>{listingsOnEtsy === 1 ? "s" : ""}</Txt><span> the photo. Deleting here only affects drafts created from now on and
                &ldquo;Replace images on Etsy&rdquo;.</span></span>
              </span>
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50 max-sm:min-h-[2.75rem] max-sm:px-4 max-sm:text-sm"
              onClick={() => remove(deleting)}
              disabled={saving}
            >
              {saving ? "Deleting…" : order.length === 1 ? "Delete image and group" : "Delete image"}
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setDeleting(null)} disabled={saving}>
              Keep it
            </button>
          </div>
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
