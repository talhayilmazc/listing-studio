"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { MAX_ZOOM, type View, cropOf, cropStyle, pan, viewOf, zoomAround } from "@/lib/crop";
import type { Asset } from "@/lib/types";

const FRAME = 288; // the square frame, in screen pixels
const PREVIEW = 96; // the live preview beside it
const SOFT_BELOW = 1000; // image pixels: a smaller square is upscaled for Etsy

/**
 * Pan and zoom the cover inside a square: the manual answer to lifestyle
 * mockups the automatic square crops badly. Drag to move, zoom with the slider
 * or the scroll wheel. The square saved here is what Etsy gets as the listing's
 * first photo; Etsy's API takes no crop, so it cannot be a display-only setting.
 */
export function CoverCropper({
  asset,
  onDone,
  onCancel,
}: {
  asset: Asset;
  onDone: () => void;
  onCancel: () => void;
}) {
  const w = asset.width ?? 1;
  const h = asset.height ?? 1;
  const [view, setView] = useState<View>(() => viewOf(asset.cover_crop ?? null, w, h, FRAME));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const src = api.assetImage(asset.id, 896);

  // Scroll to zoom. A passive React wheel handler cannot stop the page scrolling.
  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      setView((v) => zoomAround(v, v.zoom * Math.exp(-e.deltaY / 500), e.clientX - r.left, e.clientY - r.top, w, h, FRAME));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [w, h]);

  const crop = cropOf(view, w, h, FRAME);
  const s = FRAME / crop.size;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const r = (n: number) => Math.round(n * 10) / 10;
      await api.setCoverCrop(asset.id, { x: r(crop.x), y: r(crop.y), size: r(crop.size) });
      onDone();
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      await api.resetCoverCrop(asset.id);
      onDone();
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
      <div className="flex flex-wrap items-start gap-4">
        <div
          ref={frame}
          className="relative shrink-0 cursor-grab touch-none overflow-hidden rounded-md bg-slate-200 active:cursor-grabbing"
          style={{ width: FRAME, height: FRAME }}
          onPointerDown={(e) => {
            (e.target as Element).setPointerCapture?.(e.pointerId);
            drag.current = { x: e.clientX, y: e.clientY };
          }}
          onPointerMove={(e) => {
            if (!drag.current) return;
            const dx = e.clientX - drag.current.x;
            const dy = e.clientY - drag.current.y;
            drag.current = { x: e.clientX, y: e.clientY };
            setView((v) => pan(v, dx, dy, w, h, FRAME));
          }}
          onPointerUp={() => (drag.current = null)}
          onPointerCancel={() => (drag.current = null)}
          aria-label="Drag to move the photo inside the square"
          role="img"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt=""
            draggable={false}
            className="pointer-events-none absolute max-w-none select-none"
            style={{ width: w * s, height: h * s, left: view.ox, top: view.oy }}
          />
        </div>

        <div className="min-w-[12rem] flex-1 space-y-3 text-xs text-slate-600">
          <div className="flex items-center gap-3">
            <div className="relative shrink-0 overflow-hidden rounded bg-slate-200 ring-1 ring-slate-300" style={{ width: PREVIEW, height: PREVIEW }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={src}
                alt="Preview of the listing's main photo"
                draggable={false}
                className="absolute max-w-none"
                style={cropStyle(crop, w, h, PREVIEW)}
              />
            </div>
            <p>Live preview of the listing&apos;s main photo.</p>
          </div>

          <label className="block">
            Zoom
            <input
              type="range"
              min={1}
              max={MAX_ZOOM}
              step={0.01}
              value={view.zoom}
              onChange={(e) => setView((v) => zoomAround(v, Number(e.target.value), FRAME / 2, FRAME / 2, w, h, FRAME))}
              className="mt-1 block w-full"
            />
          </label>

          <p className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-amber-900">
            This changes the listing&apos;s main photo on Etsy, not just the thumbnail here. Etsy&apos;s
            API has no crop setting, so this square is uploaded as the first photo in place of the
            full image. It applies to drafts created from now on and to Replace images.
          </p>
          {crop.size < SOFT_BELOW && (
            <p className="text-amber-800">
              Zoomed in to {Math.round(crop.size)} px of your image: Etsy will show a softer photo.
            </p>
          )}

          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary px-2.5 py-1 text-xs" onClick={save} disabled={busy}>
              {busy ? "Saving…" : "Save crop"}
            </button>
            <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={reset} disabled={busy} title="Remove the crop: back to the automatic square">
              Reset to automatic
            </button>
            <button type="button" className="text-slate-500 underline" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
          </div>
          {error && <p className="text-rose-700">{error}</p>}
        </div>
      </div>
    </div>
  );
}
