"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { MockupEdits, MockupOutput } from "@/lib/types";
import { matrix3d, type Quad } from "@/lib/areaEditor";
import { fileFor, full, photoPoints, printPixels, trimOn, type Region } from "@/lib/designEdits";
import type { TileState } from "@/lib/mockupTiles";

/**
 * One mockup in the results (and in the design editor's strip). While it is being made again it
 * shows the seller's change at once, drawn in the browser with the editor's own arithmetic
 * (lib/designEdits.ts), marked "Updating…"; when the new file is in, its versioned image takes
 * over (a new stamp is a new URL, so an earlier file is never shown). One that could not be made
 * again says so, with "Try again". Nothing here calls Etsy or an AI model.
 */
export function MockupTile({ runId, designId, output, state, edits, label, onOpen, onRetry, disabled, small }: {
  runId: string;
  designId: string;
  output: MockupOutput;
  state: TileState;
  /** The run's edits (adjustments, recolours, photos, files): what the preview draws. */
  edits: MockupEdits | null;
  label: string;
  onOpen?: () => void;
  onRetry?: () => void;
  disabled?: boolean;
  small?: boolean;
}) {
  const preview = state.kind === "updating" && edits && output.photo_id ? (
    <TilePreview edits={edits} designId={designId} photoId={output.photo_id} />
  ) : null;
  const image = (
    <span className="relative block aspect-square w-full overflow-hidden rounded bg-slate-100">
      {preview ?? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={api.mockupOutputImageUrl(runId, output.id, "small", output.stamp ?? "")} alt="" loading="lazy"
          className="absolute inset-0 h-full w-full object-cover" data-testid="mockup-tile-image" />
      )}
      {state.kind === "updating" && (
        <span key="updating" className="absolute bottom-1 left-1 rounded bg-white/90 px-1.5 py-0.5 text-[11px] font-medium text-slate-700 shadow-sm"
          translate="no" data-testid="mockup-tile-updating">Updating…</span>
      )}
    </span>
  );
  return (
    <div className={`${small ? "w-20" : "w-28"} shrink-0`} data-testid="mockup-tile" data-state={state.kind} data-stamp={output.stamp ?? ""}
      data-pair={output.photo_id ? `${designId}:${output.photo_id}` : undefined}>
      {onOpen ? (
        <button type="button" className="group block w-full rounded-md border border-slate-200 p-1 text-left disabled:opacity-60"
          disabled={disabled} onClick={onOpen} aria-label={`Adjust ${label}`} data-testid="mockup-adjust">
          {image}
          <span className="mt-1 block truncate text-xs text-slate-600" translate="no">{output.photo_name ?? output.template_name}</span>
          <span className="block text-xs font-medium text-brand-700">Adjust</span>
        </button>
      ) : (
        <div className="rounded-md border border-slate-200 p-1">
          {image}
          <span className="mt-1 block truncate text-[11px] text-slate-600" translate="no" title={label}>{output.photo_name ?? output.template_name}</span>
        </div>
      )}
      {state.kind === "failed" && (
        <div key="failed" className="mt-1 space-y-1" role="alert" data-testid="mockup-tile-failed">
          <p className="text-[11px] leading-snug text-rose-700" title={state.reason}>Could not update this mockup.</p>
          {onRetry && (
            <button key="retry" type="button" className="btn-secondary min-h-11 w-full px-2 text-xs" onClick={onRetry} data-testid="mockup-tile-retry">
              Try again
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Design files as loaded once per page, at most this many pixels on the longest side. */
const PREVIEW_SIDE = 360;
const loaded = new Map<string, Promise<ImageData | null>>();

function designPixels(url: string): Promise<ImageData | null> {
  let found = loaded.get(url);
  if (!found) {
    found = new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const s = Math.min(1, PREVIEW_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(img.naturalWidth * s));
        c.height = Math.max(1, Math.round(img.naturalHeight * s));
        const ctx = c.getContext("2d", { willReadFrequently: true });
        if (!ctx) return resolve(null);
        ctx.drawImage(img, 0, 0, c.width, c.height);
        resolve(ctx.getImageData(0, 0, c.width, c.height));
      };
      img.onerror = () => resolve(null);
      img.src = url;
    });
    loaded.set(url, found);
  }
  return found;
}

/** The change drawn in the browser: the blank photo, and on each print area the design as the
 * editor places it (its adjustment, look and recolouring), cropped to the square tile. */
function TilePreview({ edits, designId, photoId }: { edits: MockupEdits; designId: string; photoId: string }) {
  const photo = edits.photos.find((p) => p.id === photoId);
  const design = edits.designs.find((d) => d.id === designId);
  const box = useRef<HTMLSpanElement>(null);
  const [side, setSide] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const fit = () => setSide(el.getBoundingClientRect().width);
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  if (!photo || !design) return null;
  const s = side ? Math.max(side / photo.width, side / photo.height) : 0;
  const off: [number, number] = [(side - photo.width * s) / 2, (side - photo.height * s) / 2];
  return (
    <span ref={box} className="absolute inset-0" data-testid="mockup-tile-preview">
      {s > 0 && (
        <span key="photo" className="absolute" style={{ left: off[0], top: off[1], width: photo.width * s, height: photo.height * s }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={api.mockupPhotoImageUrl(photo.id, "thumb")} alt="" loading="lazy" decoding="async" className="absolute inset-0 h-full w-full" />
          {photo.areas.map((area, k) => (
            <AreaPrint key={k} edits={edits} design={design} photo={photo} area={k} scale={s} />
          ))}
        </span>
      )}
    </span>
  );
}

function AreaPrint({ edits, design, photo, area, scale }: {
  edits: MockupEdits;
  design: MockupEdits["designs"][number];
  photo: MockupEdits["photos"][number];
  area: number;
  scale: number;
}) {
  const a = photo.areas[area];
  const file = fileFor(design.files, a.subgroup);
  const adjust = full((edits.adjustments.find((x) => x.design_id === design.id && x.photo_id === photo.id && x.area === area)?.data ?? null) as never);
  const regions = (edits.recolours.find((r) => r.design_id === design.id && r.subgroup === (a.subgroup ?? ""))?.regions
    ?? edits.recolours.find((r) => r.design_id === design.id && r.subgroup === "")?.regions ?? []) as Region[];
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState<[number, number] | null>(null);
  useEffect(() => {
    let gone = false;
    designPixels(api.mockupDesignFileUrl(design.id, file.version_id, file.stamp)).then((src) => {
      const out = canvas.current;
      if (gone || !src || !out) return;
      const printed = printPixels(src, trimOn(file, src.width, src.height), regions, adjust);
      out.width = printed.width;
      out.height = printed.height;
      out.getContext("2d")?.putImageData(new ImageData(printed.data, printed.width, printed.height), 0, 0);
      setSize([printed.width, printed.height]);
    });
    return () => {
      gone = true;
    };
  }, [design.id, file.version_id, JSON.stringify(regions), adjust.opacity, adjust.brightness, adjust.contrast]); // eslint-disable-line react-hooks/exhaustive-deps
  const quad = a.print_area.map(([x, y]) => [x * photo.width, y * photo.height]) as Quad;
  const points = size ? photoPoints(quad, size[0], size[1], photo.size, adjust) : null;
  const screen = points ? (points.map(([x, y]) => [x * scale, y * scale]) as Quad) : null;
  return (
    <canvas ref={canvas} aria-hidden className="pointer-events-none absolute left-0 top-0 max-w-none"
      style={size && screen ? { width: size[0], height: size[1], transformOrigin: "0 0", transform: matrix3d(size[0], size[1], screen) } : { display: "none" }} />
  );
}
