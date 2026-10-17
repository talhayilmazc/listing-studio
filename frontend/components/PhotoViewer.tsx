"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { LAYOUT_LABELS, NEEDS_CHECK, type PhotoLayout, ROLE_LABELS, swipeStep, viewerCounter, viewerStep } from "@/lib/mockups";
import type { MockupPhoto, MockupSubgroup } from "@/lib/types";

/**
 * A template's photos, large, one at a time (templates page): ← and → buttons, the keyboard's
 * arrow keys and a sideways swipe on a phone move through them; Escape closes. "Check" (or
 * "Edit") opens the print-area editor on the photo shown. Only the seller's own photos, from
 * our server; nothing is saved here.
 */
export function PhotoViewer({
  photos,
  start,
  subgroups,
  onCheck,
  onClose,
}: {
  photos: MockupPhoto[];
  start: number;
  subgroups?: MockupSubgroup[];
  onCheck: (photo: MockupPhoto) => void;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(Math.min(Math.max(start, 0), Math.max(photos.length - 1, 0)));
  const touch = useRef<{ x: number; y: number } | null>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const photo = photos[index];
  const go = (by: -1 | 1) => setIndex((i) => viewerStep(i, photos.length, by));

  useEffect(() => {
    closeButton.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "Escape") onClose();
      else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [photos.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!photo || typeof document === "undefined") return null;
  const aspect = photo.width && photo.height ? `${photo.width} / ${photo.height}` : "6 / 7";
  const group = (subgroups ?? []).length > 1 ? (subgroups ?? []).find((g) => g.id === photo.subgroup_id) : undefined;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-slate-900/80 sm:items-center sm:p-4" role="dialog"
      aria-modal="true" aria-label={`Photo ${viewerCounter(index, photos.length)}: ${photo.name}`} data-testid="photo-viewer"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="flex h-full w-full max-w-5xl flex-col bg-white sm:h-auto sm:max-h-full sm:rounded-lg">
        <div className="flex min-w-0 items-center gap-2 border-b border-slate-200 p-2">
          <p className="min-w-0 flex-1 truncate px-2 text-sm font-medium text-slate-900" title={photo.name} translate="no">{photo.name}</p>
          <span className="shrink-0 text-xs tabular-nums text-slate-500" translate="no" data-testid="viewer-counter">
            {viewerCounter(index, photos.length)}
          </span>
          <button ref={closeButton} type="button" className="btn-secondary min-h-11 min-w-11 px-3" onClick={onClose} data-testid="viewer-close">
            Close
          </button>
        </div>
        <div className="relative flex min-h-0 flex-1 items-center justify-center bg-slate-100 p-2"
          onTouchStart={(e) => { const t = e.touches[0]; touch.current = { x: t.clientX, y: t.clientY }; }}
          onTouchEnd={(e) => {
            const from = touch.current;
            touch.current = null;
            const t = e.changedTouches[0];
            if (!from || !t) return;
            const by = swipeStep(t.clientX - from.x, t.clientY - from.y);
            if (by) go(by);
          }}>
          <div className="relative inline-block max-w-full">
            {photo.status === "ready" ? (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img key={photo.id} src={api.mockupPhotoImageUrl(photo.id, 896)} alt={photo.name} width={photo.width ?? undefined}
                  height={photo.height ?? undefined} className="block h-auto max-h-[70vh] w-auto max-w-full" />
                <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full">
                  {photo.areas.map((a, i) => (
                    <polygon key={i} points={a.print_area.map(([x, y]) => `${x},${y}`).join(" ")}
                      className={photo.needs_check ? "fill-amber-400/20 stroke-amber-600" : "fill-brand-500/15 stroke-brand-600"}
                      strokeWidth={2} vectorEffect="non-scaling-stroke" />
                  ))}
                </svg>
              </>
            ) : (
              <p key="waiting" className="flex min-h-[12rem] w-64 max-w-full items-center justify-center p-4 text-center text-sm text-slate-500" role="status"
                style={{ aspectRatio: aspect }}>
                {photo.status === "failed" ? photo.error ?? "This photo could not be prepared." : "Preparing…"}
              </p>
            )}
          </div>
          <button type="button" className="btn-secondary absolute left-2 top-1/2 min-h-11 min-w-11 -translate-y-1/2 px-3 text-lg"
            onClick={() => go(-1)} disabled={index === 0} aria-label="Previous photo" data-testid="viewer-prev">
            ←
          </button>
          <button type="button" className="btn-secondary absolute right-2 top-1/2 min-h-11 min-w-11 -translate-y-1/2 px-3 text-lg"
            onClick={() => go(1)} disabled={index === photos.length - 1} aria-label="Next photo" data-testid="viewer-next">
            →
          </button>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2 border-t border-slate-200 p-2">
          <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${photo.needs_check ? "bg-amber-100 text-amber-900" : "bg-emerald-100 text-emerald-800"}`}
            translate="no">
            {photo.needs_check ? NEEDS_CHECK : photo.confirmed ? "Confirmed" : photo.status === "ready" ? "Not confirmed" : "Not ready"}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs text-slate-500" translate="no">
            {[
              LAYOUT_LABELS[(photo.layout as PhotoLayout) ?? "single"] ?? "",
              photo.areas.length > 1 ? photo.areas.map((a, i) => (a.role === "back" ? `${i + 1} ${ROLE_LABELS.back}` : `${i + 1}`)).join(" · ") : "",
              group ? group.name : "",
            ].filter(Boolean).join(" · ")}
          </span>
          {photo.status === "ready" && (
            <button key="check" type="button" className={`${photo.needs_check ? "btn-primary" : "btn-secondary"} min-h-11 px-4`}
              onClick={() => onCheck(photo)} data-testid="viewer-check">
              {photo.needs_check ? "Check" : "Edit"}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
