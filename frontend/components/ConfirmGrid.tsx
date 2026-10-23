"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { designQuad, toPx, type Quad } from "@/lib/areaEditor";
import { CONFIRM_ALL_HINT, confirmAllLabel } from "@/lib/mockups";
import type { MockupPhoto } from "@/lib/types";

/**
 * "Confirm all": the photos still waiting for a check, each small with its print area(s) and a
 * design placeholder placed as a mockup places a design (aspect kept, top-aligned, centred:
 * lib/areaEditor.ts ``designQuad``), so the seller can scan them in one look, open any one in
 * the editor to fix it, and confirm the rest in one step. Nothing is drawn by the server.
 */
export function ConfirmGrid({
  photos,
  onConfirm,
  onFix,
  onClose,
}: {
  photos: MockupPhoto[];
  onConfirm: (ids: string[]) => Promise<void>;
  onFix: (photo: MockupPhoto) => void;
  onClose: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    root.current?.focus();
  }, []);

  async function confirmAll() {
    setSaving(true);
    setError(null);
    try {
      await onConfirm(photos.map((p) => p.id));
    } catch (e: any) {
      setError(String(e.message ?? e));
      setSaving(false);
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-slate-900/60 lg:items-center lg:p-4" role="dialog"
      aria-modal="true" aria-label="Confirm all photos" data-testid="confirm-grid">
      <div ref={root} tabIndex={-1} onKeyDown={(e) => e.key === "Escape" && onClose()}
        className="flex h-full w-full flex-col overflow-hidden bg-white outline-none lg:h-auto lg:max-h-[calc(100vh-2rem)] lg:w-[min(72rem,calc(100vw-2rem))] lg:rounded-xl lg:shadow-2xl">
        <header className="flex min-w-0 items-center gap-2 border-b border-slate-200 px-3 py-2 sm:px-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-slate-900">Look over the print areas</h2>
            <p className="text-xs text-slate-500">{CONFIRM_ALL_HINT}</p>
          </div>
          <button type="button" className="btn-secondary min-h-11 px-4" onClick={onClose} data-testid="confirm-grid-close">Close</button>
        </header>
        <ul className="grid min-h-0 flex-1 grid-cols-2 gap-3 overflow-y-auto p-3 sm:grid-cols-3 sm:p-4 lg:grid-cols-4" data-testid="confirm-grid-list">
          {photos.map((p) => <Tile key={p.id} photo={p} onFix={() => onFix(p)} />)}
        </ul>
        {error && <p key="error" role="alert" className="px-4 pb-2 text-sm text-rose-700">{error}</p>}
        <footer className="flex flex-wrap items-center gap-2 border-t border-slate-200 bg-white px-3 py-2 sm:px-4">
          <span className="hidden min-w-0 flex-1 text-xs text-slate-500 sm:block" translate="no">
            {`${photos.length} ${photos.length === 1 ? "photo" : "photos"}`}
          </span>
          <button type="button" className="btn-primary min-h-12 flex-1 text-base sm:flex-none sm:px-10" onClick={confirmAll}
            disabled={saving || !photos.length} data-testid="confirm-grid-confirm">
            <span translate="no">{saving ? "Saving…" : confirmAllLabel(photos.length)}</span>
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

function Tile({ photo, onFix }: { photo: MockupPhoto; onFix: () => void }) {
  const W = photo.width ?? 1;
  const H = photo.height ?? 1;
  const quads: Quad[] = photo.areas.map((a) => toPx(a.print_area, W, H));
  return (
    <li className="min-w-0 overflow-hidden rounded-md border border-slate-200" data-testid="confirm-grid-tile">
      {/* The photo is an <img> so it loads only when scrolled to; the areas are drawn over it,
          both fitted the same way ("contain"), so they line up. */}
      <div className="relative aspect-square w-full bg-slate-100">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={api.mockupPhotoImageUrl(photo.id, "thumb")} alt="" loading="lazy" decoding="async"
        className="absolute inset-0 h-full w-full object-contain" />
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="absolute inset-0 block h-full w-full" role="img"
        aria-label={`${photo.name} with its print area`}>
        {quads.map((q, i) => {
          const d = designQuad(q, 1000, 1000, photo.size);
          const cx = (d[0][0] + d[2][0]) / 2;
          const cy = (d[0][1] + d[2][1]) / 2;
          return (
            <g key={i}>
              <polygon points={q.map((p) => p.join(",")).join(" ")} fill="none" stroke="#2563eb" strokeWidth={2} vectorEffect="non-scaling-stroke" />
              <polygon points={d.map((p) => p.join(",")).join(" ")} fill="rgba(37,99,235,0.35)" stroke="#1d4ed8" strokeDasharray="6 4"
                strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
              <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize={Math.max(12, (d[1][0] - d[0][0]) / 6)}
                fontWeight={700}>DESIGN</text>
            </g>
          );
        })}
      </svg>
      </div>
      <div className="flex min-w-0 items-center gap-1 px-2 py-1">
        <span className="min-w-0 flex-1 truncate text-xs text-slate-700" title={photo.name}>{photo.name}</span>
        <button type="button" className="tap shrink-0 text-xs font-medium text-brand-700 underline" onClick={onFix} data-testid="confirm-grid-fix">
          Fix
        </button>
      </div>
    </li>
  );
}
