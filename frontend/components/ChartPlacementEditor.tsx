"use client";

import { useEffect, useState } from "react";
import {
  FEWER_PHOTOS_RULE,
  MAX_IMAGES,
  NOT_ON_ETSY_NOTE,
  POSITION_LABELS,
  chartKey,
  chartsCrowdNotice,
  editorStrip,
  isChart,
  moveChartTo,
  nudgeChart,
  ordinal,
  placeLabel,
  placementFromStrip,
  samePlacement,
  type ChartPosition,
  type Placement,
} from "@/lib/chartOrder";

/**
 * Where a profile's size charts go on its listings: a row of the listing's
 * places in order (the cover, then mockup placeholders up to Etsy's limit) with
 * one real tile per chart. Each chart is put in its own place by dragging it, or
 * by selecting it and using Earlier / Later or tapping a place (so it works
 * with a keyboard and on a phone too). The cover's place is never a chart's.
 *
 * It is the default for every listing made with the profile; a listing group
 * can still move its charts on its batch. Nothing here changes a draft that is
 * already on Etsy.
 */
export function ChartPlacementEditor({
  placement,
  chartIds,
  custom,
  position,
  images,
  total = MAX_IMAGES,
  busy,
  onPlacement,
  onPreset,
}: {
  /** The charts in the order they appear, each with its slot (what drafts use now). */
  placement: Placement[];
  /** The profile's charts in their own order: "Size chart 1" stays the same chart wherever it is moved. */
  chartIds: number[];
  /** Dragged into place; otherwise the preset. */
  custom: boolean;
  position: ChartPosition;
  /** Each chart's picture; null while its link is being renewed. */
  images: Map<number, string | null>;
  total?: number;
  busy: boolean;
  onPlacement: (next: Placement[]) => void;
  onPreset: (position: ChartPosition) => void;
}) {
  const incoming = placement.map((p) => `${p.listing_image_id}:${p.slot}`).join(",");
  const [strip, setStrip] = useState<string[]>(() => editorStrip(placement, total));
  const [picked, setPicked] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<number | null>(null);
  useEffect(() => {
    setStrip(editorStrip(placement, total)); // what the server holds, after a save or a preset
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incoming, total]);

  const numbered = new Map(chartIds.map((id, i) => [chartKey(id), i + 1]));
  const several = placement.length > 1;
  const name = (chart: string) => (several ? `Size chart ${numbered.get(chart) ?? ""}` : "Size chart");

  function apply(next: string[]) {
    const saved = placementFromStrip(next);
    setStrip(next); // shown at once; the server's answer redraws it
    if (!samePlacement(saved, placementFromStrip(strip))) onPlacement(saved);
  }

  const pickedAt = picked ? strip.indexOf(picked) : -1;
  // Charts that alone leave fewer than two places for photos.
  const crowded = chartsCrowdNotice(chartIds.length, total);

  return (
    <div className="space-y-2" data-testid="chart-placement">
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
        <span>Size chart position</span>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Preset positions">
          {(Object.keys(POSITION_LABELS) as ChartPosition[]).map((p) => (
            <button
              key={p}
              type="button"
              className={
                "rounded-md border px-2.5 py-1 text-xs max-sm:min-h-[2.75rem] max-sm:px-3 " +
                (!custom && position === p ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 bg-white text-slate-700 hover:border-slate-400")
              }
              aria-pressed={!custom && position === p}
              disabled={busy}
              onClick={() => {
                setPicked(null);
                onPreset(p);
              }}
              data-testid={`chart-preset-${p}`}
            >
              {POSITION_LABELS[p]}
            </button>
          ))}
        </div>
        {custom && (
          <span key="custom" className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
            Your own positions
          </span>
        )}
      </div>

      {crowded && (
        <p key="crowded" role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="chart-crowded">
          {crowded}
        </p>
      )}
      <ol className="flex flex-wrap gap-1.5" aria-label="The listing's images, in order">
        {strip.map((tile, i) => {
          const place = i + 1;
          const target = dragging !== null && over === i && i > 0;
          if (isChart(tile)) {
            const url = images.get(Number(tile.slice(6))) ?? null;
            const on = picked === tile;
            return (
              <li key={tile}>
                <button
                  type="button"
                  draggable={!busy}
                  onDragStart={(e) => {
                    setDragging(tile);
                    e.dataTransfer.effectAllowed = "move";
                    e.dataTransfer.setData("text/plain", tile); // Firefox starts no drag without data
                  }}
                  onDragEnd={() => {
                    setDragging(null);
                    setOver(null);
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    if (over !== i) setOver(i);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragging) apply(moveChartTo(strip, dragging, i));
                    setDragging(null);
                    setOver(null);
                  }}
                  onClick={() => setPicked(on ? null : tile)}
                  onKeyDown={(e) => {
                    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
                    e.preventDefault();
                    setPicked(tile);
                    apply(nudgeChart(strip, tile, e.key === "ArrowLeft" ? -1 : 1));
                  }}
                  disabled={busy}
                  aria-pressed={on}
                  aria-label={`${name(tile)}: image ${place} of ${strip.length}. Select to move it; arrow keys move it too.`}
                  title={`${name(tile)}: drag it, or select it and use Earlier / Later`}
                  data-testid="chart-slot-tile"
                  className={
                    "relative block h-14 w-14 cursor-grab overflow-hidden rounded border-2 border-dashed bg-amber-50 active:cursor-grabbing " +
                    (on ? "border-slate-900 ring-2 ring-slate-900/30 " : "border-amber-400 ") +
                    (target ? "ring-2 ring-brand-500/50 " : "") +
                    (dragging === tile ? "opacity-40" : "")
                  }
                >
                  {url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img key="img" src={url} alt="" draggable={false} loading="lazy" decoding="async" className="h-full w-full object-cover" />
                  ) : (
                    <span key="none" className="flex h-full items-center justify-center px-0.5 text-center text-[9px] leading-tight text-amber-800">
                      size chart
                    </span>
                  )}
                  <span className="absolute left-0 top-0 rounded-br bg-amber-500 px-1 text-[10px] font-semibold leading-4 text-white" translate="no">
                    {place}
                  </span>
                  {several && (
                    <span key="which" className="absolute inset-x-0 bottom-0 bg-amber-500/90 text-center text-[9px] font-medium leading-3 text-white">
                      <span>chart </span>
                      <span translate="no">{numbered.get(tile)}</span>
                    </span>
                  )}
                </button>
              </li>
            );
          }
          const cover = i === 0;
          // A place: a drop target, and with a chart selected a button that takes it.
          const takes = !cover && picked !== null && !busy;
          return (
            <li key={tile}>
              <button
                type="button"
                tabIndex={takes ? 0 : -1}
                aria-disabled={!takes}
                onClick={() => {
                  if (takes && picked) apply(moveChartTo(strip, picked, i));
                }}
                onDragOver={(e) => {
                  if (cover) return; // the cover is always a design mockup
                  e.preventDefault();
                  if (over !== i) setOver(i);
                }}
                onDrop={(e) => {
                  if (cover) return;
                  e.preventDefault();
                  if (dragging) apply(moveChartTo(strip, dragging, i));
                  setDragging(null);
                  setOver(null);
                }}
                aria-label={
                  cover
                    ? "Image 1: the cover, always a design mockup"
                    : takes
                      ? `Move the selected size chart to image ${place}`
                      : `Image ${place}: a mockup`
                }
                className={
                  "flex h-14 w-14 flex-col items-center justify-center rounded border text-[10px] leading-tight " +
                  (cover
                    ? "cursor-default border-brand-500 bg-brand-50 font-medium text-brand-800 "
                    : takes
                      ? "cursor-pointer border-slate-300 bg-white text-slate-600 hover:border-slate-500 "
                      : "cursor-default border-slate-200 bg-slate-50 text-slate-400 ") +
                  (target ? "ring-2 ring-brand-500/50" : "")
                }
              >
                <span className="text-xs font-semibold" translate="no">{place}</span>
                <span>{cover ? "Cover" : "Mockup"}</span>
              </button>
            </li>
          );
        })}
      </ol>

      <div className="flex min-h-[2.75rem] flex-wrap items-center gap-2 text-xs" translate="no">
        {picked && pickedAt > 0 ? (
          <>
            <span className="text-slate-600">
              <span>{name(picked)}</span>
              <span>{`: image ${pickedAt + 1} of ${strip.length}`}</span>
            </span>
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-xs"
              onClick={() => apply(nudgeChart(strip, picked, -1))}
              disabled={busy || pickedAt <= 1}
              data-testid="chart-earlier"
            >
              ◀ Earlier
            </button>
            <button
              type="button"
              className="btn-secondary px-3 py-1.5 text-xs"
              onClick={() => apply(nudgeChart(strip, picked, 1))}
              disabled={busy || pickedAt >= strip.length - 1}
              data-testid="chart-later"
            >
              Later ▶
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setPicked(null)}>
              Done
            </button>
          </>
        ) : (
          <span className="text-slate-500">
            <span>Drag a size chart to its place, or select it and use Earlier / Later. </span>
            <span>
              {chartIds
                .map((id) => {
                  const tile = chartKey(id);
                  const where = placeLabel(strip, tile);
                  return `${name(tile)}: ${where === "last" ? "last" : `${where} image`}`;
                })
                .join(" · ")}
            </span>
          </span>
        )}
      </div>

      <p className="text-xs text-slate-500">
        <span>{`Any place from the 2nd to the ${ordinal(strip.length)}: the cover is always a design mockup. `}</span>
        <span>{FEWER_PHOTOS_RULE}</span>
      </p>
      <p className="text-xs text-slate-500">
        <span>{NOT_ON_ETSY_NOTE}</span>
        <span> A listing can still have its charts moved on its batch.</span>
      </p>
    </div>
  );
}
